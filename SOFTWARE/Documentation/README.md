# Documentation Index

Project: **AI-Enabled RISC-V Based Motorsport Electronics Platform with Active Aerodynamics, Telemetry and Digital Twin** — 1:10 Formula-style RC car.

Stage 2 documentation, written against the final BOM and the code in this archive. The Stage-1 documents are preserved unmodified in `Stage1_Archive/`.

> **Status (Stage 2d):** Stage 2c ran on the car end to end:
> - VCU → CAN → Pi → LoRa → ground → dashboard;
> - 4 halls, MG996R LF/RF with feedback, IMU, both DS18B20s.
>
> Stage 2d has only been compiled and tested in software. It adds dyno track runs on 24 stored circuits, the AI engineer with operator approval, the circuit-map dashboard and camera recording. It also changes three connections (Hall RL → GPIO3, flap RR → GPIO8, L298N on GPIO17). The steering servo, the SG90s and the L298N have **not** been tested yet. See `ChangeLog_Stage2d.md` §7.

## Start here for FW 2.3.0 (current)

| Document | Read it for |
|---|---|
| `ChangeLog_FW2.3.md` | **Knob = steering wheel, flaps follow steering, motor from the dashboard, servo calibration (how to), fault changes, dashboard-lag fixes, re-test list** |
| `../../HARDWARE/esp32/main_updated_corrected__1_/vcu_calibration.h` | **The servo end points, steering→flap law and knob settings — the file to edit** |
| `CANProtocol.md` §3b | Config keys 9, 23, 24, 30–45 (FW 2.3) |

## FW 2.2.2 (previous)

> **2026-11-12:** the prototype PCB works; the ESP32-C6 was killed by 12 V. The prototype now runs
> on a **classic ESP32 DevKit stand-in** (not RISC-V, temporary). FW 2.2.1 = FW 2.2 + that pin map;
> FW 2.2.2 swaps CAN TX/RX to match the module on J1. No logic change.

| Document | Read it for |
|---|---|
| `VCU_classic_ESP32_stopgap.md` | **Current prototype MCU:** jumper map into the U1 socket, power checks, flashing, recalibration, bring-up |
| `ChangeLog_FW2.2.2.md` | CAN TX/RX swap for the SN65HVD230 module on J1 |
| `ChangeLog_FW2.2.1.md` | What changed for the stand-in, test totals, its re-test list |
| `Project_notes/` | Chassis build notes (hand-cut sunboard v1), PCB rev B requirements check |
| `DemoRunGuide_FW2.2.md` | **The demo:** DYNO mode (presentation on the rollers) and TRACK mode (driving), dashboard Start run / E-STOP, camera auto-connect, faults |
| `ChangeLog_FW2.2.md` | Every FW 2.2 change, test totals, and the **hardware re-test list per build** |
| `VCU_final_connections.txt` | Wiring of the **prototype** build (hobby modules) |
| `PCB_revB_connections.md` | Wiring of the **PCB rev B** build, and its pre-power checklist |

> **Status (FW 2.2):** compiled and tested in software only, including end-to-end chain tests that run the firmware's own `vcu_logic.h` behind the real Pi gateway, ground bridge and backend. Not yet run on the car.

## Stage 2d (previous)

| Document | Read it for |
|---|---|
| `DemoRunGuide_Stage2d.md` | **The demo:** first power-up of the 2d firmware, dyno calibration, running a circuit, the AI engineer, recording, troubleshooting |
| `VCU_final_connections.txt` | **Authoritative VCU wiring** and the pre-power checklist |
| `VCU_wiring_changes.txt` | What moved from Stage 2c, and why |
| `AIEngineer.md` | How the AI engineer infers, proposes, and reaches the car (and what it cannot do) |
| `ChangeLog_Stage2d.md` | Every change in 2d, plus the hardware re-test list |
| `../tracks/README.md` | Where the circuit maps come from, and what in them is modelled |
| `TwoLaptopRun_Stage2c.md` | The tested Stage 2c run procedure (network, run order) |

## Stage 2 reference set — reading order

