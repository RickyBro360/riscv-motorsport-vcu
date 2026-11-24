from __future__ import annotations

import csv
import io
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.db import get_conn, loads, now
from app.models import (
    FrameBatchIn,
    SessionCreate,
    SessionDetailOut,
    SessionOut,
)
from app.services.recording import record_batch

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _row_to_session_out(row) -> SessionOut:
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM telemetry_frames WHERE session_id = ?", (row["id"],)
        ).fetchone()["c"]
    return SessionOut(
        id=row["id"],
        mode=row["mode"],
        status=row["status"],
        track=row["track"],
        createdAt=row["created_at"],
        startedAt=row["started_at"],
        endedAt=row["ended_at"],
        lapCount=row["lap_count"],
        bestLapS=row["best_lap_s"],
        frameCount=count,
    )


@router.post("", response_model=SessionOut)
def create_session(body: SessionCreate) -> SessionOut:
    session_id = f"SESS-{uuid.uuid4().hex[:8].upper()}"
    ts = now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, mode, status, track, created_at, started_at) VALUES (?, ?, 'running', ?, ?, ?)",
            (session_id, body.mode, body.track, ts, ts),
        )
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session_out(row)


@router.get("", response_model=list[SessionOut])
def list_sessions() -> list[SessionOut]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    return [_row_to_session_out(r) for r in rows]


def _get_session_row(session_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return row


def _compute_stats(session_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        frame_rows = conn.execute(
            "SELECT frame_json FROM telemetry_frames WHERE session_id = ? ORDER BY seq ASC", (session_id,)
        ).fetchall()
        fault_count = conn.execute(
            "SELECT COUNT(*) AS c FROM faults WHERE session_id = ?", (session_id,)
        ).fetchone()["c"]

    frames = [loads(r["frame_json"]) for r in frame_rows]
    if not frames:
        return {"frameCount": 0, "faultCount": fault_count}

    speeds = [f.get("speed", 0) for f in frames]
    motor_temps = [f.get("motorTemp") for f in frames if f.get("motorTemp") is not None]
    batt_temps = [f.get("batteryTemp") for f in frames if f.get("batteryTemp") is not None]
    currents = [f.get("current") for f in frames if f.get("current") is not None]
    distances = [f.get("distance") for f in frames if f.get("distance") is not None]

    duration_s = (frames[-1]["timestamp"] - frames[0]["timestamp"]) / 1000 if len(frames) > 1 else 0

    return {
        "frameCount": len(frames),
        "faultCount": fault_count,
        "durationS": duration_s,
        "maxSpeed": max(speeds) if speeds else None,
        "avgSpeed": sum(speeds) / len(speeds) if speeds else None,
        "maxMotorTemp": max(motor_temps) if motor_temps else None,
        "maxBatteryTemp": max(batt_temps) if batt_temps else None,
        "peakCurrent": max(currents) if currents else None,
        "distanceM": max(distances) if distances else None,
    }


@router.get("/{session_id}", response_model=SessionDetailOut)
def get_session(session_id: str) -> SessionDetailOut:
    row = _get_session_row(session_id)
    base = _row_to_session_out(row)
    return SessionDetailOut(**base.model_dump(), stats=_compute_stats(session_id))


@router.post("/{session_id}/frames")
def ingest_frames(session_id: str, batch: FrameBatchIn) -> dict:
    _get_session_row(session_id)  # 404s if missing
    accepted = record_batch(session_id, batch.frames)
    return {"accepted": accepted}


@router.get("/{session_id}/frames")
def get_frames(session_id: str, limit: Optional[int] = None, offset: int = 0) -> list[dict]:
    """Timestamp-ordered frames for the replay engine — the authoritative
    record, as opposed to the frontend's rolling 300-frame memory buffer."""
    _get_session_row(session_id)
    query = "SELECT frame_json, ai_json, diagnostics_json FROM telemetry_frames WHERE session_id = ? ORDER BY seq ASC"
    params: list[Any] = [session_id]
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "frame": loads(r["frame_json"]),
            "ai": loads(r["ai_json"]),
            "diagnostics": loads(r["diagnostics_json"]),
        }
        for r in rows
    ]


def _set_status(session_id: str, status: str, end: bool = False) -> SessionOut:
    row = _get_session_row(session_id)
    with get_conn() as conn:
        if end:
            conn.execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?", (status, now(), session_id)
            )
        else:
            conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session_out(row)


@router.post("/{session_id}/stop", response_model=SessionOut)
def stop_session(session_id: str) -> SessionOut:
    return _set_status(session_id, "completed", end=True)


@router.post("/{session_id}/pause", response_model=SessionOut)
def pause_session(session_id: str) -> SessionOut:
    return _set_status(session_id, "paused")


@router.post("/{session_id}/resume", response_model=SessionOut)
def resume_session(session_id: str) -> SessionOut:
    return _set_status(session_id, "running")


@router.get("/{session_id}/export.json")
def export_json(session_id: str) -> dict:
    row = _get_session_row(session_id)
    return {
        "session": _row_to_session_out(row).model_dump(),
        "stats": _compute_stats(session_id),
        "frames": get_frames(session_id),
    }


@router.get("/{session_id}/export.csv")
def export_csv(session_id: str) -> StreamingResponse:
    _get_session_row(session_id)
    frames = get_frames(session_id)
    buf = io.StringIO()
    if not frames:
        buf.write("timestamp,speed\n")
    else:
        flat_keys = sorted(frames[0]["frame"].keys())
        writer = csv.DictWriter(buf, fieldnames=flat_keys, extrasaction="ignore")
        writer.writeheader()
        for f in frames:
            row = {k: f["frame"].get(k) for k in flat_keys}
            # flatten nested dicts (imu, wing, wingTarget) to avoid raw JSON in cells
            for k, v in list(row.items()):
                if isinstance(v, dict):
                    row[k] = ";".join(f"{sk}={sv}" for sk, sv in v.items())
            writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={session_id}.csv"},
    )
