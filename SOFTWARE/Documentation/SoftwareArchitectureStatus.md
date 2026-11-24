# Software Architecture Status — Stage 2

Stage-1 snapshot: `Stage1_Archive/SoftwareArchitectureStatus.md`.

Status key: **IMPLEMENTED** (real working software) · **SIMULATED** (working software, synthetic data) · **HARDWARE-DEPENDENT** (driver/interface written; real behaviour needs hardware that has not been connected) · **MISSING**.

| # | Subsystem | Status | Notes |
|---|---|---|---|
| 1 | VCU firmware (ESP32-C6) | HARDWARE-DEPENDENT | Written in Stage 2 (was an empty file). Compiles with 0 warnings; logic unit-tested on a PC; never run on the board |
| 2 | Vehicle gateway (Pi) | HARDWARE-DEPENDENT | Real SocketCAN and SX1278 backends; tested on virtual bus / UDP substitutes |
| 3 | Ground bridge + radio firmware | HARDWARE-DEPENDENT | Real serial/SX1278 path; UDP substitute tested |
| 4 | Driver input (`drive_client.py`) | HARDWARE-DEPENDENT | Codec and forwarding tested; needs a gamepad and the car |
| 5 | Frontend (Next.js 16 / React 19) | IMPLEMENTED | All dashboard pages present; `tsc` clean, ESLint 0 errors |
| 6 | FastAPI backend | IMPLEMENTED | Sessions, frames, faults, config, AI and camera endpoints, `/ws/telemetry`, `/ws/camera-signal` |
| 7 | SQLite persistence | IMPLEMENTED | `CREATE TABLE IF NOT EXISTS` only; restarts never drop data |
| 8 | Telemetry state (`TelemetryContext`) | IMPLEMENTED | Exactly one writer at a time (simulator / socket / replay), gated on mode |
| 9 | WebSocket `/ws/telemetry` | IMPLEMENTED (round trip) / HARDWARE-DEPENDENT (real payload) | With the car absent, LIVE mode still falls back to the browser simulator and says so ("Connected · Simulated Live Fallback") |
| 10 | Simulation mode | IMPLEMENTED | Deterministic waveform; the gateway has a matching `--source sample` |
| 11 | Sessions / Replay / Analytics | IMPLEMENTED | Replay reads recorded frames; analytics compute from real rows, missing data shows `N/A` |
| 12 | Digital Twin | IMPLEMENTED (consumer) | Reads the shared context; four independent aero channels |
| 13 | Diagnostics page | IMPLEMENTED | Stage-2 fault codes (`STR-0x0A`, `AERO-0x0B`, `AI-0x0C`, `COM-0x0D`, `SAFE-0x0E`, `SAFE-0x0F`) flow through unchanged — fault payloads are open, so no code change was needed |
| 14 | AI engine (`backend/app/ai_engine.py`) | IMPLEMENTED, rule-based | Deterministic, not machine learning. `/api/ai/status` reports `SIMULATED`. The Pi/bridge fetch it; they never compute one |
| 15 | AI vs actuator separation | IMPLEMENTED | Recommendation, VCU-accepted target and measured angle are separate fields end to end |
| 16 | Camera | IMPLEMENTED (viewer) / HARDWARE-DEPENDENT (feed) | ESP32-CAM MJPEG is the default source, phone WebRTC retained; "connected" only once frames arrive |
| 17 | Computer vision / OpenCV | MISSING | No pipeline. The AI knows video exists (`videoAvailable`) but never reads it (`videoUsed` stays false) |
| 18 | Track map positioning | SIMULATED | Derived from lap distance; no GPS or vision in the BOM. Labelled as simulated |
| 19 | Firmware / OTA pages | SIMULATED | Local stubs; no device flashing from the dashboard |
| 20 | On-vehicle logging | HARDWARE-DEPENDENT | Full-rate CSV on the Pi microSD (new in Stage 2) |
| 21 | Derived metrics (gateway + VCU) | HARDWARE-DEPENDENT | Distance, wheel rpm, power, energy, Wh/km, coulomb SoC, pack resistance, wheel slip, seconds-to-temperature-limit. `rpm`/`distance`/`wheelAngle` are no longer hard-coded 0 |
| 22 | Offline analysis (`SOFTWARE/analysis/`) | IMPLEMENTED | Friction circle, understeer gradient, coast-down CdA, lateral-g vs speed, baseline-vs-AI aero comparison, lap detection, dead reckoning, thermal fit, servo identification, sensor residuals, digital-twin error. 25 tests against known physics |
| 23 | Measured energy in the AI response | IMPLEMENTED | `energyPerLap`/`predictedRangeLaps` integrated from recorded telemetry, or `null` with `energyMeasured: false`. The old invented formulas are gone |

## Mode ownership (unchanged)

```
mode === 'simulation' → simulator interval owns frame/ai/diagnostics
mode === 'live'       → WebSocket client owns them
mode === 'replay'     → ReplayEngine owns them; the simulation interval refuses to run
```

## Honesty rules the code follows

- A green "connected" badge only appears when data is actually arriving (CAN frame < 1 s, LoRa uplink < 2 s, MJPEG frame loaded, WebRTC connected).
- The gateway withholds telemetry from the backend when the VCU link is stale instead of publishing frozen values.
- Nothing labels the rule-based engine as machine learning. Values the hardware cannot produce are absent rather than invented: energy figures are `null` until measured, and the derived panel shows an em dash when the vehicle is not connected.

## Remaining work

Hardware bring-up (`BringUpGuide.md`), calibration (`CalibrationPlan.md`), a second SX1278 for LoRa, and — if you want the "AI" claim to grow beyond a rule set — a trained model or a vision pipeline at the ground station, still behind the same VCU validation.
