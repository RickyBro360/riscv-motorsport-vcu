# Telemetry Protocol (dashboard / backend layer)

Stage 2. Stage-1 version: `Stage1_Archive/TelemetryProtocol.md`.

This document covers the JSON layer between the vehicle gateway (or ground bridge) and the laptop software. The byte-level vehicle protocols are in `CANProtocol.md` and `LoRaProtocol.md`.

Status key: `IMPLEMENTED` — code exists and is tested · `SIMULATED` — works with synthetic data · `HARDWARE-DEPENDENT` — needs the physical vehicle · `NOT YET VALIDATED` — not verified on hardware.

## 1. Source of truth

The canonical schema is `SOFTWARE/dashboard/types/telemetry.ts` (`TelemetryFrame`, `AiRecommendation`, `SystemDiagnostics`, `Fault`). The FastAPI backend (`backend/app/models.py`, `TelemetryFramePayload`) validates only `schemaVersion`, `timestamp` and `speed` and accepts every other key (`extra = "allow"`). This is intentional: the gateway can add fields without a backend migration. `SessionOut`, `FaultPayload`, `VehicleSetup` and `ConfigurationOut` are fully typed. **IMPLEMENTED**.

## 2. Versioning

Every frame carries `schemaVersion` (currently `1`). A field a reader can safely ignore does not bump the version; a change of meaning, unit or presence does, and a `TelemetryFrameV{N}` type is added alongside V1. Frames recorded without `schemaVersion` are read as V1. Stage 2 added only optional keys, so the version stays 1. **IMPLEMENTED**.

## 3. WebSocket message

Endpoint `ws://<laptop>:8000/ws/telemetry`. One message per frame:

```json
{ "type": "frame", "sessionId": "<id from POST /api/sessions>",
  "snapshot": { "frame": { ... }, "ai": null, "diagnostics": { ... }, "faults": [ ... ] } }
```

The backend stores the snapshot in SQLite and broadcasts it to every connected dashboard. Senders create their own session with `POST /api/sessions {"mode":"practice"}` unless given `--session-id`. `ai` is always `null` from the vehicle side: the recommendation is computed at the backend and fetched separately.

## 4. `frame` fields and where each value comes from on the real car

| Field | Unit | Source (MODE A: CAN via Pi) | MODE B (LoRa) |
|---|---|---|---|
| `schemaVersion` | — | 1 | 1 |
| `timestamp` | ms | **ms since the Pi gateway started** (not epoch) | same, from the LoRa header |
| `speed` | km/h | mean of four Hall wheel speeds (0x110) | yes |
| `steering` | deg | steering servo feedback if calibrated, else command (0x115) | yes |
| `imu.roll/pitch/yaw` | deg | complementary filter on MPU6050 (0x122); yaw drifts | yes |
| `gForceLat` / `gForceLong` | g | MPU6050 Y (left +) / X (forward +) (0x120) | yes |
| `throttle` / `brake` | % | motor duty / braking flag (0x115) | yes |
| `voltage` | V | ADS1115 AIN0 through the divider (0x130) | yes |
| `current` | A | INA260 (0x130) | yes |
| `motorCurrent` | A | = pack current (0x140) — no motor sensor in the BOM | yes |
| `motorTemp` / `batteryTemp` | °C | DS18B20 assigned roles (0x140); 0 if absent | yes |
| `battery` | % SOC | **estimate** from voltage (linear 9.9–12.6 V, 3S) — not coulomb counting | yes |
| `wing.*` | deg | MG996R pot feedback (0x150); target copied if feedback not yet calibrated | yes (measured only) |
| `wingTarget.*` | deg | VCU rate-limited setpoint (0x151) | **= `wing`** (not carried over LoRa) |
| `vcuState` | enum | 0x100 state, mapped per `CANProtocol.md` §4 | yes |
| `rpm` | rpm | **wheel** rpm from wheel speed and diameter (no motor rpm sensor) | yes |
| `distance` | m | integrated ground distance this run (gateway) | not carried |
| `wheelAngle` | rad | accumulated wheel rotation (gateway) | not carried |
| `derived.*` | — | power, energy, Wh/km, coulomb SoC, pack resistance, wheel slip, seconds-to-temperature-limit — see `DerivedMetrics.md` | not carried |

**Stage-2 state:** the adapter is IMPLEMENTED and tested against the backend (`test_mode_a_live_recommendation.py`); values are HARDWARE-DEPENDENT and NOT YET VALIDATED.

## 4b. Stage 2d keys (additive, `schemaVersion` stays 1)

| Field | Unit | Source | MODE B (LoRa v3) |
|---|---|---|---|
| `wheels.{fl,fr,rl,rr}` | km/h | hall sensors (0x110) | yes |
| `gyroZ` | deg/s | MPU6050 (0x121) | yes |
| `vibrationMg` | mg RMS | VCU VibrationMeter (0x141 bytes 6–7 / 0x147) | yes |
| `odometerM` | m | VCU rear-wheel odometry (0x147) | `null` |
| `aeroMode`, `lastAiReason` | — | 0x171 | yes |
| `track.{id,pos,lap,currentLapMs,lastLapMs,sector,runActive,straightMode,brakingZone,configPending,benchRun,stalled}` | — | 0x145 / 0x146 | yes |
| `track.profileDutyPct`, `track.latGModel` | %, g | 0x145 | `null` |
| `runState`, `dynoCal` | text | 0x147 | yes |
| `vcuConfig` | {name: value, pending} | 0x182 round robin, accumulated | yes (rebuilt by the bridge) |
| `configAcks` | [{key,status,value}] | 0x181 since the last frame | yes (LoRa type 9) |
| `wingTarget.*` | deg | 0x151 | **yes** (v2 copied `wing`) |
| `demoFeed` | bool | only `backend/tools/demo_car.py` sets it — the dashboard shows "DEMO FEED — not the car" | — |

