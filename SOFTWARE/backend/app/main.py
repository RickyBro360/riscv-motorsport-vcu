from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.camera_relay import relay as camera_relay
from app.camera_relay import router as camera_relay_router
from app.camera_signal import router as camera_signal_router
from app.db import init_db
from app.routers import ai, config, engineer, firmware, health, sessions, telemetry, vehicle
from app.websocket import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cam = os.environ.get("VCU_CAMERA_URL")          # optional: start relaying at boot
    if cam:
        await camera_relay.set_source(cam, origin="env")
    yield
    camera_relay.stop_recording()
    await camera_relay.set_source(None)


from app.ai_command import router as ai_command_router  # noqa: E402
app = FastAPI(
    title="Racing Engineer Suite — Backend",
    description=(
        "Development/simulation backend for the AI-Enabled RISC-V Motorsport "
        "Electronics Platform. Owns session persistence, telemetry recording, "
        "replay data and vehicle configuration. Does NOT talk to real hardware "
        "yet — see /api/ai/status and /api/camera/status, which are explicit "
        "SIMULATED stubs pending ESP32-C6/Raspberry Pi integration."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

_origins = os.environ.get("VCU_CORS_ORIGINS", "http://localhost:3000").split(",")
# Stage 2c: the dashboard is also opened by IP (http://10.49.218.128:3000) and
# from the second laptop. Allow any localhost / private-LAN origin on any port;
# VCU_CORS_ORIGINS still adds explicit extra origins.
_LAN_ORIGIN_REGEX = (
    r"https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=os.environ.get("VCU_CORS_ORIGIN_REGEX", _LAN_ORIGIN_REGEX),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(sessions.router)
app.include_router(telemetry.router)
app.include_router(config.router)
app.include_router(ai.router)
app.include_router(firmware.router)
app.include_router(ws_router)
app.include_router(camera_signal_router)
# Stage 2d
app.include_router(vehicle.router)
app.include_router(engineer.router)
app.include_router(camera_relay_router)
# standalone AI twin page (rc_twin.html): command uplink, OFF unless VCU_AI_COMMAND_LINK=1
app.include_router(ai_command_router)
