# Change Log — Stage 2

> **Stage 2.1 (derived metrics)** is recorded in §"Stage 2.1" at the end of this file. The sections below cover the Stage-2 audit and rewrite.

Every file added or changed relative to `Major_Project_Hardware_Software_STAGE1_AUDITED.zip`. Paths are relative to `Major Project Hardware Software/`. Reasons refer to gap numbers in `ProjectAudit_Stage2.md` §4.

## Protocol versions

| Protocol | Stage 1 | Stage 2 | Breaking? |
|---|---|---|---|
| CAN (VCU ↔ Pi) | v1 — milli-degree aero, milli-amp current | **v2** — centi-degree aero, centi-amp current, 7 new IDs | Yes: both ends must run Stage-2 code |
| LoRa envelope | version byte 1 | **version byte 2** — centi-amp currents in TELEMETRY_COMPACT; types 7 and 8 added; type 6 formalised | Yes: v1 packets are rejected |
| Dashboard WebSocket frame | `schemaVersion` 1 | 1 (additive fields only) | No |
| Drive link (UDP) | — | **new**, magic 0xD7, version 1 | New |

## HARDWARE/

### `esp32/main_updated_corrected__1_/`  (Stage 2.2: three empty wrapper folders removed from this path)

| File | Change |
|---|---|
| `main_updated_corrected__1_.ino` | **Written from scratch** (was 0 bytes) — G1. Register-level drivers (MPU6050, INA260, ADS1115, DS18B20 1-Wire, Hall ISRs), LEDC servo + motor PWM, TWAI CAN, three FreeRTOS tasks, task watchdog, NVS calibration, serial console. Build switches `VCU_TRANSPORT_TWAI`, `VCU_TRANSPORT_WIFI_UDP`, `VCU_SENSORS_REAL`. |
| `vcu_logic.h` | **New.** Hardware-free core: configuration, state machine, safety manager, AI validation, active aero, servo maths, motor/H-bridge, supervisor, CAN v2 codec. Rounding changed from truncation to `std::lround` — G5. |

### `raspberry_pi/main.py`

- CAN v2 IDs, decoders (0x122, 0x150, 0x170, 0x171) and encoders (0x118, 0x180, 0x190) — G2, G6.
- `SocketCanInterface` implemented with python-can (was a stub); virtual-bus capable for tests.
- `SX1278SpiRadio` (LoRaRF), `SerialSlipRadio` (UART/USB modem, SLIP), shared `_VehicleRadioBase`; `UdpLoRaRadio` substitute kept.
- LoRa v2: currents ×100, message types AI_RECOMMENDATION(6), DRIVER_COMMAND(7), CONFIG_WRITE(8); `lora_time_on_air_ms()`.
- Sync word `0x12` → `0x1424` — G8. SX1278 default pins moved off the CAN HAT's CE0/GPIO25 — G9.
- `_round_half_away()` used by `_clamp_i16/_clamp_u16` — G5.
- Real Linux host metrics; `VehicleDataLogger` full-rate CSV — G12, G13.
- Drive link: `drive_packet_encode/decode`, `DriveLinkListener` → CAN 0x118 — G4.
- **SECTION 9 `run()` rewritten** as independent asyncio tasks (heartbeat 10 Hz, drive forwarding, telemetry loop, background backend connect, background AI fetch, LoRa uplink decimation) — G3. New `build_arg_parser()`.
- New CLI flags: `--ai-hz`, `--aero-mode`, `--drive-port`, `--lora-backend`, `--lora-uplink-hz`, `--lora-freq-hz`, `--lora-tx-dbm`, `--lora-serial-port/baud`, `--sx1278-*`, `--log-dir`, `--no-log`, `--duration-s`. Default `--can-interface` is now `socketcan`.
- Stale references to `HARDWARE/esp32/main.ino` and SX1278 corrected. Committed Wi-Fi password removed — G11.

### `Ground Station/`

| File | Change |
|---|---|
| `bridge.py` | Rewritten (CRLF line endings kept). LoRa v2, full 15-bit fault table, `GroundSerialRadio` (real) + `GroundRadio` (UDP substitute, same API), half-duplex downlink slot, `--aero-mode` config queue, single downlink sequence counter (G10), measured RSSI/SNR, uplink loss estimate from sequence gaps, non-blocking backend reconnect. |
| `drive_client.py` | **New.** Gamepad/keyboard/test-pattern driver input → 50 Hz UDP drive packets. ARM deadman, throttle cap, focus loss = hands off. |
| `ground_radio_bridge/ground_radio_bridge.ino` | **New.** ESP32 + SX1278 USB-serial LoRa modem (RadioLib), SLIP framed, reports RSSI/SNR. |

