"""
test_energy.py — measured energy replaces the old placeholder formulas.

Before Stage 2.1 the AI response reported `energyPerLap = 35 + 0.15 x speed`
and `predictedRangeLaps = battery% / 4` — numbers with no physical basis.
These tests pin the new behaviour: integrate what was recorded, or say the
data is not there. Never invent.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_DB = Path(__file__).resolve().parent / "test_vcu.db"
os.environ.setdefault("VCU_DB_PATH", str(TEST_DB))

from fastapi.testclient import TestClient  # noqa: E402

from app.ai_engine import generate_recommendation  # noqa: E402
from app.db import DB_PATH  # noqa: E402
from app.main import app  # noqa: E402
from app.services.energy import energy_context, session_energy  # noqa: E402


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


def _frame(t_ms: float, *, speed=36.0, voltage=10.0, current=2.0, distance=0.0, soc=80.0) -> dict:
    return {
        "schemaVersion": 1, "timestamp": t_ms, "speed": speed, "voltage": voltage,
        "current": current, "battery": soc, "distance": distance,
        "derived": {"socCoulombPct": soc},
    }


def _record_run(client, *, samples=200, dt_ms=50.0) -> str:
    """10 s at 36 km/h (10 m/s) drawing 10 V x 2 A = 20 W -> 100 m, 0.0556 Wh."""
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]
    frames = []
    for k in range(samples):
        t = k * dt_ms
        frames.append({"frame": _frame(t, distance=10.0 * (t / 1000.0)), "faults": []})
    r = client.post(f"/api/sessions/{sid}/frames", json={"frames": frames})
    assert r.status_code in (200, 201)
    return sid


def test_energy_integrated_from_recorded_frames(client):
    sid = _record_run(client)
    stats = session_energy(sid)
    assert stats["available"] is True
    assert stats["energyWh"] == pytest.approx(20 * 10 / 3600.0, rel=0.05)   # 20 W for ~10 s
    assert stats["distanceM"] == pytest.approx(100.0, rel=0.05)
    assert stats["whPerKm"] == pytest.approx(stats["energyWh"] / 0.1, abs=0.1)


def test_energy_unavailable_without_movement(client):
    sid = client.post("/api/sessions", json={"mode": "practice"}).json()["id"]
    client.post(f"/api/sessions/{sid}/frames",
                json={"frames": [{"frame": _frame(0.0, speed=0.0, distance=0.0), "faults": []}]})
    stats = session_energy(sid)
    assert stats["available"] is False
    assert "not enough" in stats["reason"]
    assert energy_context(sid) is None          # the AI gets nothing rather than a guess


def test_recommendation_reports_measured_energy(client):
    sid = _record_run(client)
    ctx = energy_context(sid, lap_distance_m=100.0)
    assert ctx is not None and ctx["measured"] is True
    rec = generate_recommendation(_frame(1000.0), energy_context=ctx)
    assert rec["energyMeasured"] is True
    assert rec["energyPerLap"] == pytest.approx(ctx["whPerKm"] * 0.1, abs=0.1)
    assert rec["predictedRangeLaps"] is not None and rec["predictedRangeLaps"] > 0
    assert "energy_unmeasured" not in rec["warnings"]


def test_recommendation_admits_when_energy_is_unknown():
    rec = generate_recommendation(_frame(1000.0))          # no energy context at all
    assert rec["energyPerLap"] is None
    assert rec["predictedRangeLaps"] is None
    assert rec["energyMeasured"] is False
    assert "energy_unmeasured" in rec["warnings"]


def test_energy_endpoint(client):
    sid = _record_run(client)
    r = client.get("/api/ai/energy", params={"session_id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["sessionId"] == sid
    assert body["whPerKm"] > 0


def test_energy_endpoint_without_data(client):
    r = client.get("/api/ai/energy")
    assert r.status_code == 200
    assert r.json()["available"] is False
