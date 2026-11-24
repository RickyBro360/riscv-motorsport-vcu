from __future__ import annotations

"""
Firmware / OTA development interface (Phase 14 of the project spec).

There is no ESP32-C6, ESC, BMS, or aero-servo hardware connected to this
backend. Every device and job below is an in-memory, process-local
simulation for building the dashboard UX against a stable contract. It
MUST NOT be presented to a user as a real flash/update having occurred.

When real hardware exists, this module is where the CAN/UART bridge that
actually flashes firmware would report in — the response shapes are
designed to stay stable across that swap.
"""

import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/firmware", tags=["firmware", "ota"])

DeviceStatus = Literal["up-to-date", "update-available", "updating", "offline"]


class FirmwareDevice(BaseModel):
    id: str
    name: str
    chip: str
    current: str
    latest: str
    status: DeviceStatus
    connected: bool = False  # always False until real hardware reports in


class OtaJob(BaseModel):
    id: str
    device: str
    version: str
    startedAt: float
    status: Literal["success", "failed", "rolled-back"]
    durationS: float
    simulated: bool = True


# Process-local, resets on backend restart — intentionally not persisted to
# SQLite, since this is dev-simulation state, not real fleet history.
_devices: dict[str, FirmwareDevice] = {
    "vcu": FirmwareDevice(id="vcu", name="VCU Core", chip="RISC-V RV32 (ESP32-C6, not yet connected)", current="v2.4.1", latest="v2.4.1", status="offline"),
    "esc": FirmwareDevice(id="esc", name="Motor ESC", chip="STM32G4 (not yet connected)", current="v1.9.3", latest="v2.0.0", status="update-available"),
    "bms": FirmwareDevice(id="bms", name="BMS Gateway", chip="ESP32-S3 (not yet connected)", current="v1.6.0", latest="v1.6.0", status="offline"),
    "aero": FirmwareDevice(id="aero", name="Aero Servo Ctrl", chip="RP2040 (not yet connected)", current="v0.8.4", latest="v0.8.4", status="offline"),
}
_jobs: list[OtaJob] = []


@router.get("/devices")
def list_devices() -> dict:
    return {
        "mode": "SIMULATED",
        "note": "No physical controller boards are connected. Statuses reflect a dev-only in-memory model, not a live CAN bus scan.",
        "devices": list(_devices.values()),
    }


@router.post("/devices/{device_id}/stage")
def stage_update(device_id: str) -> dict:
    device = _devices.get(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="unknown device id")
    if device.status != "update-available":
        raise HTTPException(status_code=400, detail="no update staged for this device")
    job = OtaJob(
        id=f"OTA-SIM-{int(time.time())}",
        device=device.name,
        version=device.latest,
        startedAt=time.time(),
        status="success",
        durationS=0.0,
        simulated=True,
    )
    _jobs.insert(0, job)
    _devices[device_id] = device.model_copy(update={"current": device.latest, "status": "up-to-date"})
    return {"mode": "SIMULATED", "job": job, "note": "No firmware was actually flashed — no hardware is connected."}


@router.get("/jobs")
def list_jobs() -> dict:
    return {"mode": "SIMULATED", "jobs": _jobs, "note": "In-memory dev history only; not a real deployment audit log."}