### `tests/`

| File | Change |
|---|---|
| `firmware_host_test.cpp` | **New.** Compiles real `vcu_logic.h` on a PC; `selftest` and `rpc` modes — G7. |
| `test_firmware_host.py` | **New.** 10 tests: golden bytes, all 451 LoRa-deliverable angles, VCU→Pi and Pi→VCU decode, AI acceptance and aero blend vs `vcu_sim.py`, state indices, fault bits across three codebases. |
| `test_stage2_gateway.py` | **New.** 7 tests: codec parity (gateway/bridge/drive client), SLIP interop, LoRa v2 currents, air-time budget, MODE A with backend down on a virtual CAN bus, MODE B end-to-end with the bridge. |
| `test_ai_recommendation_chain.py`, `test_mode_a_live_recommendation.py` | Test-side CAN decoder updated to v2 centi-degrees. |
| `vcu_sim.py` | Rejects non-finite confidence (matches firmware) — G14; comments point to `vcu_logic.h`. |

## SOFTWARE/

| File | Change |
|---|---|
| `dashboard/app/camera/page.tsx` | ESP32-CAM MJPEG source added and made default (URL editable, remembered in the browser, auto-retry). Phone WebRTC path kept; WebRTC client now only connects while that source is selected. RTSP labelled unsupported in browsers. |
| `Documentation/*` | See below. |

No backend source file changed. The backend accepts the new fault codes and extra diagnostics keys without modification (open payload models).

## SOFTWARE/Documentation/

| Document | Change |
|---|---|
| `README.md` | **New** — index and reading order |
| `ProjectAudit_Stage2.md` | **New** |
| `ChangeLog_Stage2.md` | **New** (this file) |
| `PinoutAndWiring.md` | **New** |
| `BringUpGuide.md` | **New** |
| `Architecture.md`, `CANProtocol.md`, `LoRaProtocol.md`, `TelemetryProtocol.md`, `FirmwareArchitecture.md`, `RaspberryPiArchitecture.md`, `SafetyArchitecture.md`, `CalibrationPlan.md`, `HardwareIntegration.md`, `TestingStrategy.md`, `SoftwareArchitectureStatus.md`, `SoftwareDevelopmentStatus.md` | Rewritten for the final BOM and Stage-2 code |
| `Stage1_Archive/` | **New folder** — the 12 Stage-1 documents, unmodified |

## Verification

| Check | Result |
|---|---|
| VCU firmware production build | 0 warnings, 342 807 B flash (26 %), 15 428 B RAM (4 %) |
| VCU firmware bench build | Compiles |
| Ground radio bridge firmware | Compiles, 0 sketch warnings |
| `HARDWARE/tests` | 38 passed |
| `SOFTWARE/backend/tests` | 31 passed |
| Dashboard `tsc --noEmit` | Clean |
| Dashboard `eslint .` | 0 errors, 3 pre-existing warnings |
| Physical hardware | **Not tested** |


---

# Stage 2.1 — Derived metrics, measured energy, offline analysis

Goal: get more out of the sensors the BOM already has, and stop reporting any number that was not measured. **No hardware was added.**

## Firmware (`vcu_logic.h`, `main_updated_corrected__1_.ino`)

- `slipRatioPermille()` — longitudinal slip from the driven versus reference axle.
- `ThermalEstimator` — smoothed dT/dt extrapolated to the temperature limit; `0xFFFF` = not trending, 0 = already past it.
- **New CAN frame 0x141 VEHICLE_DERIVED** (slip, seconds-to-motor-limit, seconds-to-battery-limit) at 10 Hz.
- New config: `CFG_DRIVEN_AXLE_REAR`, `CFG_SLIP_MIN_SPEED_KMH`, `CFG_THERMAL_SAMPLE_MS`, `CFG_THERMAL_ALPHA`, `CFG_THERMAL_MIN_RATE_DPS`.
- **Bug found by the new test:** the thermal estimator used `last_ms_ != 0` as its "have a sample" flag, so it would never start if the first sample arrived at `millis() == 0` (i.e. right after boot). Replaced with an explicit flag.
- Production build still compiles with 0 warnings (26 % flash, 4 % RAM).

## Gateway (`raspberry_pi/main.py`)

