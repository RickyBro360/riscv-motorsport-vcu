from __future__ import annotations

import time

from fastapi import APIRouter

from app.ai_engine import ai_unavailable_response, generate_recommendation
from app.ai_engineer import live_aero_recommendation, live_track_mode_recommendation, station_settings
from app.camera_relay import relay as camera_relay
from app.camera_signal import camera_state
from app.routers.telemetry import get_latest_telemetry
from app.services.energy import energy_context, session_energy

router = APIRouter(prefix="/api", tags=["ai", "camera"])


def _video_available() -> bool:
    """FW 2.2: the ESP32-CAM relay counts as a video source (it is the camera actually on the car);
    the older phone/WebRTC path still does too."""
    return bool(camera_relay.connected) or camera_state.phone_connected

# Per the Racing Engineer AI layer brief: the AI/perception/analysis system
# lives HERE, at the Racing Engineer Station (this backend) — explicitly NOT
# on the Raspberry Pi. See app/ai_engine.py for the full architecture note
# and HARDWARE/Ground Station/bridge.py for how a recommendation this router
# produces travels back down to the Pi -> ESP32 -> VCU.


@router.get("/ai/status")
def ai_status() -> dict:
    return {
        "mode": "SIMULATED",
        "computeLocation": "racing_engineer_station",
        "source": "deterministic rule-based engine (app/ai_engine.py)",
        "modelLoaded": False,
        "raspberryPiConnected": False,
        "inferenceLatencyMs": None,
        "note": (
            "AI/perception/analysis runs at the Racing Engineer Station (this backend), "
            "not on the Raspberry Pi — the Pi only relays telemetry up and forwards the "
            "resulting recommendation down to the ESP32/VCU for validation. This is a "
            "deterministic simulated engine, not a trained model; raspberryPiConnected "
            "reflects whether a Pi gateway is currently relaying telemetry, not whether "
            "it is doing any AI work (it never does)."
        ),
    }


@router.get("/ai/recommendation")
def ai_recommendation() -> dict:
    """The Racing Engineer Station's current recommendation, computed
    fresh from the latest recorded telemetry frame. This is what
    HARDWARE/Ground Station/bridge.py polls to get the recommendation it
    forwards down the radio substitute to the Pi, and what the dashboard's
    AI Engineer page polls to display it. If AI stops or no telemetry has
    arrived yet, this returns a clear 'unavailable' shape (never a 500,
    never a fabricated/stale value) — the vehicle keeps operating either
    way; the VCU simply has nothing new to validate.
    """
    latest = get_latest_telemetry()
    if latest is None or latest.get("frame") is None:
        return ai_unavailable_response()

    # Stage 2d: while the car is running a circuit, the recommendation is the
    # position-aware look-ahead (app/ai_engineer.py). Otherwise the Stage-2 engine.
    try:
        live = live_aero_recommendation(latest["frame"], lookahead_s=station_settings().get("lookaheadS"))
    except Exception:  # never let the new path take the old one down
        live = None
    source = "engineer_station_track_lookahead_v1"
    if live is None:
        # FW 2.2: TRACK mode, driver in DRIVE — anticipation from the driver's steering / yaw
        try:
            live = live_track_mode_recommendation(latest["frame"])
        except Exception:
            live = None
        source = "engineer_station_track_mode_v1"
    if live is not None:
        now_ms = time.time() * 1000.0
        live.update({"available": True, "sessionId": latest.get("sessionId"), "timestamp": now_ms,
                     "validityMs": 500, "validUntil": now_ms + 500, "source": source,
                     "videoAvailable": _video_available(), "videoUsed": False, "warnings": []})
        return live

    try:
        rec = generate_recommendation(
            latest["frame"],
            video_available=_video_available(),
            now_ms=time.time() * 1000.0,
            energy_context=energy_context(latest.get("sessionId")),
        )
    except Exception as exc:  # AI must never take the backend down
        resp = ai_unavailable_response()
        resp["reason"] = f"AI engine error — treated as unavailable: {exc}"
        return resp

    rec["available"] = True
    rec["sessionId"] = latest.get("sessionId")
    return rec


@router.get("/ai/energy")
def ai_energy(session_id: str | None = None) -> dict:
    """Measured energy for a session: integrated pack energy, consumption and
    state of charge. Returns available=false rather than an estimate when the
    session has not recorded enough movement (see app/services/energy.py)."""
    if session_id is None:
        latest = get_latest_telemetry()
        session_id = latest.get("sessionId") if latest else None
    if not session_id:
        return {"available": False, "reason": "no session with recorded telemetry yet"}
    stats = session_energy(session_id)
    stats["sessionId"] = session_id
    return stats


@router.get("/camera/status")
def camera_status() -> dict:
    # The production camera is an on-board smartphone streaming WebRTC
    # peer-to-peer to the dashboard (see app/camera_signal.py, /ws/camera-signal).
    # Video never touches this backend, so we can only honestly report
    # whether a phone publisher socket is currently open — not resolution/fps,
    # which the backend never sees. connected flips true/false in real time
    # as an actual phone connects/disconnects; nothing here is faked.
    if camera_relay.connected:   # FW 2.2: the on-car ESP32-CAM through the backend relay
        st = camera_relay.status()
        return {"mode": "LIVE", "connected": True, "resolution": None, "fps": st["fps"], "latencyMs": None,
                "recording": st["recording"] is not None, "source": "esp32-cam relay", "url": st["url"],
                "note": "ESP32-CAM on the car, relayed and recorded by this backend (see /api/camera/relay)."}
    connected = camera_state.phone_connected
    return {
        "mode": "SIMULATED" if not connected else "LIVE",
        "connected": connected,
        "resolution": None,
        "fps": None,
        "latencyMs": None,
        "recording": False,
        "source": "webrtc",
        "note": (
            "No on-board phone connected yet. Open /camera/broadcast on the "
            "phone to start the live WebRTC stream (see app/camera_signal.py). "
            "When connected, its video is available to the AI layer as a raw "
            "signal only — no computer-vision pipeline reads it yet (see "
            "app/ai_engine.py's videoAvailable/videoUsed fields)."
            if not connected
            else "Live WebRTC stream from the on-board phone. Video is "
            "peer-to-peer to the dashboard; this backend only relays signaling. "
            "The AI layer knows a phone is connected (videoAvailable) but does "
            "not yet run any computer vision on it (videoUsed stays false)."
        ),
    }
