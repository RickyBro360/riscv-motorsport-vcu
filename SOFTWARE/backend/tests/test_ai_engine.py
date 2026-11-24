"""
Tests for the Racing Engineer AI layer (app/ai_engine.py + /api/ai/recommendation).

Covers the project brief's TESTING section for the software side of the
chain (telemetry -> AI -> recommendation):
  - valid recommendation
  - AI unavailable (no telemetry yet)
  - changing vehicle state -> recommendation changes accordingly
  - deterministic (same input -> same output)
  - safety-priority ordering
  - the AI never touches actuators directly (no such API exists here —
    this is a structural guarantee, verified by the shape of the
    contract: a recommendation, never a command)

The hardware side of the same chain (recommendation -> radio substitute
-> Pi -> ESP32 simulation -> VCU validation, including stale/invalid
recommendations and communication loss) is covered separately in
HARDWARE/tests/test_ai_recommendation_chain.py, since it doesn't need a
FastAPI app at all.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# NOTE: app/db.py reads VCU_DB_PATH once, at import time (see its
# module-level DB_PATH). If tests/test_api.py has already been imported
# in this same pytest process, that import already fixed DB_PATH — a
# fresh os.environ assignment here would be silently ignored. So rather
# than fight that, this file cleans up whatever app.db.DB_PATH actually
# resolved to (same pattern tests/test_api.py uses when it's the one
# that ends up "owning" the import first), keeping both test files safe
# to run together or alone.
os.environ.setdefault("VCU_DB_PATH", str(Path(__file__).resolve().parent / "test_ai_engine.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app.ai_engine import (  # noqa: E402
    AI_RECOMMENDATION_VALIDITY_MS,
    generate_recommendation,
)
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


NOMINAL_FRAME = {
    "timestamp": 1000.0,
    "speed": 45.0,
    "motorTemp": 60.0,
    "batteryTemp": 30.0,
    "battery": 80.0,
    "gForceLat": 0.1,
    "gForceLong": 0.0,
    "steering": 2.0,
}


# ---------------------------------------------------------------------------
# Engine unit tests (no HTTP, pure function)
# ---------------------------------------------------------------------------


def test_valid_recommendation_shape():
    rec = generate_recommendation(NOMINAL_FRAME)
    assert rec["type"] in ("DRIVER_ADVICE", "AERO_ADVICE", "SAFETY_ADVICE")
    assert 0.0 <= rec["confidence"] <= 1.0
    assert isinstance(rec["reason"], str) and rec["reason"]
    assert rec["target"]
    assert rec["validityMs"] == AI_RECOMMENDATION_VALIDITY_MS
    assert rec["validUntil"] == rec["timestamp"] + AI_RECOMMENDATION_VALIDITY_MS
    # existing AiRecommendation-contract fields must also be present —
    # "smallest compatible addition", not a breaking replacement
    for key in ("corner", "grip", "recommendedWing", "recommendedSpeed", "efficiency", "note"):
        assert key in rec


def test_deterministic_same_input_same_output():
    a = generate_recommendation(NOMINAL_FRAME)
    b = generate_recommendation(dict(NOMINAL_FRAME))
    assert a == b


def test_safety_advice_takes_priority_on_overtemp():
    frame = dict(NOMINAL_FRAME, motorTemp=95.0)
    rec = generate_recommendation(frame)
    assert rec["type"] == "SAFETY_ADVICE"
    assert "motor" in rec["reason"].lower()
    assert "motor_overtemp" in rec["warnings"]


def test_safety_advice_on_low_battery():
    frame = dict(NOMINAL_FRAME, battery=8.0)
    rec = generate_recommendation(frame)
    assert rec["type"] == "SAFETY_ADVICE"
    assert "battery_low" in rec["warnings"]


def test_aero_advice_under_high_cornering_load():
    frame = dict(NOMINAL_FRAME, gForceLat=1.1, speed=70.0)
    rec = generate_recommendation(frame)
    assert rec["type"] == "AERO_ADVICE"
    assert rec["target"] == "aero.rearWing"


def test_driver_advice_reduce_entry_speed_moderate_cornering():
    frame = dict(NOMINAL_FRAME, gForceLat=0.6, speed=55.0)
    rec = generate_recommendation(frame)
    assert rec["type"] == "DRIVER_ADVICE"
    assert "entrySpeed" in rec["target"]


def test_changing_vehicle_state_changes_recommendation():
    """Same engine, evolving telemetry -> recommendation tracks the change,
    exactly like it would if fed a live stream."""
    calm = generate_recommendation(dict(NOMINAL_FRAME, gForceLat=0.05, motorTemp=40.0))
    hot = generate_recommendation(dict(NOMINAL_FRAME, motorTemp=96.0))
    cornering = generate_recommendation(dict(NOMINAL_FRAME, gForceLat=1.2, speed=80.0))
    assert calm["type"] == "DRIVER_ADVICE"
    assert hot["type"] == "SAFETY_ADVICE"
    assert cornering["type"] == "AERO_ADVICE"
    assert len({calm["type"], hot["type"], cornering["type"]}) == 3


def test_missing_fields_degrade_gracefully_not_crash():
    rec = generate_recommendation({"timestamp": 1.0})
    assert rec["type"] in ("DRIVER_ADVICE", "AERO_ADVICE", "SAFETY_ADVICE")


def test_video_available_is_labeled_not_used():
    rec = generate_recommendation(NOMINAL_FRAME, video_available=True)
    assert rec["videoAvailable"] is True
    assert rec["videoUsed"] is False  # no real CV pipeline yet — must never be faked


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_ai_recommendation_unavailable_with_no_telemetry(client):
    """AI unavailable / vehicle remains operational: no telemetry yet ->
    a clear, non-crashing 'unavailable' shape, never a 500."""
    r = client.get("/api/ai/recommendation")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["type"] is None


def test_ai_recommendation_valid_after_telemetry_recorded(client):
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "frame",
                "sessionId": sid,
                "snapshot": {"frame": NOMINAL_FRAME, "faults": []},
            }
        )
        ws.receive_json()

    r = client.get("/api/ai/recommendation")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["type"] in ("DRIVER_ADVICE", "AERO_ADVICE", "SAFETY_ADVICE")
    assert body["sessionId"] == sid


def test_ai_recommendation_tracks_changing_vehicle_state(client):
    """Feed two different telemetry frames through the real pipeline and
    confirm the recommendation returned by the endpoint changes with it —
    the software-side equivalent of the brief's 'changing vehicle state ->
    recommendation changes accordingly'."""
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]

    def send(frame):
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_json()
            ws.send_json({"type": "frame", "sessionId": sid, "snapshot": {"frame": frame, "faults": []}})
            ws.receive_json()

    send(dict(NOMINAL_FRAME, timestamp=1000.0, motorTemp=40.0, gForceLat=0.05))
    first = client.get("/api/ai/recommendation").json()

    send(dict(NOMINAL_FRAME, timestamp=2000.0, motorTemp=96.0))
    second = client.get("/api/ai/recommendation").json()

    assert first["type"] != second["type"]
    assert second["type"] == "SAFETY_ADVICE"


def test_ai_status_reports_engineer_station_not_pi(client):
    r = client.get("/api/ai/status")
    body = r.json()
    assert body["mode"] == "SIMULATED"
    assert body["computeLocation"] == "racing_engineer_station"


def test_no_power_sensor_is_not_battery_low():
    """Stage 2c bench (no INA260): voltage 0 V / SoC 0 % must not produce a
    permanent battery-low SAFETY_ADVICE."""
    from app.ai_engine import generate_recommendation as compute_recommendation
    rec = compute_recommendation({"timestamp": 1.0, "speed": 0.0, "voltage": 0.0, "battery": 0.0,
                                  "motorTemp": 25.0, "batteryTemp": 25.0})
    assert "battery_low" not in rec.get("warnings", [])