- `can_decode_vehicle_derived()` for 0x141; `aero_mode` recorded from 0x171 so runs can be split by aero mode afterwards.
- **`DerivedEstimator`** (new SECTION 6f): distance, wheel rpm, wheel angle, power, energy, Wh/km, coulomb-counted SoC (voltage-corrected at rest), pack internal resistance from load steps.
- `to_dashboard_frame()` now sends **real** `rpm`, `distance` and `wheelAngle` (previously hard-coded 0) plus a `derived` block.
- New CLI flags `--wheel-diameter-m`, `--pack-capacity-ah`; a run summary line on exit.
- 12 new CSV columns, so every derived value is in the on-vehicle log.

## Ground station — new package `SOFTWARE/analysis/`

| File | Contents |
|---|---|
| `log_loader.py` | Reads the gateway CSV; time base, filters, aero-mode split. Standard library only |
| `metrics.py` | Energy summary, per-lap energy, range, pack resistance, friction circle, understeer gradient, slip, sideslip, fused speed, coast-down drag (CdA), lateral-g vs speed, baseline-vs-AI comparison, lap detection, dead reckoning, thermal fit, servo identification, sensor residuals, digital-twin error |
| `analyze.py` | CLI: printed summary, `--compare`, `--json`, optional `--plots` (matplotlib) |
| `tests/synthetic.py`, `tests/test_analysis.py` | 25 tests against logs with analytically known answers |
| `README.md` | Usage |

**Bug found by the new tests:** lap detection used an un-normalised autocorrelation, which scored two laps as highly as one and reported double the true lap time. Replaced with a normalised coefficient and first-peak selection.

## Backend

- New `app/services/energy.py`: integrates pack voltage × current over a session's recorded frames; returns `available: False` when there is not enough movement.
- `ai_engine.generate_recommendation()` takes `energy_context`; **`energyPerLap` and `predictedRangeLaps` are now measured or `null`** with `energyMeasured: false` and an `energy_unmeasured` warning. The old formulas (`35 + 0.15 × speed`, `battery% / 4`) are removed.
- New endpoint `GET /api/ai/energy`.
- New `tests/test_energy.py` (6 tests). Backend suite: 31 → 37.

## Dashboard

- `types/telemetry.ts`: `energyPerLap` and `predictedRangeLaps` are nullable, plus `whPerKm` and `energyMeasured`.
- Analytics page: new "Derived metrics (measured, no extra sensors)" panel — power, energy, consumption, coulomb SoC, pack resistance, wheel slip, seconds-to-limit; em dashes when the vehicle is not connected.

## Documentation

- **New** `DerivedMetrics.md` — every metric, its method, assumptions and error sources, plus the experiment design needed for the active-aero comparison.
- Updated: `CANProtocol.md` (0x141, bus load), `TelemetryProtocol.md` (real `rpm`/`distance`/`wheelAngle`, `derived`, measured energy), `FirmwareArchitecture.md` (§6b), `RaspberryPiArchitecture.md` (§6f, new flags), `TestingStrategy.md`, `SoftwareArchitectureStatus.md`, `README.md`, `SOFTWARE/analysis/README.md`.

## Verification after Stage 2.1

| Check | Result |
|---|---|
| `HARDWARE/tests` | 38 passed |
| `SOFTWARE/backend/tests` | 37 passed |
| `SOFTWARE/analysis/tests` | 25 passed |
| Dashboard `tsc --noEmit` | clean |
| Dashboard `eslint .` | 0 errors, 3 pre-existing warnings |
| VCU firmware (production) | 0 warnings, 343 889 B flash (26 %) |
| Ground radio bridge firmware | compiles |
| Physical hardware | still untested |


---

# Stage 2.2 — Folder cleanup

No code behaviour changed; only paths.

| Removed | Why |
|---|---|
| `HARDWARE/esp32/main/`, `.../main_updated_corrected/`, `.../main_updated_corrected (1)/` | Three nested folders that held no files — artefacts of how the sketch was originally shared. The sketch now sits at **`HARDWARE/esp32/main_updated_corrected__1_/`**. The leaf folder keeps its name because the Arduino toolchain requires the sketch folder and the `.ino` file to match |
| The two archive wrapper folders (`Major_Project_Hardware_Software_STAGE1_AUDITED/Major_Project_Hardware_Software_FROZEN/`) | Wrappers from the original upload. The zip now starts at `Major Project Hardware Software/`, which also keeps Windows path lengths well under the 260-character limit |

References updated in `HARDWARE/tests/test_firmware_host.py`, `Architecture.md`, `FirmwareArchitecture.md`, `TestingStrategy.md` and `README.md`. Re-verified afterwards: 38 + 37 + 25 tests pass, firmware compiles with 0 warnings from the new location.

