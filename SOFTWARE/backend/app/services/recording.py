from __future__ import annotations

import threading

from app.db import LATEST, dumps, get_conn, now
from app.models import TelemetrySnapshotIn

# FW 2.3: recording runs in a worker thread (off the event loop); one writer at a time keeps the
# per-session seq numbers unique.
_WRITE_LOCK = threading.Lock()


def record_batch(session_id: str, snapshots: list[TelemetrySnapshotIn]) -> int:
    """The one function that writes telemetry into persistent storage.
    Both POST /api/sessions/{id}/frames and the /ws/telemetry ingest path
    call this, so there is a single authoritative recorder rather than two
    versions of the same logic drifting apart."""
    if not snapshots:
        return 0
    with _WRITE_LOCK:
        return _record_batch_locked(session_id, snapshots)


def _record_batch_locked(session_id: str, snapshots: list[TelemetrySnapshotIn]) -> int:
    last_live = None
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if exists is None:
            raise ValueError(f"Session {session_id} not found")

        start_seq = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM telemetry_frames WHERE session_id = ?",
            (session_id,),
        ).fetchone()["next_seq"]

        for i, snap in enumerate(snapshots):
            cur = conn.execute(
                "INSERT INTO telemetry_frames (session_id, seq, timestamp, frame_json, ai_json, diagnostics_json, "
                "received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    start_seq + i,
                    snap.frame.timestamp,
                    dumps(snap.frame.model_dump()),
                    dumps(snap.ai) if snap.ai is not None else None,
                    dumps(snap.diagnostics) if snap.diagnostics is not None else None,
                    now(),
                ),
            )
            if not snap.frame.model_dump().get("simulated"):
                last_live = (cur.lastrowid, snap)
            for fault in snap.faults:
                conn.execute(
                    "INSERT INTO faults (session_id, code, message, level, subsystem, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, fault.code, fault.message, fault.level, fault.subsystem, fault.timestamp),
                )
    if last_live is not None:
        row_id, snap = last_live
        LATEST.clear()
        LATEST.update({"rowId": row_id, "sessionId": session_id, "frame": snap.frame.model_dump(),
                       "ai": snap.ai, "diagnostics": snap.diagnostics})
    # Stage 2d: VCU answers to approved changes ride on the telemetry frames.
    from app.services import vehicle_commands  # local import: avoids a cycle at import time
    for snap in snapshots:
        extra = snap.frame.model_dump()
        if extra.get("configAcks"):
            vehicle_commands.process_acks(extra.get("configAcks"))
        vehicle_commands.reconcile_with_report(extra.get("vcuConfig"))
    return len(snapshots)
