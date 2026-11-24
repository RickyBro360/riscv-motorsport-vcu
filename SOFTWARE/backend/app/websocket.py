"""
WebSocket telemetry path: /ws/telemetry

Two things share this one socket, matching the locked architecture's
"Raspberry Pi -> FastAPI -> WebSocket -> dashboard" flow:

1. INBOUND (gateway -> backend): the Raspberry Pi telemetry gateway
   (HARDWARE/raspberry_pi/main.py) sends
   {"type": "frame", "sessionId": "...", "snapshot": {...TelemetrySnapshotIn}}
   to record a frame and have it fanned out to every connected viewer in
   real time. During development the Pi's own sample-value generator
   (main.py's SampleTelemetrySource, --source sample) originates the
   values; later the same message shape carries real ESP32-C6/CAN-sourced
   telemetry the Pi has forwarded, with no change required on this end.

2. OUTBOUND (backend -> browser): every accepted frame is immediately
   rebroadcast to all other connected sockets, so a second dashboard tab
   (or a race engineer's laptop) sees live telemetry without polling.

The backend never originates fake "live" data on this socket — if no
gateway is connected, nothing is broadcast. The Next.js dashboard no
longer sends frames on this socket either (see
racing-engineer-suite/contexts/telemetry-context.tsx) — it is a pure
consumer in LIVE mode. Only the Raspberry Pi gateway is a valid sender.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models import TelemetrySnapshotIn
from app.services.recording import record_batch

router = APIRouter()
logger = logging.getLogger("telemetry.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict, exclude: WebSocket | None = None) -> None:
        # FW 2.3 (dashboard lag): serialise ONCE and send to every client CONCURRENTLY. Before,
        # each client was awaited in turn (up to 0.5 s each), so one slow tab or a second laptop
        # on weak Wi-Fi delayed every frame for everyone.
        text = json.dumps(message, separators=(",", ":"))
        targets = [c for c in list(self.active) if c is not exclude]

        async def _send(conn: WebSocket) -> WebSocket | None:
            try:
                # Stage 2c: bounded, so one stuck client (e.g. a gateway that
                # stopped reading) can never stall telemetry for everyone else.
                await asyncio.wait_for(conn.send_text(text), timeout=0.5)
                return None
            except Exception:
                return conn

        for dead in await asyncio.gather(*(_send(c) for c in targets)):
            if dead is not None:
                self.disconnect(dead)


manager = ConnectionManager()


@router.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await websocket.send_json({"type": "connected", "activeViewers": len(manager.active)})
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            if message.get("type") != "frame":
                await websocket.send_json({"type": "error", "detail": "Unknown message type"})
                continue

            session_id = message.get("sessionId")
            if not session_id:
                await websocket.send_json({"type": "error", "detail": "sessionId is required"})
                continue

            try:
                snapshot = TelemetrySnapshotIn.model_validate(message.get("snapshot", {}))
                # FW 2.3 (dashboard lag): the SQLite write runs in a worker thread. It used to run ON
                # the event loop, so every frame froze the WebSocket fan-out and every REST call the
                # dashboard was waiting on for the length of a database commit.
                await asyncio.to_thread(record_batch, session_id, [snapshot])
            except ValueError as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})
                continue
            except Exception as exc:  # malformed snapshot payload
                await websocket.send_json({"type": "error", "detail": f"Invalid snapshot: {exc}"})
                continue

            # Broadcast to EVERY connected socket, including the sender.
            # The sender is normally the Raspberry Pi gateway, not a
            # browser tab — echoing back to it is harmless (the gateway
            # doesn't render anything) and keeps a single dashboard tab
            # and a multi-viewer setup on exactly the same code path: the
            # backend is the single source of truth for what "live" means,
            # for one viewer or many.
            await manager.broadcast(
                {"type": "frame", "sessionId": session_id, "snapshot": message.get("snapshot")}
            )
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass  # socket already closed underneath us (dropped by broadcast); nothing to do
    finally:
        manager.disconnect(websocket)