Every remaining folder is load-bearing: `SOFTWARE/backend/data/` is where SQLite writes `vcu.db`, each `SOFTWARE/dashboard/app/<name>/` folder is one Next.js route (the framework requires the folder-per-page layout), and `SOFTWARE/Documentation/Stage1_Archive/` holds the superseded Stage-1 documents for traceability — delete that one if you do not want the history.


---

# Stage 2.3 — Matched to the hardware actually on the bench

Parikshit's photographs showed three parts that differ from the BOM the code was written against, plus two board-level pin limits. Everything below is a code change, not a wiring change: **the connections already built stay as they are**, apart from the four listed in "wiring that must change".

## Parts that differ

| BOM said | Actually have | Consequence |
|---|---|---|
| INA226 + external 2 mΩ shunt | **INA260** (integrated 2 mΩ shunt) | Different register map and no calibration register — the old driver would have failed its self-test. No shunt to buy, but the sensor saturates near 15 A |
| SX1262 @ 866 MHz | **2× SX1278 (Ai-Thinker Ra-02) @ 433 MHz** | Different chip family (SX126x vs SX127x); 866 MHz is outside the module's band. Both radios present, so the link can be built |
| ESP32-C6-DevKitC-1 | **ESP32-C6-Pico** | GPIO12/13 are not broken out |
| — | Ground radio on a **classic ESP32 dev module** | No GPIO20; GPIO1 is the USB serial TX |

## Firmware (`main_updated_corrected__1_.ino`, `vcu_logic.h`)

- **INA226 driver replaced with an INA260 driver.** Manufacturer *and* die ID checked (0x5449 / 0x227x); current read from register 0x01 at 1.25 mA/bit, bus voltage from 0x02 at 1.25 mV/bit; no calibration register.
- `CFG_BATTERY_OVERCURRENT_A` 35 A → **14 A**, and `CFG_MOTOR_CURRENT_LIMIT_A` 20 A → **8 A**. These are now set by the INA260's range, not the motor: above ~15 A the sensor saturates and the protection would be blind.
- **Hall pins moved:** FL GPIO12 → **GPIO14**, FR GPIO13 → **GPIO2** (the Pico does not expose 12/13).
- **Servo feedback split across two ADCs:** LF and RF stay on GPIO0/GPIO1; steering, LR and RR are read by the ADS1115 (AIN1, AIN2, AIN3) because the Pico cannot supply six ADC pins. The ADS1115 is cycled `1, 2, 1, 3, 0` at 100 Hz — steering 40 Hz, the rest 20 Hz each.
- Self-test and ADC setup now handle both feedback sources; GPIO3 is the only spare pin left.
- Still compiles with 0 warnings (344 127 B flash, 26 %).

## Vehicle gateway (`raspberry_pi/main.py`)

- `SX1262SpiRadio` → **`SX1278SpiRadio`** (LoRaRF `SX127x`). No BUSY pin, no TCXO control, no TXEN/RXEN — the SX1278 switches its own RF path. RX is polled straight from the IRQ-flags register, because LoRaRF's `wait()` spins without a timeout when no IRQ pin is configured and would stall the gateway's other tasks.
- Defaults: **SPI1** (`/dev/spidev1.0`, needs `dtoverlay=spi1-1cs`) since the 2-channel CAN HAT owns both SPI0 chip selects, RST GPIO17, DIO0 GPIO27, 433 MHz, 17 dBm via PA_BOOST, sync word 0x12.
- Flags `--sx1262-*` → `--sx1278-spi-bus/-spi-cs/-reset/-dio0/-rfo`; `--lora-backend sx1262` → `sx1278`.

## Ground station

- `ground_radio_bridge.ino`: RadioLib `SX1262` → **`SX1278`**, DIO0 interrupt instead of DIO1, VSPI pins for a classic ESP32 dev module (NSS 5, SCK 18, MISO 19, MOSI 23, RST 14, DIO0 26), 433 MHz, 17 dBm. Compiles for `esp32:esp32:esp32`, 0 sketch warnings.
- `bridge.py`: air settings and documentation updated to 433 MHz / SX1278.

## Wiring that must change from the earlier tables

1. Hall FL → GPIO14, Hall FR → GPIO2.
2. Flap LR and RR feedback → ADS1115 AIN2 and AIN3 (not GPIO2/GPIO3).
3. Ra-02 on the Pi → SPI1 (NSS GPIO18, SCK GPIO21, MOSI GPIO20, MISO GPIO19, RST GPIO17, DIO0 GPIO27).
4. Ra-02 on the ground ESP32 → NSS 5, SCK 18, MISO 19, MOSI 23, RST 14, DIO0 26.

