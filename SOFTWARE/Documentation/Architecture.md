# System Architecture

Stage 2 — matches the final BOM. Stage-1 version: `Stage1_Archive/Architecture.md`.
Validation status: software-verified only; not yet run on hardware (`ProjectAudit_Stage2.md`).

## 1. Three computers, three jobs

| Node | Hardware | Job | Code |
|---|---|---|---|
| **VCU** | ESP32-C6 (RISC-V), SN65HVD230 | Reads every sensor, drives every actuator, runs the safety manager and state machine. **Sole actuator authority.** | `HARDWARE/esp32/main_updated_corrected__1_/` (`.ino` + `vcu_logic.h`) |
| **Vehicle gateway** | Raspberry Pi 4B, CAN HAT (MCP2515), SX1278 | Bridges CAN ↔ laptop (Wi-Fi) or CAN ↔ LoRa, forwards driver commands, logs all telemetry to microSD. **Computes nothing that controls the car.** | `HARDWARE/raspberry_pi/main.py` |
| **Ground station** | Laptop (+ ESP32 + second SX1278 for LoRa) | FastAPI backend + SQLite, rule-based AI engine, Next.js dashboard with Digital Twin, driver input, radio bridge | `SOFTWARE/backend`, `SOFTWARE/dashboard`, `HARDWARE/Ground Station/` |

The ESP32-CAM is a fourth, independent device: it streams MJPEG over Wi-Fi straight to the dashboard and is not connected to CAN or LoRa.

## 2. Block diagram

```
                              ON THE CAR                                          │        GROUND STATION (laptop)
                                                                                  │
 MPU6050 ─┐I2C                                                                    │
 INA260  ─┤          ┌───────────────────────────┐                                │   ┌───────────────────────────────┐
 ADS1115 ─┘          │  ESP32-C6  VCU            │  CAN 500 kbit/s                │   │ FastAPI backend (:8000)       │
 DS18B20 ── 1-Wire ─►│  sensor task   100 Hz     │  SN65HVD230                    │   │  /ws/telemetry  SQLite        │
 4× Hall ── ISR ────►│  control task  100 Hz     │◄══════════════╗                │   │  /api/ai/recommendation       │
 5× MG996R pot ─ADC─►│  CAN task      TX 100 Hz  │               ║                │   │  ai_engine.py (rule-based)    │
                     │  safety · state machine   │               ║                │   └──────────▲──────────┬─────────┘
 5× MG996R ◄─ PWM ───│  aero · steering · motor  │               ║                │              │ WebSocket │ HTTP
 BTS7960   ◄─ PWM ───│                           │               ║                │   ┌──────────┴──────────▼─────────┐
                     └───────────────────────────┘               ║                │   │ Next.js dashboard             │
                                                   ┌─────────────╨─────────────┐  │   │ live · digital twin · camera  │
                                                   │ Raspberry Pi 4B gateway   │  │   └──────────▲────────────────────┘
                                                   │  CAN HAT  can0            │  │              │ MJPEG :81/stream
                                                   │  heartbeat 10 Hz ─► VCU   │  │              │
                                                   │  CSV logger (microSD)     │  │   ┌──────────┴───────┐
                                                   │  drive link UDP :5020     │◄─┼───│ drive_client.py  │ gamepad/keyboard
                                                   │                           │  │   └──────────────────┘
                                   MODE A (Wi-Fi) ─│─► WebSocket + HTTP ───────┼──┼──► backend
                                   MODE B (LoRa)  ─│─► SX1278 ~5 Hz ···········┼··┼··► ESP32+SX1278 USB ─► bridge.py ─► backend
                                                   └───────────────────────────┘  │
 ESP32-CAM ─────────────── Wi-Fi MJPEG ───────────────────────────────────────────┼──────────────────────► dashboard
```

## 3. Operating modes of the gateway

| | MODE A — `--ground-link direct` | MODE B — `--ground-link lora` |
|---|---|---|
| Uplink | Pi → WebSocket → backend, 20 Hz | Pi → SX1278 → ground radio bridge → `bridge.py` → backend, **5 Hz** |
| AI recommendation | Pi polls `GET /api/ai/recommendation` at `--ai-hz` (5 Hz) → CAN 0x160/0x161 | `bridge.py` polls the backend, sends one LoRa downlink in the slot after each uplink → Pi → CAN |
| Driver commands | Wi-Fi UDP `drive_client.py` → Pi `:5020` → CAN 0x118 | Same (Wi-Fi). LoRa DRIVER_COMMAND exists but is too slow to drive with. |
| Needs | Laptop and Pi on the same Wi-Fi | Two SX1278 radios; Wi-Fi still needed for driving and video |
| Use | Bench work, short range, highest data rate | Range beyond Wi-Fi; the architecture's target telemetry link |

In both modes the Pi also writes every decoded frame, at full rate, to `logs/vehicle_YYYYmmdd_HHMMSS.csv`.

## 4. The authority chain (non-negotiable)

```
ai_engine.py ──recommends──► Pi / bridge ──relays──► VCU aiRecommendationCheck()
                                                        │  confidence ≥ 0.55
                                                        │  age ≤ 500 ms (from CAN arrival)
                                                        │  every channel finite and 0–45°
                                                        ▼
                                                     ActiveAeroController
                                                        │  only in ARMED/DRIVE, aero mode = AI-assist
                                                        │  max ±20° from the physics baseline
                                                        │  rate-limited 120 °/s
                                                        ▼
                                                     servo PWM
```

Driver commands follow the same principle: the Pi only forwards fresh packets. The VCU decides whether to arm, enforces neutral throttle before DRIVE, caps duty, derates on low voltage and high current, and stops the motor 250 ms after the last command. Details: `SafetyArchitecture.md`.

## 5. Rates and latency budget

| Path | Rate | Typical latency |
|---|---|---|
| Sensor read → control decision | 100 Hz | ≤ 10 ms |
| VCU → CAN telemetry | 100 / 50 / 20 / 10 Hz by frame | < 1 ms on the bus |
| Pi → backend (MODE A) | 20 Hz | Wi-Fi, tens of ms |
| Pi → ground (MODE B) | 5 Hz | 48.8 ms air time per 50-byte packet (SF7/BW250/CR4-5) |
| AI refresh at the backend | 5 Hz | HTTP round trip |
| Ground → Pi downlink (MODE B) | ≤ 1 per uplink | 28.3 ms air time (20-byte packet) |
| Driver input → VCU | 50 Hz | Wi-Fi + CAN, tens of ms; VCU timeout 250 ms |
| ESP32-CAM → dashboard | camera-dependent | independent of all of the above |

LoRa duty cycle in MODE B: 5 Hz × 48.8 ms ≈ 24 % uplink, leaving room for downlink in the half-duplex channel.

## 6. What each layer deliberately does **not** do

- The **VCU** does not trust any value from the Pi without range/freshness checks.
- The **Pi** never computes a recommendation, never replays an old driver command, never sends actuator commands of its own.
- The **backend AI** never talks to CAN, and it is a deterministic rule set — not a trained model.
- **Video never goes over LoRa** and is not consumed by the AI (no vision pipeline exists).

## 7. Related documents

`CANProtocol.md` · `LoRaProtocol.md` · `TelemetryProtocol.md` · `FirmwareArchitecture.md` · `RaspberryPiArchitecture.md` · `SafetyArchitecture.md` · `PinoutAndWiring.md` · `BringUpGuide.md`