| # | Document | Read it for |
|---|---|---|
| 1 | `ProjectAudit_Stage2.md` | What was found, what was fixed, what is still open, BOM risks |
| 2 | `Architecture.md` | The three computers, both operating modes, the authority chain |
| 3 | `FirmwareArchitecture.md` | Inside the ESP32-C6 VCU |
| 4 | `RaspberryPiArchitecture.md` | The gateway and its command line |
| 5 | `SafetyArchitecture.md` | Guarantees, fault table, failure drills |
| 6 | `CANProtocol.md` · `LoRaProtocol.md` · `TelemetryProtocol.md` | Wire formats, byte by byte |
| 7 | `PinoutAndWiring.md` | Pi, ground-radio and camera wiring (the VCU section is superseded by `VCU_final_connections.txt`) |
| 8 | `BringUpGuide.md` | **Start here for hardware.** Step-by-step: laptop → Pi → CAN → VCU → driving → LoRa → camera → full system |
| 9 | `CalibrationPlan.md` | Every `[PLACEHOLDER]`: what it means, how to measure it |
| 10 | `HardwareIntegration.md` | Simulated → real status and the integration order |
| 11 | `DerivedMetrics.md` | Every metric derived from the existing sensors: method, assumptions, error sources |
| 12 | `TestingStrategy.md` | What the automated tests prove, and what they cannot (Stage 2 numbers; Stage 2d totals are in `ChangeLog_Stage2d.md` §8) |
| 13 | `SoftwareArchitectureStatus.md` · `SoftwareDevelopmentStatus.md` | Subsystem-by-subsystem status |
| 14 | `ChangeLog_Stage2.md` | Every file added or changed in Stage 2 |

## Code map

```
HARDWARE/
  esp32/main_updated_corrected__1_/            VCU firmware (.ino + vcu_logic.h)
  raspberry_pi/main.py                         vehicle gateway
  Ground Station/bridge.py                     ground-side LoRa → backend bridge
  Ground Station/drive_client.py               gamepad/keyboard driver input
  Ground Station/ground_radio_bridge/          ESP32 + SX1278 radio modem firmware
  esp32/main_updated_corrected__1_/track_maps.h  generated circuit tables (24 × 512 samples)
  tests/                                       firmware host tests, protocol parity, gateway integration, full chain
SOFTWARE/
  tracks/                                      circuit generator + GeoJSON sources (MIT)
  backend/                                     FastAPI + SQLite, AI engineer, digital twin, camera relay, tools/demo_car.py
  dashboard/                                   Next.js dashboard, Digital Twin, camera
  analysis/                                    offline log analysis (metrics, CLI, tests)
  Documentation/                               this folder
```

## One-paragraph summary for the report

An ESP32-C6 (RISC-V) vehicle control unit reads the car's sensors at 100 Hz, runs a state machine and safety manager, and is the only component allowed to move an actuator. It exchanges CAN v2 frames at 500 kbit/s with a Raspberry Pi 4B gateway, which logs everything to microSD and relays telemetry to a laptop ground station over Wi-Fi or an SX1278 LoRa link at 5 Hz. The laptop runs a FastAPI backend, a deterministic rule-based aero engine and a Next.js dashboard with a digital twin. The engine's wing recommendations are advisory: the VCU validates confidence, freshness and range, and will only nudge each of the four independent aero flaps by up to ±20° from its own speed and lateral-acceleration baseline. Driver input travels over Wi-Fi as a hold-to-arm deadman command that the VCU discards after 250 ms of silence.

**Stage 2d.** On a rolling-road dyno, the VCU drives any of 24 stored F1 circuits from its own wheel odometry:
- steering from the circuit's curvature;
- motor duty from a modelled speed profile;
- the flaps switching between low-drag straight mode and high-downforce corner mode.

A rule-based AI engineer on the laptop reads the car's position and sensor data. It sends look-ahead aero requests and proposes setup changes. A proposal reaches the car only after an operator approves it, and the VCU still validates it, stages it to the next start/finish crossing, and acknowledges it.
