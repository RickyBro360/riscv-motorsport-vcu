"""
vehicle_commands.py — the outbox from the Racing Engineer Station to the VCU.

Only two things put a row here: the operator (Vehicle Setup / Track Map pages) and
the operator's APPROVAL of an AI-engineer suggestion. The AI never writes here by
itself. The gateway (Pi in MODE A, ground bridge in MODE B) collects rows with
GET /api/vehicle/commands/next and turns each into one CONFIG_WRITE (CAN 0x180 /
LoRa type 8). The VCU validates it and answers CONFIG_ACK; that ACK comes back in
the telemetry frame (`configAcks`) and closes the row. A row that is not ACKed is
offered again after RESEND_S, up to MAX_ATTEMPTS, then marked expired — LoRa is
lossy and the operator must never be told "applied" on a guess.
"""
from __future__ import annotations

from app import vcu_config
from app.db import get_conn, now

RESEND_S = 3.0
MAX_ATTEMPTS = 6
OPEN = ("queued", "sent")
# FW 2.2 commands (run / e-stop): resent faster and more often, and a STOP or E-STOP goes first.
COMMAND_RESEND_S = 1.0
COMMAND_MAX_ATTEMPTS = 10
KEY_RUN_CMD, KEY_ESTOP = 6, 8
KEY_MOTOR_CMD = 9            # FW 2.3: motor duty from the dashboard (0 = stop)


def _urgent(key: int, value: int) -> bool:
    return key == KEY_ESTOP or (key in (KEY_RUN_CMD, KEY_MOTOR_CMD) and value == 0)


def queue(key: int, value: int, origin: str = "operator", suggestion_id: int | None = None) -> dict:
    with get_conn() as conn:
        # a newer write to the same key supersedes an older one still in flight
        conn.execute("UPDATE vehicle_commands SET status = 'superseded' WHERE key = ? AND status IN ('queued','sent')",
                     (key,))
        if key == KEY_ESTOP and value == 1:   # an e-stop cancels a run / motor request still in flight
            conn.execute("UPDATE vehicle_commands SET status = 'superseded' WHERE key IN (?, ?) AND value != 0 "
                         "AND status IN ('queued','sent')", (KEY_RUN_CMD, KEY_MOTOR_CMD))
        cur = conn.execute(
            "INSERT INTO vehicle_commands (created_at, key, value, status, origin, suggestion_id) "
            "VALUES (?, ?, ?, 'queued', ?, ?)", (now(), key, value, origin, suggestion_id))
        cid = cur.lastrowid
    return get(cid)


def get(cid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM vehicle_commands WHERE id = ?", (cid,)).fetchone()
    return _out(row) if row else None


def _out(row) -> dict:
    d = dict(row)
    k = vcu_config.KEYS.get(d["key"])
    d["name"] = k.name if k else str(d["key"])
    d["label"] = k.label if k else str(d["key"])
    d["displayValue"] = k.display(d["value"]) if k else d["value"]
    d["unit"] = k.unit if k else ""
    return d


def next_batch(limit: int = 4) -> list[dict]:
    """What the gateway should transmit now. Marks the rows sent."""
    t = now()
    out = []
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vehicle_commands WHERE status IN ('queued','sent') ORDER BY id").fetchall()
        rows = sorted(rows, key=lambda r: (0 if _urgent(r["key"], r["value"]) else 1, r["id"]))
        for r in rows:
            command = r["key"] in vcu_config.COMMAND_KEYS
            if r["status"] == "sent" and (r["last_sent_at"] or 0) > t - (COMMAND_RESEND_S if command else RESEND_S):
                continue
            if r["attempts"] >= (COMMAND_MAX_ATTEMPTS if command else MAX_ATTEMPTS):
                conn.execute("UPDATE vehicle_commands SET status = 'expired' WHERE id = ?", (r["id"],))
                continue
            conn.execute("UPDATE vehicle_commands SET status = 'sent', attempts = attempts + 1, last_sent_at = ? "
                         "WHERE id = ?", (t, r["id"]))
            out.append({"id": r["id"], "key": r["key"], "value": r["value"]})
            if len(out) >= limit:
                break
    return out


def process_acks(acks: list[dict] | None) -> list[int]:
    """Close the newest open command for each ACKed key. Returns touched ids."""
    touched = []
    for a in acks or []:
        try:
            key, status, value = int(a["key"]), int(a["status"]), int(a["value"])
        except (KeyError, TypeError, ValueError):
            continue
        name = vcu_config.STATUS_NAMES[status] if 0 <= status < len(vcu_config.STATUS_NAMES) else f"status_{status}"
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM vehicle_commands WHERE key = ? AND status IN ('queued','sent') "
                               "ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            if row is None:
                continue
            conn.execute("UPDATE vehicle_commands SET status = ?, acked_at = ?, ack_value = ? WHERE id = ?",
                         (name, now(), value, row["id"]))
            touched.append(row["id"])
    return touched


def reconcile_with_report(reported: dict | None) -> list[int]:
    """If an ACK was lost but the VCU's CONFIG_REPORT already shows the new value, the
    change IS on the car. Staged rows become applied once the report shows the value
    and nothing is pending any more."""
    if not reported:
        return []
    touched = []
    pending = bool(reported.get("pending"))
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vehicle_commands WHERE status IN ('sent','staged_next_lap')").fetchall()
        for r in rows:
            k = vcu_config.KEYS.get(r["key"])
            if k is None or k.name not in reported:
                continue
            if int(reported[k.name]) == r["value"] and (r["status"] == "sent" or not pending):
                conn.execute("UPDATE vehicle_commands SET status = 'applied', acked_at = COALESCE(acked_at, ?) "
                             "WHERE id = ?", (now(), r["id"]))
                touched.append(r["id"])
    return touched


def recent(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vehicle_commands ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_out(r) for r in rows]


def for_suggestion(sid: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vehicle_commands WHERE suggestion_id = ? ORDER BY id", (sid,)).fetchall()
    return [_out(r) for r in rows]
