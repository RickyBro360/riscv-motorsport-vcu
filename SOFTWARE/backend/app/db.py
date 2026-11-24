"""
SQLite persistence layer.

Design choice: plain sqlite3 (stdlib) rather than an ORM. This is a
final-year project backend with a handful of tables and no need for
migrations tooling yet — an ORM would add a dependency and an
abstraction layer without buying much. The schema is deliberately
easy to move to PostgreSQL later: every statement below is
standard SQL with no SQLite-only extensions (no JSON1 functions
used in queries, just TEXT columns holding JSON blobs that the
application layer parses).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(os.environ.get("VCU_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "vcu.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    track TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    lap_count INTEGER NOT NULL DEFAULT 0,
    best_lap_s REAL,
    config_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS telemetry_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    frame_json TEXT NOT NULL,
    ai_json TEXT,
    diagnostics_json TEXT,
    received_at REAL                -- Stage 2d: wall clock at ingest (frame timestamps are the Pi's own clock)
);
CREATE INDEX IF NOT EXISTS idx_frames_session ON telemetry_frames(session_id, seq);

CREATE TABLE IF NOT EXISTS faults (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    level TEXT NOT NULL,
    subsystem TEXT NOT NULL,
    timestamp REAL NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_faults_session ON faults(session_id);

-- Stage 2d: operator-approved changes on their way to the VCU, and their fate.
CREATE TABLE IF NOT EXISTS vehicle_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    key INTEGER NOT NULL,
    value INTEGER NOT NULL,
    status TEXT NOT NULL,            -- queued | sent | applied | staged_next_lap | rejected_* | expired
    attempts INTEGER NOT NULL DEFAULT 0,
    last_sent_at REAL,
    acked_at REAL,
    ack_value INTEGER,
    origin TEXT NOT NULL,            -- 'operator' | 'ai:<suggestion id>'
    suggestion_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cmd_status ON vehicle_commands(status);

-- Stage 2d: AI engineer proposals and what the operator decided.
CREATE TABLE IF NOT EXISTS ai_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    status TEXT NOT NULL,            -- proposed | approved | applied | rejected | failed | expired
    session_id TEXT,
    track_id INTEGER,
    title TEXT NOT NULL,
    body_json TEXT NOT NULL,         -- rationale, evidence, changes, expected effect, risk
    outcome_json TEXT                -- before/after comparison once applied
);
CREATE INDEX IF NOT EXISTS idx_sugg_status ON ai_suggestions(status);

CREATE TABLE IF NOT EXISTS configuration (
    id TEXT PRIMARY KEY DEFAULT 'current',
    setup_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # FW 2.3: WAL journal. A stray -wal/-shm next to a deleted database must not be replayed
    # into a new, empty one.
    if not DB_PATH.exists():
        for suffix in ("-wal", "-shm"):
            stray = DB_PATH.with_name(DB_PATH.name + suffix)
            if stray.exists():
                stray.unlink()
    LATEST.clear()
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # Stage 2d migration for databases created before received_at existed
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(telemetry_frames)").fetchall()}
        if "received_at" not in cols:
            conn.execute("ALTER TABLE telemetry_frames ADD COLUMN received_at REAL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_frames_received ON telemetry_frames(received_at)")


# FW 2.3 (dashboard lag): the newest non-simulated frame, kept in memory by the recorder.
# Every "what is the car doing now" endpoint (vehicle status, twin, AI recommendation — polled
# several times a second by the dashboard AND the gateway's AI task) used to load and JSON-parse
# the last 50 frames from SQLite on each call. init_db() clears it (new database = no frame).
LATEST: dict = {}


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # FW 2.3: with WAL, readers no longer block the telemetry writer, and NORMAL sync means a
    # commit is not an fsync (a crash can lose the last few frames, never corrupt the file).
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> float:
    return time.time()


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)
