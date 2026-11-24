from __future__ import annotations

from fastapi import APIRouter, Query

from app.db import LATEST, get_conn, loads

router = APIRouter(prefix="/api", tags=["telemetry"])


def _latest_frame() -> dict | None:
    """The most recently RECEIVED frame from the car (or a stand-in such as demo_car.py).

    FW 2.2 fix: this used to ORDER BY the frame's own timestamp. That is the gateway's
    uptime clock, so after a Pi or bridge restart (timestamps start again at 0) the old
    session's last frame stayed "latest" — the AI engineer, the AI downlink and the run
    controls would have worked from stale data. Arrival order (row id) is the truth.
    Frames the dashboard's offline SIMULATION recorded (marked "simulated": true) are
    skipped, so a tab left in Simulation Mode can never feed made-up data to the AI
    that talks to the real car.
    FW 2.3: served from the recorder's in-memory copy when there is one (same row, no SQLite read)."""
    if LATEST:
        return {"sessionId": LATEST["sessionId"], "frame": LATEST["frame"], "ai": LATEST["ai"],
                "diagnostics": LATEST["diagnostics"]}
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tf.frame_json, tf.ai_json, tf.diagnostics_json, tf.session_id
            FROM telemetry_frames tf
            JOIN sessions s ON s.id = tf.session_id
            ORDER BY tf.id DESC
            LIMIT 50
            """
        ).fetchall()
    row = None
    for r in rows:
        f = loads(r["frame_json"]) or {}
        if not f.get("simulated"):
            row = r
            break
    if row is None:
        return None
    return {
        "sessionId": row["session_id"],
        "frame": loads(row["frame_json"]),
        "ai": loads(row["ai_json"]),
        "diagnostics": loads(row["diagnostics_json"]),
    }


def get_latest_telemetry() -> dict | None:
    """Public accessor for the most recently recorded telemetry snapshot.
    Used by app/routers/ai.py so the AI engine and this router share one
    definition of "latest telemetry" instead of two DB queries drifting
    apart over time."""
    return _latest_frame()


@router.get("/vehicle/state")
def vehicle_state() -> dict:
    latest = _latest_frame()
    if latest is None:
        return {"mode": "no-data", "frame": None}
    return {"mode": "recorded", **latest}


@router.get("/telemetry/latest")
def telemetry_latest() -> dict:
    """Same authoritative record as /api/vehicle/state, exposed under the
    name the locked spec's endpoint list uses."""
    return vehicle_state()


@router.get("/faults")
def list_faults(session_id: str | None = Query(default=None), limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM faults WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM faults ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]
