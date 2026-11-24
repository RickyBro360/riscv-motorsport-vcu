"""
FW 2.2 backend tests: drive modes, dashboard run control and e-stop (the outbox side),
the mode-aware AI engineer, and the ESP32-CAM announcing itself to the relay.
"""
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("VCU_DB_PATH", str(Path(__file__).resolve().parent / "test_stage2d.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import ai_engineer, camera_relay, vcu_config  # noqa: E402
from app.db import DB_PATH  # noqa: E402
from app.main import app  # noqa: E402
from app.services import vehicle_commands  # noqa: E402
from tests.test_stage2d import _Cam, _post, _session  # noqa: E402


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


def _f(t_ms, mode="dyno", state="IDLE", hw="pcb_rev_b", run="off", steer=0.0, glat=0.0, yaw=0.0, speed=0.0,
       throttle=0.0, brake=0.0, tc=False, slip=0.0, straight=False, link=True, power=False, cfg=None, acks=None):
    return {"frame": {
        "timestamp": t_ms, "speed": speed, "voltage": 11.9, "current": 0.8, "motorTemp": 30.0, "batteryTemp": 28,
        "steering": steer, "throttle": throttle, "brake": brake, "gForceLat": glat, "gForceLong": 0, "gyroZ": yaw,
        "imu": {"roll": 0, "pitch": 0, "yaw": 0},
        "wing": {"frontLeft": 5, "frontRight": 5, "rearLeft": 5, "rearRight": 5},
        "wingTarget": {"frontLeft": 5, "frontRight": 5, "rearLeft": 5, "rearRight": 5},
        "wheels": {"fl": speed, "fr": speed, "rl": speed * (1 + slip), "rr": speed * (1 + slip)}, "vibrationMg": 30,
        "vcuState": state, "runState": run, "lastAiReason": "accepted",
        "track": {"id": 12, "pos": 0.1, "lap": 0, "runActive": run in ("bench_laps", "track_drive"),
                  "straightMode": straight, "currentLapMs": 0, "lastLapMs": 0, "stalled": False, "configPending": False},
        "vcuConfig": dict(cfg or {}, driveMode=1 if mode == "track" else 0, hwProfile=1 if hw == "pcb_rev_b" else 0,
                          pending=False),
        "configAcks": acks or [],
        "derived": {"slipRatio": slip},
        "vcu": {"driveMode": mode, "hwProfile": hw, "killSwitchFitted": hw == "pcb_rev_b",
                "motorPower": power if hw == "pcb_rev_b" else None, "remoteRun": run == "bench_laps",
                "tcActive": tc, "tcCutPct": 30 if tc else 0, "groundLinkOk": link, "aeroStraight": straight,
                "encoderFitted": True, "knob": 0, "runLaps": 0},
    }, "faults": []}


# ------------------------------------------------------------------ keys
def test_fw22_keys_and_validation():
    assert {5, 6, 7, 8, 22, 104, 105} <= set(vcu_config.KEYS)
    assert vcu_config.COMMAND_KEYS == {6, 8, 9, 23, 24, 25, 26, 27}   # FW 2.3: + motor, knob centre, calibration save/revert
    assert vcu_config.validate(22, 30) is not None and vcu_config.validate(22, 0) is None
    assert vcu_config.validate(22, 250) is None and vcu_config.validate(5, 2) is not None
    assert vcu_config.validate(104, 1) is not None          # read-only
    assert vcu_config.DEFAULTS["driveMode"] == 1             # TRACK by default, as the firmware


# ------------------------------------------------------------------ run control
def test_mode_run_estop_endpoints_queue_writes_and_ack_closes_them(client):
    assert client.post("/api/vehicle/mode", json={"mode": "rollers"}).status_code == 422
    m = client.post("/api/vehicle/mode", json={"mode": "dyno"}).json()
    assert m["key"] == 5 and m["value"] == 0 and m["status"] == "queued"
    r = client.post("/api/vehicle/run", json={"action": "start", "laps": 3}).json()
    assert r["key"] == 6 and r["value"] == 1 and r["lapsCommand"]["key"] == 7 and r["lapsCommand"]["value"] == 3
    assert client.post("/api/vehicle/run", json={"action": "start", "laps": 150}).status_code == 422
    batch = client.get("/api/vehicle/commands/next").json()["commands"]
    assert [(c["key"], c["value"]) for c in batch] == [(5, 0), (7, 3), (6, 1)]   # in order
    sid = _session(client)
    _post(client, sid, [_f(1000, acks=[{"key": 5, "status": 0, "value": 0}, {"key": 7, "status": 0, "value": 3},
                                       {"key": 6, "status": 0, "value": 1}])])
    st = {c["key"]: c["status"] for c in client.get("/api/vehicle/commands").json()}
    assert st[5] == "applied" and st[6] == "applied" and st[7] == "applied"
    # the VCU refusing (e.g. TRACK mode) is shown, not hidden
    client.post("/api/vehicle/run", json={"action": "dyno_cal"})
    client.get("/api/vehicle/commands/next")
    _post(client, sid, [_f(1200, acks=[{"key": 6, "status": 3, "value": 2}])])
    assert client.get("/api/vehicle/commands").json()[0]["status"] == "rejected_state"


def test_estop_and_stop_jump_the_queue_and_cancel_a_pending_start(client):
    client.post("/api/vehicle/config", json={"name": "cornerFrontCdeg", "value": 25})
    client.post("/api/vehicle/run", json={"action": "start"})
    e = client.post("/api/vehicle/estop", json={"action": "trigger"}).json()
    assert e["key"] == 8 and e["value"] == 1
    batch = client.get("/api/vehicle/commands/next").json()["commands"]
    assert batch[0] == {"id": e["id"], "key": 8, "value": 1}
    keys = [c["key"] for c in batch]
    assert 6 not in keys                                    # the start was superseded by the e-stop
    stop = client.post("/api/vehicle/run", json={"action": "stop"}).json()
    assert client.get("/api/vehicle/commands/next").json()["commands"][0]["id"] == stop["id"]
    # commands are resent after 1 s (not 3 s) until ACKed
    vehicle_commands.COMMAND_RESEND_S = 0.0
    try:
        again = client.get("/api/vehicle/commands/next").json()["commands"]
        assert {c["key"] for c in again} >= {8, 6}
    finally:
        vehicle_commands.COMMAND_RESEND_S = 1.0
    assert client.post("/api/vehicle/estop", json={"action": "reset"}).json()["value"] == 2


def test_vehicle_status_explains_why_a_run_cannot_start(client):
    sid = _session(client)
    _post(client, sid, [_f(1000, mode="track")])
    st = client.get("/api/vehicle/status").json()
    assert st["driveMode"] == "track" and st["hwProfile"] == "pcb_rev_b" and st["canStartRun"] is False
    assert any("DYNO" in r for r in st["startBlockedBy"])
    _post(client, sid, [_f(1100, mode="dyno", link=False)])
    st = client.get("/api/vehicle/status").json()
    assert not st["canStartRun"] and any("ground link" in r for r in st["startBlockedBy"])
    _post(client, sid, [_f(1200, mode="dyno")])
    assert client.get("/api/vehicle/status").json()["canStartRun"] is True


# ------------------------------------------------------------------ AI engineer
def test_track_mode_ai_anticipates_turn_in_and_dyno_lookahead_is_unchanged(client):
    sid = _session(client)
    # straight, powered, VCU in corner mode -> AI opens straight mode
    _post(client, sid, [_f(1000, mode="track", state="DRIVE", speed=6.0, throttle=70)])
    rec = client.get("/api/ai/recommendation").json()
    assert rec["trackMode"] and rec["straightModeAhead"] and rec["source"] == "engineer_station_track_mode_v1"
    # driver turns in while the VCU is still in straight mode -> corner mode, split from yaw x speed
    _post(client, sid, [_f(1100, mode="track", state="DRIVE", speed=6.0, throttle=60, steer=12, yaw=40,
                           straight=True)])
    rec = client.get("/api/ai/recommendation").json()
    assert not rec["straightModeAhead"] and "Turn-in" in rec["reason"] and rec["confidence"] < 0.8
    w = rec["recommendedWing"]
    assert w["frontRight"] > w["frontLeft"]                  # +yaw = turning left -> right flaps up
    # not driving -> no track-mode advice
    assert ai_engineer.live_track_mode_recommendation(_f(1, mode="track", state="IDLE")["frame"]) is None
    assert ai_engineer.live_track_mode_recommendation(_f(1, mode="dyno", state="DRIVE")["frame"]) is None


def test_engineer_is_mode_aware(client):
    sid = _session(client)
    frames = [_f(1000 + k * 200, mode="track", state="DRIVE", speed=5.0, throttle=80, tc=k % 2 == 0, slip=0.4,
                 glat=0.35 if k % 3 else 0.05, power=True) for k in range(60)]
    _post(client, sid, frames)
    a = ai_engineer.analyse(sid)
    ids = {i["id"] for i in a["insights"]}
    assert a["driveMode"] == "track" and a["hwProfile"] == "pcb_rev_b"
    assert {"mode", "traction", "cornering_g", "position"} <= ids
    kinds = {p["kind"] for p in a["proposals"]}
    assert "tc_power" in kinds
    assert not kinds & {"lap_length", "aero_optimise", "close_early_servo_lag", "duty_floor"}
    # dyno mode, link down: the engineer says why a dashboard run would be refused
    sid2 = _session(client)
    _post(client, sid2, [_f(1000 + k * 200, mode="dyno", link=False) for k in range(20)])
    a2 = ai_engineer.analyse(sid2)
    mode_ins = next(i for i in a2["insights"] if i["id"] == "mode")
    assert mode_ins["severity"] == "warning" and "ground link" in mode_ins["detail"]


def test_kill_switch_anomaly_is_flagged_on_rev_b(client):
    sid = _session(client)
    _post(client, sid, [_f(1000 + k * 100, mode="track", state="DRIVE", throttle=40, speed=3, power=False)
                        for k in range(30)])
    assert "kill_switch" in {i["id"] for i in ai_engineer.analyse(sid)["insights"]}


# ------------------------------------------------------------------ camera
def test_esp32_cam_announces_itself_and_the_relay_connects(client):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Cam)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/stream"
    try:
        assert client.post("/api/camera/announce", json={"url": "rtsp://x"}).status_code == 422
        r = client.post("/api/camera/announce", json={"url": url, "ip": "127.0.0.1", "rssi": -55}).json()
        assert r["switched"] and r["relayUrl"] == url
        t0 = time.time()
        while time.time() - t0 < 5 and client.get("/api/camera/relay").json()["frames"] < 3:
            time.sleep(0.05)
        st = client.get("/api/camera/relay").json()
        assert st["connected"] and st["origin"] == "announce" and st["announced"]["rssi"] == -55
        # repeated announcements do not reconnect
        assert not client.post("/api/camera/announce", json={"url": url}).json()["switched"]
        # the AI layer and the camera status now see the ESP32-CAM
        cam = client.get("/api/camera/status").json()
        assert cam["connected"] and cam["source"] == "esp32-cam relay"
        # an operator-typed URL that is delivering frames is not replaced by an announcement
        client.post("/api/camera/source", json={"url": url})
        t0 = time.time()
        while time.time() - t0 < 5 and not client.get("/api/camera/relay").json()["connected"]:
            time.sleep(0.05)
        r = client.post("/api/camera/announce", json={"url": "http://10.0.0.99:81/stream"}).json()
        assert not r["switched"] and client.get("/api/camera/relay").json()["url"] == url
    finally:
        client.post("/api/camera/source", json={"url": ""})
        srv.shutdown()
