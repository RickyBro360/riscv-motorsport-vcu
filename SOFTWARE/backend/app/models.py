"""
Pydantic schemas for the API.

These deliberately mirror racing-engineer-suite/types/telemetry.ts in
naming so the two stay conceptually in sync, but they are intentionally
NOT a rigid 1:1 field-for-field re-declaration of TelemetryFrame /
AiRecommendation / SystemDiagnostics. The frontend's own
TelemetryParser.parse() only validates `timestamp` and `speed` before
accepting a frame (see services/telemetry-stack.ts) — everything else
is treated as an opaque, forward-compatible payload. This backend
follows the same contract on purpose: TelemetryFrame/AiRecommendation/
SystemDiagnostics come across the wire as validated-but-flexible dicts
rather than closed schemas, so a firmware/AI field addition on the
ESP32-C6 or Raspberry Pi side later doesn't require a backend schema
migration to keep recording data. Session/Fault/Config records — the
things this backend actually owns — ARE fully-typed below.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SessionMode = Literal["practice", "qualifying", "race", "testing"]
SessionStatus = Literal["idle", "running", "paused", "completed", "aborted"]
FaultLevel = Literal["ok", "warning", "critical"]


CURRENT_TELEMETRY_SCHEMA_VERSION = 1


class TelemetryFramePayload(BaseModel):
    """Validated-but-open telemetry frame, matching TelemetryParser.parse().

    `schemaVersion` mirrors types/telemetry.ts's TELEMETRY_SCHEMA_VERSION. It
    defaults to 1 rather than being required so that (a) any gateway/sender
    that predates this field still validates, and (b) old recorded sessions
    replay without a migration step. See Documentation/TelemetryProtocol.md
    for the full field table and the versioning policy: a breaking field
    change gets a new schemaVersion and a new TelemetryFrameV{N} type
    alongside this one, never a silent redefinition of V1.
    """

    schemaVersion: int = CURRENT_TELEMETRY_SCHEMA_VERSION
    timestamp: float
    speed: float

    model_config = {"extra": "allow"}


# Explicit alias so backend code/docs can name the exact wire version,
# matching the locked spec's "TelemetryFrameV1 / TelemetryFrameV2" example.
TelemetryFrameV1 = TelemetryFramePayload


class FaultPayload(BaseModel):
    id: Optional[str] = None
    code: str
    message: str
    level: FaultLevel
    subsystem: str
    timestamp: float


class TelemetrySnapshotIn(BaseModel):
    """One recorded sample: what services/telemetry-stack.ts calls a TelemetrySnapshot."""

    frame: TelemetryFramePayload
    ai: Optional[dict[str, Any]] = None
    diagnostics: Optional[dict[str, Any]] = None
    faults: list[FaultPayload] = Field(default_factory=list)


class FrameBatchIn(BaseModel):
    frames: list[TelemetrySnapshotIn]


class SessionCreate(BaseModel):
    mode: SessionMode = "practice"
    track: Optional[str] = None


class SessionOut(BaseModel):
    id: str
    mode: SessionMode
    status: SessionStatus
    track: Optional[str]
    createdAt: float
    startedAt: Optional[float]
    endedAt: Optional[float]
    lapCount: int
    bestLapS: Optional[float]
    frameCount: int = 0


class SessionDetailOut(SessionOut):
    stats: dict[str, Any] = Field(default_factory=dict)


class VehicleSetup(BaseModel):
    """Mirrors types/telemetry.ts VehicleSetup. Kept fully typed since
    this is a form the Vehicle Setup page persists and validates."""

    wingAngle: float
    adaptiveAero: bool
    brakeBias: float
    steeringRate: float
    ersMap: float
    regen: float
    tractionControl: bool
    steeringPid: dict[str, float]
    motorPid: dict[str, float]
    steeringCenterUs: int
    steeringMinUs: int
    steeringMaxUs: int
    wingCenterUs: int
    canBitrateKbps: int
    loraSpreadingFactor: int
    telemetryRateHz: int
    cellCount: int
    capacityMah: int
    cutoffVoltage: float

    @field_validator("telemetryRateHz")
    @classmethod
    def rate_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("telemetryRateHz must be positive")
        return v


class ConfigurationOut(BaseModel):
    setup: VehicleSetup
    version: int
    updatedAt: float