## Still outstanding on the hardware side

- **An ADS1115 is now required, not optional.** Without it there is no battery-voltage sensing (so no undervoltage protection) and no steering or LR/RR flap feedback.
- Both antennas must be 433 MHz types.
- The INA260's ~15 A ceiling is below the traction motor's stall current: the car will derate and then fault before the motor's real limit. That is the safe direction, but it does cap performance.

## Verification after Stage 2.3

| Check | Result |
|---|---|
| `HARDWARE/tests` | 38 passed |
| `SOFTWARE/backend/tests` | 37 passed |
| `SOFTWARE/analysis/tests` | 25 passed |
| VCU firmware (ESP32-C6) | 0 warnings, 344 127 B flash (26 %) |
| Ground radio bridge (classic ESP32) | compiles, 0 sketch warnings |
| Dashboard `tsc` / `eslint` | clean / 0 errors |
| Physical hardware | still untested |


---

# Stage 2.4 — ESP32-CAM sketch added

`HARDWARE/esp32_cam/esp32_cam_stream/` — the on-board camera firmware, which until now was "flash the stock CameraWebServer example". Same idea, trimmed to the project's needs: MJPEG on port 81 (the URL the dashboard expects) with the page, snapshot and JSON status endpoints on port 80, so a stuck stream cannot block them. One viewer at a time (a second client gets 503 instead of halving the first one's rate), Wi-Fi auto-reconnect for when the car drives out of range, `CAMERA_GRAB_LATEST` for lowest latency, and the 300 mA flash LED forced off at boot. Compiles for `esp32:esp32:esp32cam`, 31 % flash, 0 warnings. Not hardware validated.

---

## Stage 2b — bring-up audit (findings B1–B13 in `ProjectAudit_Stage2.md` §9)

| File | Change |
|---|---|
| `HARDWARE/esp32/main_updated_corrected__1_/main_updated_corrected__1_.ino` | `VCU_HAVE_ADS1115` (default 0) + `VCU_FB_WIRED_MASK` + `fbWired()`; INA260 VBUS as pack voltage and power freshness without ADS1115; self-test, sensor task, `cal`, `status` gated per servo; boot banner prints the build config; `status` prints TWAI state/TEC/REC; header corrected to ESP32-C6-Pico + USB CDC guidance; bench Wi-Fi block set to CTRL / 10.118.112.178; `-Wvolatile` fix |
| `HARDWARE/raspberry_pi/main.py` | `--laptop`/`LAPTOP_IP`, MODE A guard; Pi IP; ground-link heartbeat bit in MODE A = backend_ok; SX1278: 1 MHz SPI, STANDBY before TX, signed SNR, 10 dBm; serial modem ignores `#` status lines |
| `HARDWARE/raspberry_pi/setup/` (new) | `pi_setup.sh`, `can_up.sh`, `config_txt_snippet.txt`, `vcu-gateway.service` |
| `HARDWARE/Ground Station/bridge.py` | serial port opened with DTR/RTS low, settle + flush, clear port-busy error; `#` status lines logged not decoded |
| `HARDWARE/Ground Station/ground_radio_bridge/ground_radio_bridge.ino` | 10 dBm, current limit 100 mA, regulatory note |
| `HARDWARE/Ground Station/drive_client.py` | Pi IP in usage text |
| `HARDWARE/esp32_cam/esp32_cam_stream/esp32_cam_stream.ino`, `README.md` | project Wi-Fi credentials; lag-tuning notes |
| `HARDWARE/tests/test_stage2b_fixes.py` (new) | 10 tests: SX1278 fixes with a fake LoRaRF, serial `#` filtering, DTR/RTS, heartbeat bit, `--laptop`, network constants, firmware ADS-fallback wiring |
| `SOFTWARE/Documentation/BringUpGuide.md` | rewritten for the actual hardware and a full-system run; previous version in `Stage2a_Archive/` |
| `PinoutAndWiring.md`, `FirmwareArchitecture.md`, `LoRaProtocol.md`, `CalibrationPlan.md`, `RaspberryPiArchitecture.md`, `ProjectAudit_Stage2.md`, `README.md`, `SoftwareDevelopmentStatus.md` | corrected facts (CAN naming, HAT ground, INA260 VBUS, USB CDC, 433 MHz regulation, test counts) |
