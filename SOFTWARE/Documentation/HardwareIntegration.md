# Hardware Integration Plan

Stage 2. Stage-1 version (mock→real plan for a deleted source tree): `Stage1_Archive/HardwareIntegration.md`.

This is the plan for replacing every simulated interface with the real one. Status values: **REAL** (hardware driver written, compiles, untested on hardware) · **SUBSTITUTE** (deliberate stand-in, kept) · **MISSING** (not obtained/not in the BOM).

## 1. Interface status

| Interface | Stage 1 | Stage 2 | Substitute kept for bench use |
|---|---|---|---|
| VCU sensors | none (empty firmware) | **REAL** — MPU6050, Hall ×4, INA260, ADS1115, DS18B20 | `VCU_SENSORS_REAL 0` sample waveform |
| VCU actuators | none | **REAL** — 5 × MG996R (LEDC), BTS7960 | Console `servo` command |
| VCU ↔ Pi CAN | Wi-Fi UDP substitute | **REAL** — ESP32-C6 TWAI + SN65HVD230 ↔ MCP2515 HAT / SocketCAN | `VCU_TRANSPORT_WIFI_UDP` + `--can-interface wifi`; `mock` loopback |
| Pi ↔ ground telemetry | UDP substitute | **REAL** — SX1278 SPI (`LoRaRF`) or SLIP serial modem | `--lora-backend udp` + `bridge.py --radio udp` |
| Ground radio | none | **REAL** — `ground_radio_bridge.ino` (ESP32 + SX1278, RadioLib) | — |
| Driver input | none | **REAL** — `drive_client.py` over Wi-Fi UDP | `--input test` pattern |
| Video | phone WebRTC | **REAL** — ESP32-CAM MJPEG in the dashboard | phone WebRTC path retained |
| On-vehicle logging | none | **REAL** — CSV on the Pi microSD | `--no-log` |
| Pi host metrics | sine waves | **REAL** — `/proc`, `thermal_zone0` | labelled synthetic fallback on non-Linux |
| AI | rule-based at the backend | unchanged | — |
| Vision / OpenCV | none | **MISSING** — no pipeline exists | — |
| Second LoRa radio | — | **MISSING from the BOM** — required for any LoRa test | UDP substitute |

## 2. Integration order and exit criteria

| Step | Work | Exit criterion |
|---|---|---|
| 1 | Flash the VCU, no sensors | Console responds, self-test mask explains every missing part |
| 2 | I2C sensors | `i2cscan` finds all three; values sane and stable |
| 3 | Temperatures, Hall | ROM codes mapped and saved; each wheel reads independently |
| 4 | Servos | Travel set, feedback calibrated, measured tracks commanded within a few degrees |
| 5 | Motor | Bridge stays off during reset; no twitch at power-up |
| 6 | CAN | `candump can0` shows the expected IDs at the expected rates; VCU sees the Pi heartbeat |
| 7 | Gateway → backend | Dashboard shows live values; CSV log grows |
| 8 | Drive link | ARM/DRIVE sequence works; all failure drills pass |
| 9 | LoRa | ~5 Hz uplink with RSSI; recommendations reach the VCU; range noted |
| 10 | ESP32-CAM | Stream visible in the dashboard while telemetry keeps flowing |
| 11 | Calibration | `CalibrationPlan.md` complete, values recorded |

Detailed commands: `BringUpGuide.md`.

## 3. Risks carried into hardware bring-up

| Risk | Impact | Mitigation |
|---|---|---|
| INA260 breakout ships with a 0.1 Ω shunt | Current saturates at 0.82 A; overcurrent protection never fires | Fit 2 mΩ ≥ 3 W; verify against a clamp meter (step 2) |
| One 5 V buck shared by servos and Pi | Pi brown-out mid-run, SD corruption | Separate servo supply (§`PinoutAndWiring.md` 4) |
| BTS7960 5 V logic vs 3.3 V GPIO | Erratic or dead motor drive | 74AHCT125 level shifter |
| MG996R pot wiper at up to 6 V into a 3.3 V ADC | Destroys the ADC input | Divider + meter check before connecting |
| ESP32-C6 pin budget fully used, several strapping pins in use | Boot failures if a pin is held wrong | Keep the pull-ups/pull-downs in `PinoutAndWiring.md` §1 |
| MCP2515 HAT oscillator value varies (8/12/16 MHz) | CAN silently fails or runs at the wrong bit rate | Read the HAT's documentation before setting the overlay |
| SX1278 and CAN HAT both want SPI0 | Bus conflict | Radio on CE1 with the pin map in §3 of the wiring document |
| Only one SX1278 in the BOM | LoRa cannot be tested at all | Buy a second module + spare ESP32, or stay in MODE A |
| Wrong LoRa band variant (433 vs 868) | Illegal and non-functional | Confirm the part number before ordering |
| Servo stall currents during aero sweeps | Voltage sag → undervoltage faults | Rate limit (already 120 °/s), adequate supply, bulk capacitance |

## 4. What stays simulated after bring-up

- The AI remains a deterministic rule set; call it that in the report.
- No computer vision: the camera is an observation aid.
- Track position on the dashboard map is derived from lap distance, not GPS.
- Firmware/OTA pages in the dashboard are local stubs.
