from __future__ import annotations

import time

from fastapi import APIRouter

router = APIRouter(tags=["health"])
START_TIME = time.time()


@router.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "racing-engineer-suite-backend",
        "uptimeS": time.time() - START_TIME,
        # This backend is a telemetry gateway/validator, not a data source —
        # "development" reflects that the hardware chain feeding it (Pi
        # gateway, CAN, ESP32-C6) has not yet been validated on real
        # vehicle hardware, not that this service fabricates telemetry.
        "mode": "development",
    }
