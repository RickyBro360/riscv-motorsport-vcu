"""
/ws/ai-command — command uplink for the standalone AI twin page (rc_twin.html, "AI ENGINE" mode).

OFF by default. Start the backend with the environment variable VCU_AI_COMMAND_LINK=1 to enable it.
It is an actuator path: when the page is ARMED, the trained model's motor duty reaches the car.

What it does with each message {"type":"ai_command","duty_pct":..,"straight_mode":..} (5 per second):
  * motor duty  -> CONFIG_WRITE key 9 (duty in permille), the SAME request the dashboard's "Run motor"
                   sends. The VCU still decides: IDLE, no fault, ground link up, its own duty cap.
                   Forwarded only when it changed by >= 1.5 % and at most every 0.4 s (the radio uplink
                   cannot carry 5 commands a second, and a newer value replaces an older one in flight).
  * straight mode -> key 25: 1 when it switches on (the VCU's DRS: flaps flat for 5 s), refreshed every
                   4 s while it stays on, 0 when it switches off.
  * {"estop":true} -> key 8 = 1, immediately.
  * "steer_deg" (+ = LEFT) -> key 27 (FW 2.3.12, tenths of a degree): forwarded on a change of >= 0.5 deg,
                   at most every 0.2 s, and repeated every 0.6 s while unchanged, because the VCU hands
                   the steering back to the knob when no value arrives for 1.5 s.
Safety: if the page stops sending for 1.5 s (disarmed, tab closed, laptop asleep) or the socket closes,
the motor is told to stop (key 9 = 0) and the steering is centred (key 27 = 0, then the knob takes over).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services import vehicle_commands

router = APIRouter()
logger = logging.getLogger("ai.command")

ENABLED = os.environ.get("VCU_AI_COMMAND_LINK", "0") not in ("", "0", "false", "False")
MIN_DUTY_STEP_PCT = 1.5
MIN_INTERVAL_S = 0.4
DRS_REFRESH_S = 4.0
SILENCE_STOP_S = 1.5
KEY_ESTOP, KEY_MOTOR, KEY_DRS, KEY_AI_STEER = 8, 9, 25, 27
MIN_STEER_STEP_DEG = 0.5
MIN_STEER_INTERVAL_S = 0.2
STEER_KEEPALIVE_S = 0.6
STEER_LIMIT_DEG = 45.0


async def _queue(key: int, value: int) -> None:
    await asyncio.to_thread(vehicle_commands.queue, key, value, "ai_engine")


@router.websocket("/ws/ai-command")
async def ai_command_socket(ws: WebSocket) -> None:
    await ws.accept()
    if not ENABLED:
        await ws.send_json({"type": "error", "detail": "AI command link is off. Start the backend with VCU_AI_COMMAND_LINK=1."})
        await ws.close(code=1008)
        return
    await ws.send_json({"type": "connected", "detail": "AI command link open (motor duty, DRS, steering)"})
    st = {"duty": None, "duty_t": 0.0, "drs": False, "drs_t": 0.0, "rx": time.monotonic(), "active": False,
          "steer": None, "steer_t": 0.0}

    async def stop_motor(reason: str) -> None:
        if st["active"] or st["steer"] is not None:      # motor stop is sent even if only the steering was live
            st["active"] = False
            st["duty"] = None
            logger.info("AI command link: motor stop (%s)", reason)
            await _queue(KEY_MOTOR, 0)
            if st["drs"]:
                st["drs"] = False
                await _queue(KEY_DRS, 0)
        if st["steer"] is not None:
            st["steer"] = None
            await _queue(KEY_AI_STEER, 0)

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(0.25)
            if (st["active"] or st["steer"] is not None) and time.monotonic() - st["rx"] > SILENCE_STOP_S:
                await stop_motor("no command for %.1f s" % SILENCE_STOP_S)

    wd = asyncio.create_task(watchdog())
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict) or msg.get("type") != "ai_command":
                continue
            now = time.monotonic()
            st["rx"] = now
            if msg.get("estop"):
                st["active"] = False
                st["duty"] = None
                st["drs"] = False
                st["steer"] = None
                await _queue(KEY_ESTOP, 1)
                continue
            try:
                duty = float(msg.get("duty_pct"))
            except (TypeError, ValueError):
                continue
            if not (duty == duty):        # NaN
                continue
            duty = min(max(duty, 0.0), 100.0)
            changed = st["duty"] is None or abs(duty - st["duty"]) >= MIN_DUTY_STEP_PCT
            if changed and now - st["duty_t"] >= MIN_INTERVAL_S:
                st["duty"], st["duty_t"], st["active"] = duty, now, duty > 0
                await _queue(KEY_MOTOR, int(round(duty * 10)))
            try:
                steer = float(msg.get("steer_deg")) if msg.get("steer_deg") is not None else None
            except (TypeError, ValueError):
                steer = None
            if steer is not None and steer == steer:
                steer = min(max(steer, -STEER_LIMIT_DEG), STEER_LIMIT_DEG)
                moved = st["steer"] is None or abs(steer - st["steer"]) >= MIN_STEER_STEP_DEG
                if (moved and now - st["steer_t"] >= MIN_STEER_INTERVAL_S) or now - st["steer_t"] >= STEER_KEEPALIVE_S:
                    st["steer"], st["steer_t"] = steer, now
                    await _queue(KEY_AI_STEER, int(round(steer * 10)))
            drs = bool(msg.get("straight_mode"))
            if drs and (not st["drs"] or now - st["drs_t"] >= DRS_REFRESH_S):
                st["drs"], st["drs_t"] = True, now
                await _queue(KEY_DRS, 1)
            elif not drs and st["drs"]:
                st["drs"] = False
                await _queue(KEY_DRS, 0)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        wd.cancel()
        # Written directly (not awaited): this must still happen when the handler itself is being
        # cancelled, e.g. the server shutting down with the page still armed.
        if st["active"]:
            st["active"] = False
            logger.info("AI command link: motor stop (command link closed)")
            vehicle_commands.queue(KEY_MOTOR, 0, "ai_engine")
            if st["drs"]:
                vehicle_commands.queue(KEY_DRS, 0, "ai_engine")
        if st["steer"] is not None:
            st["steer"] = None
            vehicle_commands.queue(KEY_AI_STEER, 0, "ai_engine")
