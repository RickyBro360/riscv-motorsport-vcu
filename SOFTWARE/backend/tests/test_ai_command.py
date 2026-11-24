"""
/ws/ai-command (uplink for the standalone AI twin page): off by default; when enabled, duty goes
to key 9 (rate-limited, only on change), straight mode to key 25, e-stop to key 8, and the motor is
stopped when the page goes quiet or the socket closes.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("VCU_DB_PATH", str(Path(__file__).resolve().parent / "test_stage2d.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import ai_command  # noqa: E402
from app.db import get_conn  # noqa: E402
from app.main import app  # noqa: E402


def _rows(since_id):
    with get_conn() as conn:
        return [(r["key"], r["value"], r["origin"]) for r in conn.execute(
            "SELECT key, value, origin FROM vehicle_commands WHERE id > ? ORDER BY id", (since_id,))]


def _last_id():
    with get_conn() as conn:
        r = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM vehicle_commands").fetchone()
        return r["m"]


def test_ai_command_link_is_off_by_default(monkeypatch):
    monkeypatch.setattr(ai_command, "ENABLED", False)
    with TestClient(app) as client:                 # the app start-up creates the tables
        start = _last_id()
        with client.websocket_connect("/ws/ai-command") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error" and "VCU_AI_COMMAND_LINK" in msg["detail"]
        assert _rows(start) == []


def test_ai_command_link_forwards_duty_drs_estop_and_stops(monkeypatch):
    monkeypatch.setattr(ai_command, "ENABLED", True)
    monkeypatch.setattr(ai_command, "MIN_INTERVAL_S", 0.05)
    monkeypatch.setattr(ai_command, "SILENCE_STOP_S", 0.4)
    with TestClient(app) as client:
        start = _last_id()
        with client.websocket_connect("/ws/ai-command") as ws:
            assert ws.receive_json()["type"] == "connected"
            ws.send_json({"type": "ai_command", "duty_pct": 60.0, "straight_mode": False})
            time.sleep(0.1)
            ws.send_json({"type": "ai_command", "duty_pct": 60.5, "straight_mode": False})     # < 1.5 %: not forwarded
            time.sleep(0.1)
            ws.send_json({"type": "ai_command", "duty_pct": 72.0, "straight_mode": True})
            time.sleep(0.1)
            ws.send_json({"type": "ai_command", "duty_pct": 250.0, "straight_mode": False})    # clamped to 100 %
            ws.send_json({"type": "something_else", "duty_pct": 10})                           # ignored
            ws.send_json({"type": "ai_command", "duty_pct": "abc"})                            # ignored
            time.sleep(0.2)
            rows = _rows(start)
            assert rows == [(9, 600, "ai_engine"), (9, 720, "ai_engine"), (25, 1, "ai_engine"),
                            (9, 1000, "ai_engine"), (25, 0, "ai_engine")], rows
            time.sleep(0.9)                                                                    # page went quiet
            assert _rows(start)[-1] == (9, 0, "ai_engine")
            n = len(_rows(start))
            ws.send_json({"type": "ai_command", "duty_pct": 50.0})
            time.sleep(0.15)
            ws.send_json({"type": "ai_command", "estop": True})
            time.sleep(0.15)
            assert _rows(start)[n:] == [(9, 500, "ai_engine"), (8, 1, "ai_engine")]
            ws.send_json({"type": "ai_command", "duty_pct": 55.0})
            time.sleep(0.15)
        deadline = time.time() + 5.0                                                           # socket closed
        while time.time() < deadline and _rows(start)[-1] != (9, 0, "ai_engine"):
            time.sleep(0.05)
        assert _rows(start)[-1] == (9, 0, "ai_engine")


def test_ai_command_link_forwards_steering_and_centres(monkeypatch):
    monkeypatch.setattr(ai_command, "ENABLED", True)
    monkeypatch.setattr(ai_command, "MIN_INTERVAL_S", 0.05)
    monkeypatch.setattr(ai_command, "STEER_KEEPALIVE_S", 0.3)
    monkeypatch.setattr(ai_command, "MIN_STEER_INTERVAL_S", 0.05)
    monkeypatch.setattr(ai_command, "SILENCE_STOP_S", 5.0)
    with TestClient(app) as client:
        start = _last_id()
        with client.websocket_connect("/ws/ai-command") as ws:
            assert ws.receive_json()["type"] == "connected"
            ws.send_json({"type": "ai_command", "duty_pct": 0, "steer_deg": 12.34})
            time.sleep(0.1)
            ws.send_json({"type": "ai_command", "duty_pct": 0, "steer_deg": 12.5})      # < 0.5 deg: not forwarded
            time.sleep(0.1)
            ws.send_json({"type": "ai_command", "duty_pct": 0, "steer_deg": -99})       # clamped to 45 deg
            time.sleep(0.4)
            ws.send_json({"type": "ai_command", "duty_pct": 0, "steer_deg": -99})       # unchanged: keep-alive
            time.sleep(0.1)
            rows = [x for x in _rows(start) if x[0] == 27]
            assert rows == [(27, 123, "ai_engine"), (27, -450, "ai_engine"), (27, -450, "ai_engine")], rows
        deadline = time.time() + 5.0
        while time.time() < deadline and _rows(start)[-1] != (27, 0, "ai_engine"):
            time.sleep(0.05)
        assert _rows(start)[-1] == (27, 0, "ai_engine")
        assert (9, 0, "ai_engine") in _rows(start)
