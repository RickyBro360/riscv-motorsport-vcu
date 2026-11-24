"""
Camera signaling path: /ws/camera-signal

This is a SIGNALING RELAY ONLY. No video ever passes through this backend —
video goes phone-browser -> Internet -> viewer-browser directly over WebRTC
(peer-to-peer), exactly per the target architecture:

    ON-BOARD PHONE --(local recording)--> phone storage
    ON-BOARD PHONE --(WebRTC, live)-----> Racing Engineer Station --> Dashboard

All this socket does is pass tiny JSON SDP offer/answer + ICE candidate
messages between exactly one "phone" (the publisher) and any number of
"viewer" tabs (the dashboard), so the two sides can find each other and
negotiate a direct peer-to-peer media connection. It never inspects,
transcodes, or stores video, and it carries no telemetry — the CAN/LoRa
telemetry path (app/websocket.py, /ws/telemetry) is completely separate
and untouched by this file.

Connection roles, chosen via query param:
  /ws/camera-signal?role=phone    -- the on-board phone (publisher, at most 1 active)
  /ws/camera-signal?role=viewer   -- a dashboard tab (consumer, any number)

Message shapes relayed verbatim (backend only reads "type" to route them):
  phone -> viewer(s):  {"type": "offer", "sdp": "..."}
  viewer -> phone:      {"type": "answer", "sdp": "..."}
  either direction:     {"type": "ice-candidate", "candidate": {...}}

`camera_state` is the single in-memory source of truth /api/camera/status
(app/routers/ai.py) reads from — it stays "connected: false" until a real
phone publisher socket is actually open, same honesty contract the rest of
this backend already follows for hardware that doesn't exist yet.

KNOWN LIMITATION: the phone maintains exactly one RTCPeerConnection and
negotiates one SDP answer at a time. Multiple simultaneous dashboard
viewers will each trigger their own answer against that same connection,
which is not a supported many-viewer fan-out — fine for the current
single-race-engineer-station use case, not fine for N independent viewers.
A real many-viewer setup would need either one PeerConnection per viewer
on the phone (expensive on a phone's radio/CPU) or a media server (SFU) in
front of the phone — explicitly out of scope for this minimum pass.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger("camera.signal")


class CameraState:
    """Tiny in-memory status the REST /api/camera/status endpoint reads."""

    def __init__(self) -> None:
        self.phone_connected = False
        self.viewer_count = 0


camera_state = CameraState()


class CameraSignalHub:
    def __init__(self) -> None:
        self.phone: WebSocket | None = None
        self.viewers: list[WebSocket] = []
        # Cached so a viewer that joins *after* the phone already published
        # still gets the current offer, instead of waiting forever for one
        # that was already sent to viewers connected earlier.
        self.last_offer: dict | None = None

    async def connect_phone(self, ws: WebSocket) -> None:
        await ws.accept()
        # Only one on-board phone at a time — a second connect replaces the
        # first (e.g. a page refresh), it does not stack publishers.
        old = self.phone
        self.phone = ws
        self.last_offer = None
        camera_state.phone_connected = True
        if old is not None and old is not ws:
            try:
                await old.close()
            except Exception:
                pass
        for viewer in self.viewers:
            await _safe_send(viewer, {"type": "phone-connected"})

    def disconnect_phone(self, ws: WebSocket) -> None:
        if self.phone is ws:
            self.phone = None
            self.last_offer = None
            camera_state.phone_connected = False

    async def connect_viewer(self, ws: WebSocket) -> None:
        await ws.accept()
        self.viewers.append(ws)
        camera_state.viewer_count = len(self.viewers)
        if self.phone is not None:
            await _safe_send(ws, {"type": "phone-connected"})
            if self.last_offer is not None:
                await _safe_send(ws, self.last_offer)
        else:
            await _safe_send(ws, {"type": "phone-disconnected"})

    def disconnect_viewer(self, ws: WebSocket) -> None:
        if ws in self.viewers:
            self.viewers.remove(ws)
        camera_state.viewer_count = len(self.viewers)

    async def relay_from_phone(self, message: dict) -> None:
        if message.get("type") == "offer":
            self.last_offer = message
        for viewer in self.viewers:
            await _safe_send(viewer, message)

    async def relay_from_viewer(self, message: dict) -> None:
        if self.phone is not None:
            await _safe_send(self.phone, message)


async def _safe_send(ws: WebSocket, message: dict) -> None:
    try:
        await ws.send_json(message)
    except Exception:
        pass


hub = CameraSignalHub()

_RELAYABLE_TYPES = {"offer", "answer", "ice-candidate"}


@router.websocket("/ws/camera-signal")
async def camera_signal_socket(websocket: WebSocket, role: str = "viewer") -> None:
    if role not in ("phone", "viewer"):
        await websocket.close(code=4000)
        return

    if role == "phone":
        await hub.connect_phone(websocket)
    else:
        await hub.connect_viewer(websocket)

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict) or message.get("type") not in _RELAYABLE_TYPES:
                continue
            if role == "phone":
                await hub.relay_from_phone(message)
            else:
                await hub.relay_from_viewer(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("camera-signal socket error (role=%s)", role)
    finally:
        if role == "phone":
            hub.disconnect_phone(websocket)
            await hub.relay_from_phone({"type": "phone-disconnected"})
        else:
            hub.disconnect_viewer(websocket)
