# Telemetry Protocol

Status key used throughout this document:
`IMPLEMENTED` — code exists and is exercised by tests.
`SIMULATED` — code works via `SimulationTelemetrySource`, no hardware involved.
`HARDWARE-DEPENDENT` — code/interface exists but requires the ESP32-C6 VCU / Raspberry Pi 5 to produce real values.
`NOT YET VALIDATED` — nothing has been experimentally verified on physical hardware.

## 1. Source of truth

The canonical schema is `racing-engineer-suite/types/telemetry.ts` (`TelemetryFrame`,
`AiRecommendation`, `SystemDiagnostics`, `Fault`). The FastAPI backend
(`backend/app/models.py: TelemetryFramePayload`) deliberately does **not**
re-declare every field as a closed schema — it validates only
`schemaVersion`, `timestamp`, `speed` and accepts the rest as an open,
forward-compatible payload (`extra = "allow"`). This is intentional, not an
oversight: it means a firmware/AI field addition on the ESP32-C6 or
Raspberry Pi side does not require a backend migration to keep recording
data. `SessionOut`, `FaultPayload`, `VehicleSetup`, `ConfigurationOut` — the
records the backend actually owns and enforces — are fully typed.

Status: **IMPLEMENTED** (backend + frontend), enforced by
`backend/tests/` and `tsc --noEmit`.

## 2. Versioning policy

Every telemetry frame carries `schemaVersion` (currently `1`, exported as
`TELEMETRY_SCHEMA_VERSION` in TS / `CURRENT_TELEMETRY_SCHEMA_VERSION` in
Python). Rule: a field addition that a reader can safely ignore does **not**
require a version bump. A field whose meaning, unit, or presence changes in
a way an old reader would misinterpret **does** — at which point a new
`TelemetryFrameV{N}` type is added alongside `TelemetryFrameV1`, not in
place of it, and the recorder/replay path must keep reading both. Frames
recorded before `schemaVersion` existed have no such field on disk; both
the frontend parser (`TelemetryParser.parse`) and the backend model default
a missing value to `1` rather than rejecting the frame.

Status: **IMPLEMENTED**.

## 3. Frame contents (`TelemetryFrameV1`)

| Field | Unit | Group |
|---|---|---|
| schemaVersion | — | meta |
| timestamp | ms (epoch) | meta |
| speed | km/h | vehicle |
| steering | deg (-30..30) | vehicle |
| imu.roll / imu.pitch / imu.yaw | deg | vehicle |
| gForceLat / gForceLong | g | vehicle |
| wheelAngle | rad (accumulated) | vehicle |
| distance | m into lap | vehicle |
| rpm | rpm | vehicle |
| throttle / brake | % | vehicle |
| voltage | V (pack) | powertrain |
| current | A (pack) | powertrain |
| motorCurrent | A | powertrain |
| motorTemp / batteryTemp | °C | powertrain |
| battery | % SOC | powertrain |
| wing.{frontLeft,frontRight,rearLeft,rearRight} | deg | aero (actual/measured) |
| wingTarget.{frontLeft,frontRight,rearLeft,rearRight} | deg | aero (VCU-accepted setpoint) |
| vcuState | enum: POWER_ON / INITIALIZATION / SENSOR_CHECK / IDLE / DRIVE / FAULT / EMERGENCY_STOP | health |

`AiRecommendation` (separate message, keyed to a frame by timestamp, not
merged into it): `corner`, `grip`, `recommendedWing`, `recommendedSpeed`,
`confidence`, `efficiency`, `racingLine`, `note`, `energyPerLap`,
`predictedRangeLaps`, `warnings[]`.

`SystemDiagnostics` (separate message): `can`, `lora` connection status,
`packetLoss`, `cpu`, `memory`, `watchdog`, `ping`, `packetRate`,
`refreshRate`, `uptimeS`, `temperatureC`, `canNodes[]`, `loraLink`.

Status: fields — **IMPLEMENTED** as a schema; values — **SIMULATED** (no
VCU/Pi exists yet to source real ones). See `services/telemetry-simulator.ts`
for how simulated values are derived — it targets physically coherent
relationships (e.g. current draw following speed/throttle) rather than
independent random numbers per field.

## 4. Transport today vs. target

**Today (IMPLEMENTED, SIMULATED data):** browser tab runs
`SimulationTelemetrySource` → sends `{"type":"frame","sessionId","snapshot"}`
over `/ws/telemetry` → FastAPI validates via `TelemetrySnapshotIn` →
`record_batch()` writes to SQLite → broadcast to every connected socket
(including the sender, so a single tab can prove the round trip happened).

**Target (HARDWARE-DEPENDENT, NOT YET VALIDATED):** ESP32-C6 VCU produces
the frame from real sensors → CAN → Raspberry Pi 5 telemetry gateway process
→ same `/ws/telemetry` message shape over WiFi → same FastAPI path. No
backend or dashboard code changes are expected for this transition — the
gateway just needs to speak the message shape above.

## 5. What is explicitly out of scope of this document

CAN bus message IDs/byte layout and LoRa framing are a separate protocol
layer (vehicle↔VCU and vehicle↔ground respectively) and are documented in
`CANProtocol.md` / `LoRaProtocol.md` once those subsystems exist — neither
has been implemented yet as of this revision.
