"""
FW 2.3 backend tests: the dashboard motor command, steering centre, live servo calibration
(and the vcu_calibration.h text the dashboard offers to copy), and the dashboard-lag fixes
(latest frame served from memory, recording off the event loop, concurrent broadcast).
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("VCU_DB_PATH", str(Path(__file__).resolve().parent / "test_stage2d.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import camera_relay, vcu_config  # noqa: E402
from app.db import DB_PATH, LATEST  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import telemetry  # noqa: E402
from app.routers.vehicle import calibration_header  # noqa: E402
from app.services import vehicle_commands  # noqa: E402
from tests.test_fw22 import _f  # noqa: E402
from tests.test_stage2d import _post, _session  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CAL_H = ROOT / "HARDWARE" / "esp32" / "main_updated_corrected__1_" / "vcu_calibration.h"


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    if DB_PATH.exists():
        DB_PATH.unlink()
    monkeypatch.setattr(camera_relay, "REC_DIR", tmp_path / "recordings")
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_fw23_keys_are_commands_or_calibration():
    assert {9, 23, 24, 25} <= vcu_config.COMMAND_KEYS
    assert vcu_config.CALIBRATION_KEYS == list(range(30, 46))
    assert vcu_config.validate(30, 400) is not None and vcu_config.validate(30, 1150) is None
    assert vcu_config.validate(41, 400) is not None and vcu_config.validate(44, 20) is not None
    assert vcu_config.validate(9, 1001) is not None and vcu_config.validate(9, 0) is None


def test_backend_defaults_match_vcu_calibration_h():
    text = CAL_H.read_text()
    d = {m.group(1): float(m.group(2)) for m in re.finditer(r"#define VCU_CAL_(\w+)\s+([\d.]+)", text)}
    D = vcu_config.DEFAULTS
    assert D["steerLeftUs"] == d["STEER_LEFT_US"] and D["steerCenterUs"] == d["STEER_CENTER_US"]
    assert D["steerRightUs"] == d["STEER_RIGHT_US"] and D["steerLockCdeg"] == d["STEER_LOCK_DEG"] * 100
    for code, name in (("FL", "Fl"), ("FR", "Fr"), ("RL", "Rl"), ("RR", "Rr")):
        assert D[f"flap{name}FlatUs"] == d[f"FLAP_{code}_FLAT_US"] and D[f"flap{name}MaxUs"] == d[f"FLAP_{code}_MAX_US"]
    assert D["flapNeutralCdeg"] == d["FLAP_NEUTRAL_DEG"] * 100 and D["flapSwingCdeg"] == d["FLAP_SWING_DEG"] * 100
    assert D["knobCdegPerClick"] == d["KNOB_DEG_PER_CLICK"] * 100 and D["knobReverse"] == d["KNOB_REVERSE"]


def test_calibration_header_round_trips_into_the_file_format():
    text = calibration_header(vcu_config.DEFAULTS)
    file_vals = {m.group(1): m.group(2) for m in re.finditer(r"#define (VCU_CAL_\w+)\s+([\d.]+)", CAL_H.read_text())}
    for name, val in re.findall(r"#define (VCU_CAL_\w+)\s+([\d.]+)", text):
        assert float(file_vals[name]) == float(val), name     # same names, same values as the shipped file


def test_motor_endpoint_queues_key_9_and_stop_is_urgent(client):
    assert client.post("/api/vehicle/motor", json={"dutyPct": 120}).status_code == 422
    m = client.post("/api/vehicle/motor", json={"dutyPct": 42.5}).json()
    assert m["key"] == 9 and m["value"] == 425 and m["status"] == "queued"
    client.post("/api/vehicle/config", json={"name": "flapNeutralCdeg", "value": 15})
    s = client.post("/api/vehicle/motor", json={"dutyPct": 0}).json()   # STOP supersedes the run request
    batch = vehicle_commands.next_batch()
    assert batch[0]["key"] == 9 and batch[0]["value"] == 0                 # ... and goes out first
    assert vehicle_commands.get(m["id"])["status"] == "superseded" and s["value"] == 0


def test_estop_cancels_a_motor_request_in_flight(client):
    m = client.post("/api/vehicle/motor", json={"dutyPct": 50}).json()
    client.post("/api/vehicle/estop", json={"action": "trigger"})
    assert vehicle_commands.get(m["id"])["status"] == "superseded"


def test_status_reports_motor_and_steering(client):
    sid = _session(client)
    fr = _f(1000, mode="track", hw="prototype", run="bench_motor", throttle=40.0, steer=-12.0, cfg={"motorCmd": 400})
    fr["frame"]["vcu"]["knob"] = 4
    _post(client, sid, [fr])
    st = client.get("/api/vehicle/status").json()
    assert st["motorRunning"] is True and st["motorDutyPct"] == 40.0 and st["motorCommandPct"] == 40.0
    assert st["canRunMotor"] is True and st["steeringDeg"] == -12.0 and st["knob"] == 4
    assert st["canStartRun"] is False                                       # circuit runs still need DYNO
    _post(client, sid, [_f(2000, mode="track", hw="prototype", state="FAULT")])
    st = client.get("/api/vehicle/status").json()
    assert st["canRunMotor"] is False and any("IDLE" in r for r in st["motorBlockedBy"])


def test_steering_center_and_calibration_actions(client):
    c = client.post("/api/vehicle/steering/center").json()
    assert c["key"] == 23 and c["value"] == 1
    assert client.post("/api/vehicle/calibration", json={"action": "bogus"}).status_code == 422
    assert client.post("/api/vehicle/calibration", json={"action": "save"}).json()["value"] == 1
    assert client.post("/api/vehicle/calibration", json={"action": "revert"}).json()["value"] == 2
    # a calibration value goes through the normal config path, in microseconds
    w = client.post("/api/vehicle/config", json={"name": "steerLeftUs", "value": 1120})
    assert w.status_code == 200 and w.json()["key"] == 30 and w.json()["value"] == 1120
    assert client.post("/api/vehicle/config", json={"name": "steerLeftUs", "value": 3000}).status_code == 422
    # degrees are sent in centi-degrees
    assert client.post("/api/vehicle/config", json={"name": "flapSwingCdeg", "value": 12.5}).json()["value"] == 1250


def test_calibration_view_uses_reported_values(client):
    sid = _session(client)
    _post(client, sid, [_f(1000, cfg={"steerLeftUs": 1111, "flapRlMaxUs": 1620, "flapNeutralCdeg": 1500})])
    body = client.get("/api/vehicle/calibration").json()
    keys = {k["name"]: k for k in body["keys"]}
    assert body["reported"] is True and len(keys) == 16
    assert keys["steerLeftUs"]["value"] == 1111 and keys["steerLeftUs"]["reported"] is True
    assert keys["flapNeutralCdeg"]["value"] == 15.0 and keys["flapNeutralCdeg"]["unit"] == "°"
    assert "#define VCU_CAL_STEER_LEFT_US       1111" in body["header"]
    assert "#define VCU_CAL_FLAP_RL_MAX_US      1620" in body["header"]
    assert "#define VCU_CAL_FLAP_NEUTRAL_DEG    15" in body["header"]


def test_latest_frame_comes_from_memory_and_matches_the_database(client):
    sid = _session(client)
    _post(client, sid, [_f(1000, speed=3.0), _f(1100, speed=4.0)])
    assert LATEST and LATEST["frame"]["speed"] == 4.0
    mem = telemetry.get_latest_telemetry()
    saved = dict(LATEST)
    LATEST.clear()
    db = telemetry.get_latest_telemetry()                                   # DB fallback path
    LATEST.update(saved)
    assert mem["sessionId"] == db["sessionId"] == sid
    assert mem["frame"]["speed"] == db["frame"]["speed"] == 4.0
    # a simulated frame never becomes "the car's latest state"
    sim = _f(1200, speed=99.0)
    sim["frame"]["simulated"] = True
    _post(client, sid, [sim])
    assert telemetry.get_latest_telemetry()["frame"]["speed"] == 4.0


def test_websocket_still_records_before_broadcast_and_fans_out_to_all(client):
    sid = _session(client)
    with client.websocket_connect("/ws/telemetry") as a, client.websocket_connect("/ws/telemetry") as b, \
            client.websocket_connect("/ws/telemetry") as c:
        for ws in (a, b, c):
            assert ws.receive_json()["type"] == "connected"
        a.send_json({"type": "frame", "sessionId": sid, "snapshot": _f(5, speed=7.0)})
        for ws in (a, b, c):
            m = ws.receive_json()
            assert m["type"] == "frame" and m["snapshot"]["frame"]["speed"] == 7.0
        # recorded before the broadcast went out
        assert len(client.get(f"/api/sessions/{sid}/frames").json()) == 1
    assert telemetry.get_latest_telemetry()["frame"]["speed"] == 7.0


def test_drs_endpoint(client):
    d = client.post("/api/vehicle/drs", json={}).json()
    assert d["key"] == 25 and d["value"] == 1
    assert client.post("/api/vehicle/drs", json={"action": "cancel"}).json()["value"] == 0
    assert client.post("/api/vehicle/drs", json={"action": "bogus"}).status_code == 422
    assert vcu_config.validate(43, 9000) is None and vcu_config.validate(43, 9001) is not None