`speed` is now the undriven FRONT average when the fronts turn, else the driven REAR average (same rule on the VCU and the Pi), so a dyno that only turns the rear wheels still reads a speed.

## 5. `diagnostics`

| Key | MODE A (Pi) | MODE B (bridge) |
|---|---|---|
| `can` | `connected` if a CAN frame arrived < 1 s ago | always `connected` (CAN health arrives in fault bits) |
| `lora` | `disconnected` (no radio in MODE A) | uplink seen < 2 s ago |
| `packetLoss` | 0 / 100 % by link freshness | estimated from uplink sequence gaps |
| `cpu`, `memory`, `temperatureC` | **Pi's own** `/proc/stat`, `/proc/meminfo`, `thermal_zone0` | 0 |
| `ping` | age of the last CAN frame, ms | 0 |
| `packetRate`, `refreshRate` | 20 | measured uplink rate |
| `uptimeS` | VCU uptime from 0x171 | 0 |
| `loraLink.rssi/snr` | from the radio (0 until measured) | from the ground radio bridge |
| extra: `hostMetricsSource` | `linux` or `synthetic-fallback` | — |
| extra: `vcu` | decoded 0x171 (`aeroMode`, `lastAiReason`, `driverLinkOk`, `servoFeedbackCalibratedMask`, `uptimeS`, `loopOverruns`, `protocolVersion`) | — |
| extra: `radioStatsMeasured` | — | true once RSSI has been reported |

## 6. `faults`

One `FaultPayload` per currently-set fault bit (`code`, `message`, `level` ∈ ok/warning/critical, `subsystem`, `timestamp`). Codes: `SAFE-0x01` … `SAFE-0x0F`; full table in `SafetyArchitecture.md` §3. The VCU is the only source of faults; the gateway and bridge only decode bits.

## 7. AI recommendation

`GET /api/ai/recommendation` returns the backend's current `AiRecommendation` computed from the latest stored frame, or `{"available": false, ...}`. `energyPerLap`, `predictedRangeLaps` and `whPerKm` are **measured** by integrating recorded pack voltage × current (`backend/app/services/energy.py`) and are `null` with `energyMeasured: false` when a session has not recorded enough movement — they are never estimated from a formula. `GET /api/ai/energy` exposes the same figures directly. Fields sent to the car: `recommendedWing.{frontLeft,frontRight,rearLeft,rearRight}` and `confidence`. Everything else (`corner`, `grip`, `recommendedSpeed`, `note`, …) is for the dashboard. The engine is deterministic and rule-based (`backend/app/ai_engine.py`).

## 7b. Stage 2d endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/tracks`, `GET /api/tracks/{id}` | circuit library (2026 calendar) |
| `GET /api/ai/engineer` | AI engineer desk: position, inferences, live look-ahead, proposals, history, commands |
| `POST /api/ai/suggestions/{id}/approve` · `/reject` | operator decision; approve queues CONFIG_WRITEs |
| `GET/POST /api/vehicle/config` | VCU tunables (reported values + bounds) / operator write |
| `POST /api/vehicle/track` | select a circuit |
| `GET /api/vehicle/commands` · `/next` | command outbox (dashboard) / what the gateway should transmit now |
| `GET /api/twin` | digital-twin lap and modelled forces at the car's position |
| `GET /api/laps` | per-lap table reconstructed from frames |
| `GET/POST /api/station/settings` | power source (adapter / LiPo), adapter limit, AI look-ahead |
| `/api/camera/*` | ESP32-CAM relay, recording, downloads — see §8 |

`GET /api/ai/recommendation` keeps its shape. While a lap run is active it returns the **position-aware look-ahead** (`positionAware: true`, `posAhead`, `lookaheadS`), otherwise the Stage-2 engine's answer.

## 8. Video

Not carried by LoRa. Stage 2d: the ESP32-CAM serves ONE client, so the backend is that client (`POST /api/camera/source {"url": "http://<cam-ip>:81/stream"}`) and relays the frames to every dashboard at `GET /api/camera/stream`. `POST /api/camera/record/start|stop` records the untouched JPEGs into an MJPEG `.avi` (plus `.mp4` when ffmpeg / imageio-ffmpeg is available); `GET /api/camera/recordings/{name}` downloads it and `/telemetry.csv` gives the telemetry frames received during the same wall-clock window (`telemetry_frames.received_at`). The Stage-1 phone WebRTC path (`/ws/camera-signal`) remains in the backend.

## 9. Transports summary

| Mode | Path | Status |
|---|---|---|
| SIMULATION | dashboard simulator → `/ws/telemetry` | IMPLEMENTED, SIMULATED data |
| MODE A | VCU → CAN → Pi `main.py` → Wi-Fi WebSocket | IMPLEMENTED; HARDWARE-DEPENDENT |
| MODE B | VCU → CAN → Pi → SX1278 → ground bridge firmware → `bridge.py` → WebSocket | IMPLEMENTED; HARDWARE-DEPENDENT |
| Bench | `main.py --source sample --can-interface mock` | IMPLEMENTED |
