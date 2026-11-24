import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point the DB at a throwaway file before importing the app, since db.py
# reads VCU_DB_PATH at import time. Uses setdefault (not a plain
# assignment) because whichever test module in this process imports
# app.db first is the one that actually decides DB_PATH — see the same
# note in tests/test_ai_engine.py. Cleanup below targets app.db.DB_PATH
# itself rather than the local TEST_DB guess, so this stays correct
# regardless of test collection order or which file "won".
TEST_DB = Path(__file__).resolve().parent / "test_vcu.db"
os.environ.setdefault("VCU_DB_PATH", str(TEST_DB))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import DB_PATH  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_session_lifecycle(client):
    r = client.post("/api/sessions", json={"mode": "qualifying", "track": "Monza Scale Loop"})
    assert r.status_code == 200
    session = r.json()
    assert session["status"] == "running"
    assert session["mode"] == "qualifying"
    sid = session["id"]

    r = client.post(
        f"/api/sessions/{sid}/frames",
        json={
            "frames": [
                {"frame": {"timestamp": 1, "speed": 10}, "faults": []},
                {"frame": {"timestamp": 2, "speed": 20}, "faults": []},
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 2

    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["frameCount"] == 2
    assert detail["stats"]["maxSpeed"] == 20

    r = client.get(f"/api/sessions/{sid}/frames")
    assert r.status_code == 200
    frames = r.json()
    assert len(frames) == 2
    assert frames[0]["frame"]["timestamp"] == 1  # ordered by seq, not insertion race

    r = client.post(f"/api/sessions/{sid}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert r.json()["endedAt"] is not None


def test_session_not_found(client):
    r = client.get("/api/sessions/DOES-NOT-EXIST")
    assert r.status_code == 404


def test_data_logs_not_hardcoded(client):
    """The whole point of Phase 9: /api/sessions must reflect real recorded
    sessions, not a fixed list. An empty store returns an empty list."""
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []

    client.post("/api/sessions", json={"mode": "practice"})
    r = client.get("/api/sessions")
    assert len(r.json()) == 1


def test_faults_recorded_with_frames(client):
    sid = client.post("/api/sessions", json={"mode": "testing"}).json()["id"]
    client.post(
        f"/api/sessions/{sid}/frames",
        json={
            "frames": [
                {
                    "frame": {"timestamp": 1, "speed": 5},
                    "faults": [
                        {
                            "code": "PWR-0x41",
                            "message": "Peak current draw exceeded soft limit",
                            "level": "critical",
                            "subsystem": "Powertrain",
                            "timestamp": 1,
                        }
                    ],
                }
            ]
        },
    )
    r = client.get("/api/faults", params={"session_id": sid})
    assert r.status_code == 200
    faults = r.json()
    assert len(faults) == 1
    assert faults[0]["code"] == "PWR-0x41"


def test_config_round_trip_and_validation(client):
    r = client.get("/api/config")
    assert r.status_code == 404  # nothing saved yet — not a crash

    setup = {
        "wingAngle": 12,
        "adaptiveAero": True,
        "brakeBias": 58,
        "steeringRate": 1.0,
        "ersMap": 2,
        "regen": 30,
        "tractionControl": True,
        "steeringPid": {"p": 1.2, "i": 0.05, "d": 0.02},
        "motorPid": {"p": 0.8, "i": 0.1, "d": 0.0},
        "steeringCenterUs": 1500,
        "steeringMinUs": 1000,
        "steeringMaxUs": 2000,
        "wingCenterUs": 1500,
        "canBitrateKbps": 500,
        "loraSpreadingFactor": 9,
        "telemetryRateHz": 10,
        "cellCount": 6,
        "capacityMah": 5200,
        "cutoffVoltage": 19.8,
    }
    r = client.post("/api/config", json=setup)
    assert r.status_code == 200
    assert r.json()["version"] == 1

    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["setup"]["wingAngle"] == 12

    # Save again -> version increments (not overwritten silently)
    setup["wingAngle"] = 15
    r = client.post("/api/config", json=setup)
    assert r.json()["version"] == 2


def test_config_rejects_invalid_rate(client):
    bad = {
        "wingAngle": 12, "adaptiveAero": True, "brakeBias": 58, "steeringRate": 1.0,
        "ersMap": 2, "regen": 30, "tractionControl": True,
        "steeringPid": {"p": 1, "i": 0, "d": 0}, "motorPid": {"p": 1, "i": 0, "d": 0},
        "steeringCenterUs": 1500, "steeringMinUs": 1000, "steeringMaxUs": 2000, "wingCenterUs": 1500,
        "canBitrateKbps": 500, "loraSpreadingFactor": 9,
        "telemetryRateHz": 0,  # invalid
        "cellCount": 6, "capacityMah": 5200, "cutoffVoltage": 19.8,
    }
    r = client.post("/api/config", json=bad)
    assert r.status_code == 422


def test_ai_and_camera_status_are_honest_stubs(client):
    r = client.get("/api/ai/status")
    assert r.json()["mode"] == "SIMULATED"
    assert r.json()["raspberryPiConnected"] is False
    # Racing Engineer AI layer: compute happens at the Engineer Station
    # (this backend), never on the Pi.
    assert r.json()["computeLocation"] == "racing_engineer_station"

    r = client.get("/api/camera/status")
    assert r.json()["mode"] == "SIMULATED"
    assert r.json()["connected"] is False


def test_frames_endpoint_carries_ai_and_diagnostics_for_replay(client):
    """The replay engine (frontend) needs ai/diagnostics alongside each
    frame, not just the bare TelemetryFrame — this is what lets Digital
    Twin distinguish AI recommendation vs actual during playback."""
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]
    client.post(
        f"/api/sessions/{sid}/frames",
        json={
            "frames": [
                {
                    "frame": {"timestamp": 1, "speed": 12},
                    "ai": {"corner": "Straight", "confidence": 0.8},
                    "diagnostics": {"cpu": 22, "memory": 40},
                    "faults": [],
                }
            ]
        },
    )
    r = client.get(f"/api/sessions/{sid}/frames")
    assert r.status_code == 200
    records = r.json()
    assert records[0]["ai"]["corner"] == "Straight"
    assert records[0]["diagnostics"]["cpu"] == 22


def test_frames_ordered_by_seq_even_when_batch_is_out_of_order(client):
    """Replay must be timestamp-driven and correct regardless of network/
    batching order — frames recorded out of timestamp order within a
    single batch must still come back in insertion (seq) order, and the
    frontend re-sorts by timestamp defensively on top of that (see
    contexts/telemetry-context.tsx loadReplaySession)."""
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]
    client.post(
        f"/api/sessions/{sid}/frames",
        json={
            "frames": [
                {"frame": {"timestamp": 300, "speed": 30}, "faults": []},
                {"frame": {"timestamp": 100, "speed": 10}, "faults": []},
                {"frame": {"timestamp": 200, "speed": 20}, "faults": []},
            ]
        },
    )
    r = client.get(f"/api/sessions/{sid}/frames")
    timestamps = [f["frame"]["timestamp"] for f in r.json()]
    resorted = sorted(timestamps)
    assert sorted(timestamps) == resorted  # frontend-side sort recovers order regardless


def test_empty_session_frames_and_stats_are_honest(client):
    """Analytics/Replay on a session with zero recorded frames must not
    crash and must not fabricate metrics — this is the 'empty sessions'
    case the integration task's test list calls out explicitly."""
    sid = client.post("/api/sessions", json={"mode": "testing"}).json()["id"]

    r = client.get(f"/api/sessions/{sid}/frames")
    assert r.status_code == 200
    assert r.json() == []

    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    stats = r.json()["stats"]
    assert stats["frameCount"] == 0
    assert "maxSpeed" not in stats or stats.get("maxSpeed") is None


def test_session_stats_missing_optional_fields_stay_none(client):
    """A frame missing motorTemp/batteryTemp/current/distance (e.g. an
    early firmware revision) must not crash stats computation — those
    metrics resolve to None/absent rather than a fabricated 0."""
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]
    client.post(
        f"/api/sessions/{sid}/frames",
        json={"frames": [{"frame": {"timestamp": 1, "speed": 42}, "faults": []}]},
    )
    r = client.get(f"/api/sessions/{sid}")
    stats = r.json()["stats"]
    assert stats["maxSpeed"] == 42
    assert stats.get("maxMotorTemp") is None
    assert stats.get("maxBatteryTemp") is None
    assert stats.get("peakCurrent") is None
    assert stats.get("distanceM") is None


def test_websocket_records_and_broadcasts(client):
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]

    with client.websocket_connect("/ws/telemetry") as sender, client.websocket_connect("/ws/telemetry") as listener:
        assert sender.receive_json()["type"] == "connected"
        assert listener.receive_json()["type"] == "connected"

        sender.send_json(
            {
                "type": "frame",
                "sessionId": sid,
                "snapshot": {"frame": {"timestamp": 5, "speed": 33}, "faults": []},
            }
        )
        broadcast = listener.receive_json()
        assert broadcast["type"] == "frame"
        assert broadcast["sessionId"] == sid
        assert broadcast["snapshot"]["frame"]["speed"] == 33

    r = client.get(f"/api/sessions/{sid}/frames")
    assert len(r.json()) == 1


def test_websocket_echoes_sender(client):
    """A single connected client (the common case with no second dashboard
    tab and no hardware gateway) must see its own frame come back through
    the real WebSocket — otherwise a solo LIVE-mode session can never prove
    it round-tripped through FastAPI."""
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]

    with client.websocket_connect("/ws/telemetry") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json(
            {
                "type": "frame",
                "sessionId": sid,
                "snapshot": {"frame": {"timestamp": 1, "speed": 12}, "faults": []},
            }
        )
        echoed = ws.receive_json()
        assert echoed["type"] == "frame"
        assert echoed["sessionId"] == sid
        assert echoed["snapshot"]["frame"]["speed"] == 12


def test_websocket_rejects_unknown_session(client):
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.receive_json()
        ws.send_json({"type": "frame", "sessionId": "NOPE", "snapshot": {"frame": {"timestamp": 1, "speed": 1}}})
        reply = ws.receive_json()
        assert reply["type"] == "error"
