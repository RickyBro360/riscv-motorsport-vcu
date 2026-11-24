# Project Audit — Stage 2

**Project:** AI-Enabled RISC-V Based Motorsport Electronics Platform with Active Aerodynamics, Telemetry and Digital Twin (1:10 Formula-style RC car)
**Audit input:** `Major_Project_Hardware_Software_STAGE1_AUDITED.zip` + final BOM + final system architecture
**Audit date:** September 2026
**Scope:** everything in `HARDWARE/` and `SOFTWARE/`. Folder structure was preserved; nothing was moved or renamed. Stage-1 versions of every rewritten document are kept verbatim in `Documentation/Stage1_Archive/`.

> **Validation status, stated once and meant everywhere:** nothing in this repository has been run on the physical car, the Raspberry Pi CAN HAT, the SX1278 radios or the ESP32-CAM. "Verified" below means compiled, type-checked or tested in software (unit, parity and integration tests with simulated buses). Hardware bring-up is the next stage — see `BringUpGuide.md`.

---

## 1. Locked architecture (unchanged by this audit)

```
 ESP32-C6 VCU  ──CAN 500k──  Raspberry Pi 4B  ──SX1278 LoRa──  Laptop ground station
 (sensors, actuators,        (telemetry gateway,               (FastAPI backend, rule-based AI,
  safety, state machine,      CSV logger, relay —               Next.js dashboard, Digital Twin)
  SOLE ACTUATOR AUTHORITY)    NOT the AI)
        ▲                          ▲ Wi-Fi                           │ Wi-Fi
        │                          └── drive_client.py (driver) ─────┤
        └──────── ESP32-CAM ── MJPEG over Wi-Fi ────────────────────►┘ dashboard
```

Authority rule: **laptop AI recommends → Pi relays → VCU validates and decides.** No component other than the VCU can move a servo or the motor.

## 2. Final BOM vs. what the Stage-1 repository described

| BOM item (final) | Stage-1 repo state | Stage-2 state |
|---|---|---|
| ESP32-C6 VCU (RISC-V) | **Firmware file was 0 bytes** | Full firmware written, compiles (26 % flash, 0 warnings) |
| SN65HVD230 + 2×120 Ω | Docs said MCP2515 + TJA1050 on the VCU | Native ESP32-C6 TWAI controller + SN65HVD230 |
| Raspberry Pi 4B + CAN HAT | Docs said Raspberry Pi 5; CAN was a UDP substitute / `NotImplementedError` stub | Real SocketCAN backend (python-can) |
| SX1278 LoRa | Docs said SX1278; LoRa was a UDP substitute | Real SX1278 SPI backend (LoRaRF) + serial modem backend + ground radio bridge firmware |
| ESP32-CAM (video over Wi-Fi) | Not mentioned anywhere; dashboard expected a phone over WebRTC | Dashboard camera page shows the ESP32-CAM MJPEG stream (phone path kept) |
| MPU6050 IMU | Docs said ICM42688P | Register-level driver, gyro bias calibration, complementary filter |
| 4× Hall wheel speed | Documented | ISR period measurement, debounce, stop timeout |
| INA260 current | Documented | Driver + calibration register maths (shunt value flagged, see §5) |
| ADS1115 + divider battery voltage | Documented | Driver; AIN0 battery, AIN1 steering servo feedback |
| 2+ DS18B20 | Documented | Bit-banged 1-Wire with ROM search, role mapping stored in NVS |
| 5× MG996R with internal-pot feedback | Docs said AS5600 magnetic encoders ×5 | Pot-feedback calibration + commanded-vs-measured position monitor |
| Traction motor + BTS7960 | Not mentioned | Sign-magnitude drive, ramp, braking, current/voltage derating |
| 3S LiPo + 5 V buck | Documented | Undervoltage/overcurrent/overtemperature with persistence timers |
| Laptop ground station | Implemented (backend + dashboard) | Unchanged except camera page |

## 3. What already existed and worked (Stage 1)

| Component | Evidence at audit |
|---|---|
| `SOFTWARE/backend` FastAPI + SQLite + `/ws/telemetry` + rule-based `ai_engine.py` | 31 pytest tests passed |
| `SOFTWARE/dashboard` Next.js 16 / React 19 / TypeScript 5.7 | `tsc --noEmit` clean; ESLint 0 errors, 3 pre-existing `no-explicit-any` warnings |
| `HARDWARE/raspberry_pi/main.py` gateway (MODE A direct / MODE B via LoRa substitute) | 21 HARDWARE pytest tests passed |
| `HARDWARE/Ground Station/bridge.py` | Covered by the same tests |
| `SOFTWARE/Documentation/` (12 files) | Present but largely describing superseded hardware and a deleted `Firmware/` + `RaspberryPi/` source tree |

## 4. Gaps found (all addressed in Stage 2 unless marked OPEN)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| G1 | **Critical** | `main_updated_corrected__1_.ino` was **empty**, yet the Pi gateway, bridge, tests and docs all referenced it. The project had no VCU. | Firmware written from scratch against the final BOM (`FirmwareArchitecture.md`). Hardware-free logic split into `vcu_logic.h` so it is compiled and tested on a PC. |
| G2 | High | CAN v1 encoded aero angles as **milli-degrees in int16** — clamps at 32.767° on a 0–45° flap — and currents as **milli-amps in int16**, clamping at 32.767 A below the 35 A overcurrent threshold. | CAN protocol v2: centi-degrees, centi-amps (`CANProtocol.md`). Same bug found and fixed in LoRa TELEMETRY_COMPACT → LoRa protocol v2. |
| G3 | High | **Stage-1 gateway loop would have tripped the VCU's timeouts.** It awaited backend reconnect (seconds) and the AI HTTP fetch (up to 1 s) inline; the VCU needs a Pi frame every < 500 ms and a driver command every < 250 ms. | Gateway loop rewritten as independent asyncio tasks. Integration test proves heartbeat gaps < 300 ms while the backend is down. |
| G4 | High | No driver-command path at all (no RC receiver in the BOM, nothing on CAN). The car could not be driven. | CAN 0x118 DRIVER_COMMAND; `drive_client.py` (gamepad/keyboard) → UDP → Pi → CAN, with ARM-deadman, 250 ms VCU timeout. |
| G5 | Medium | Scaling encoders **truncated**: 12.3° × 100 is 1229.999… in IEEE double, so Python sent 1229 while float32 C++ produced 1230. Found by the new firmware-parity test. | Both sides now round half away from zero (`std::lround` / `_round_half_away`). 9 001 values checked identical. |
| G6 | Medium | Reserved-but-unimplemented CAN IDs (0x122, 0x140, 0x150, 0x170, 0x180) and fault bits (servo position error, comm loss) defined but never produced. | All implemented; fault table extended to 15 bits and kept identical in C++, gateway and bridge (checked by test). |
| G7 | Medium | Tests compared Python against a hand-written Python "mirror" of firmware that did not exist. | `tests/firmware_host_test.cpp` compiles the **real** `vcu_logic.h`; `test_firmware_host.py` checks golden bytes and behaviour against the gateway codec and `vcu_sim.py`. |
| G8 | Medium | LoRaRF `setSyncWord(0x12)` writes `(0x14, 0x124)` — the second value is not a byte. Would fail or mis-program the SX1278. | Pi now passes the 2-byte register value `0x1424` (equals RadioLib's `0x12` on the ground side). |
| G9 | Medium | Pi SX1278 default pins (SPI0 CE0, GPIO25) **collide with a standard MCP2515 CAN HAT** (CE0 chip-select, GPIO25 interrupt). | SX1278 moved to SPI0 CE1, reset GPIO17, busy GPIO27, TXEN GPIO5, RXEN GPIO6 (`PinoutAndWiring.md`). |
| G10 | Medium | Ground bridge downlink used two sequence counters; the Pi's duplicate filter would drop valid packets when they coincided. | Single downlink counter. |
| G11 | Low | Real Wi-Fi password committed in `raspberry_pi/main.py` (unused by the code). | Replaced with `SET_ME`. **Change the network password** if this repo was ever shared. |
| G12 | Low | Diagnostics CPU/memory/temperature were sine waves. | Read from `/proc` and `thermal_zone0` on Linux; synthetic fallback labelled `hostMetricsSource: synthetic-fallback`. |
| G13 | Low | No on-vehicle record: LoRa only carries ~5 Hz. | Full-rate CSV logger on the Pi microSD. |
| G14 | Low | `vcu_sim.py` accepted a NaN confidence; firmware rejects it. | Mirror aligned. |
| G15 | Doc | 8 of 12 documents described Pi 5, SX1278, ICM42688P, AS5600, MCP2515/TJA1050, Pi Camera and a deleted source tree. | All rewritten; originals archived. |

## 5. Hardware and BOM risks you must resolve before bring-up

These are **not software problems** and cannot be fixed in code. Each is repeated where relevant in `PinoutAndWiring.md` / `BringUpGuide.md`.

1. **Second LoRa radio missing from the BOM.** A LoRa link needs two radios. The ground side needs a second SX1278 module (same frequency variant) plus any spare ESP32 dev board running `Ground Station/ground_radio_bridge/`.
2. **INA260 shunt.** Most breakouts ship with 0.1 Ω (R100), which saturates at 0.82 A. Fit a 2 mΩ shunt rated ≥ 3 W (35 A → 2.45 W). With 2 mΩ the INA260 measures up to ±40.9 A.
3. **Servo power.** Five MG996R servos can each draw ~2.5 A at stall. A single "5 V high-current buck" shared with the Pi 4B (3 A) will brown out the Pi when servos load. Use a dedicated servo supply (≥ 10 A, 6 V is fine for MG996R) and a separate 5 V/3 A supply for the Pi and logic, with common ground.
4. **BTS7960 (IBT-2) logic level.** The module buffers inputs with a 74HC244 at 5 V; 3.3 V from the ESP32-C6 is marginal. Add a 74AHCT125 (or similar) level shifter on RPWM, LPWM and EN.
5. **MG996R feedback modification.** Internal-pot feedback requires opening each servo and soldering a wire to the pot wiper. The wiper voltage is referenced to the servo supply (up to 6 V) — a resistor divider is mandatory before the ESP32-C6 ADC (max 3.3 V) and the ADS1115 (VDD + 0.3 V).
6. **ESP32-C6 pin budget is fully used** (all 21 exposed GPIOs). GPIO12/13 are the native-USB pins (on the ESP32-C6-Pico the single USB-C port IS the native USB — flash and use the console through it with *USB CDC On Boot: Enabled*) and GPIO4/5/8/9/15 are strapping pins — see the notes in `PinoutAndWiring.md`.
7. **Band and power.** The radios fitted are 433 MHz Ra-02s. In India 433–434.79 MHz is de-licensed only at very low power and with conditions (865–867 MHz is the band LoRa was designed around there). Both ends now transmit at 10 dBm; verify the current notification and state the limitation in the report.

## 6. Known software limitations (honest list)

- **AI is rule-based, not machine learning.** `backend/app/ai_engine.py` is deterministic. Describe it as such in the report.
- **No computer vision.** The ESP32-CAM stream is displayed, not analysed. OpenCV is in the BOM's ground-station role but no vision pipeline exists yet.
- **AI staleness is measured from VCU arrival, not from the telemetry it was computed on.** In MODE B the recommendation can be based on telemetry up to ~0.5 s old (49 ms uplink + ≤ 200 ms AI refresh + ≤ 200 ms wait for the next downlink slot + 28 ms downlink) and still be accepted as "fresh".
- **Yaw drifts** (gyro-only, no magnetometer in the BOM).
- **No dedicated motor-current sensor.** CAN 0x140 "motor current" carries the INA260 pack current.
- **Dashboard shows wing target = wing measured in MODE B** because the LoRa packet carries only measured angles. The full target/measured split is in the Pi CSV log and in MODE A.
- **Real hardware backends** (`SocketCanInterface` on `can0`, `SX1278SpiRadio`, `SerialSlipRadio`, `GroundSerialRadio`, TWAI) are exercised only through virtual buses and compile checks.

## 7. Verification performed (Stage 2, end state)

| Check | Result |
|---|---|
| VCU firmware, production build (TWAI + real sensors), `arduino-cli`, esp32 core 3.1.1, `--warnings all` | Compiles, **0 warnings**, 342 807 B flash (26 %), 15 428 B RAM (4 %) |
| VCU firmware, bench build (Wi-Fi-UDP + sample sensors) | Compiles (unused-variable warnings only, expected) |
| Ground radio bridge firmware (RadioLib 7.1.2, ESP32-C6) | Compiles, 0 sketch warnings, 307 051 B flash (23 %) |
| `vcu_logic.h` on host, `g++ -Wall -Wextra -Werror` | Clean; self-test PASSED |
| `HARDWARE/tests` pytest | **38 passed** (21 Stage-1 + 10 firmware parity + 7 Stage-2 gateway integration), stable over repeated runs |
| `SOFTWARE/backend/tests` pytest | 31 passed |
| Dashboard `npx tsc --noEmit` | Clean |
| Dashboard `npx eslint .` | 0 errors, 3 pre-existing warnings |

The final numbers are re-confirmed in `ChangeLog_Stage2.md` §Verification.

## 8. What is left (Stage 3 — hardware)

Follow `BringUpGuide.md` in order. In summary: power and wiring checks → VCU alone on the bench (sample build, then real sensors one bus at a time) → servo feedback calibration → CAN between VCU and Pi → drive link with wheels off the ground → LoRa link → ESP32-CAM → full system → calibration (`CalibrationPlan.md`) → track tests with data from the Pi CSV logs.

---

## 9. Stage 2b — bring-up audit (September 2026, against the hardware on the bench)

Input: the Stage-2 code plus the first real bring-up (ESP32-CAM streaming; backend and dashboard running; Pi CAN HAT initialised and passing loopback; no frames from the VCU; `can0` ERROR-PASSIVE; missing HAT ground found). Hardware as actually fitted: Waveshare ESP32-C6-Pico, VP230, Pi 4B + 2-channel MCP2515 HAT, two 433 MHz Ra-02, classic ESP32 ground bridge, ESP32-CAM, INA260, **no ADS1115**.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| B1 | **Blocker** | Without the ADS1115 the VCU fails self-test (ADS1115, battery) and also raises INVALID_SENSOR / SENSOR_TIMEOUT forever, because pack voltage and its freshness stamp came only from ADS1115 AIN0. The car could never arm. | `VCU_HAVE_ADS1115` build option (default 0): pack voltage and freshness from the INA260 VBUS register (the INA260 is in the pack line); ADS1115 channels treated as not wired. `VCU_FB_WIRED_MASK` + `fbWired()` gate self-test, position monitor, `cal` and `status` per servo. |
| B2 | High | CAN interface naming: Linux names `can0`/`can1` in probe order (`can0` measured as `spi0.1`), so the VCU can be on `can1` while wired to the terminal labelled CAN0. A loopback test cannot reveal this, nor a wrong `oscillator=` value. | `raspberry_pi/setup/can_up.sh` brings up every interface, prints `canX -> spi0.Y`, listens on each and reports the `--can-channel` to use. Guide §5 adds the crystal check. |
| B3 | High | No `restart-ms`: an MCP2515 that goes bus-off (e.g. with no common ground) stays down until the link is manually cycled. | `can_up.sh` sets `restart-ms 100`; txqueuelen deliberately left short so stale driver commands are not replayed. |
| B4 | High | ESP32-C6-Pico has one USB-C port and it is the native USB. The documented "USB CDC On Boot: Disabled" routes the console to unconnected UART0 pins — console appears dead. | Firmware header, FirmwareArchitecture, BringUpGuide corrected (Enabled for native USB, Disabled only for USB-UART boards). |
| B5 | High | Pi Wi-Fi power save (on by default) adds 100–300 ms latency spikes; the VCU drops driver commands after 250 ms → random disarms / COMM_LOSS. | `setup/pi_setup.sh` turns it off persistently; guide covers the laptop side. |
| B6 | Medium | MODE A heartbeat always reported the ground link as down (bit 0 was tied to the LoRa radio even when there is none) → permanent LORA_TIMEOUT warning on the dashboard. | Bit 0 = ground link: LoRa downlink age in MODE B, `backend_ok` in MODE A. |
| B7 | Medium | SX1278 on the Pi: TX was started straight from RX-continuous (arriving packet can overwrite the TX FIFO; SX127x expects mode changes via STANDBY); LoRaRF drives SPI at 7.8 MHz (unreliable over jumper wires); LoRaRF's `snr()` treats the signed register as unsigned. | STANDBY + FIFO pointer reset before every TX; SPI at 1 MHz; SNR decoded as two's complement. Covered by tests with a fake LoRaRF. |
| B8 | Medium | Ground bridge prints `# …` status lines between SLIP frames; both serial decoders passed them on as corrupt packets (decode-error noise). Opening the port toggled DTR/RTS, which resets (or can hold) an ESP32 DevKit. | `#` lines are logged, not decoded (Pi and ground). Ground port opened with DTR/RTS de-asserted, 1.5 s settle, input flushed; clear error if the Serial Monitor still holds the port. |
| B9 | Medium | Radio power mismatch (Pi 14 dBm, ground 17 dBm) and an overstated regulatory claim ("433 MHz is licence-exempt in India"). | Both ends 10 dBm; RadioLib current limit raised to 100 mA so PA_BOOST is not starved if the power is ever raised; docs now say the band is de-licensed only at very low power with conditions — verify before raising power. |
| B10 | Low | Wrong Pi IP everywhere (`10.118.113.178`; actual `10.118.112.178`); laptop IP hard-coded. | Pi IP corrected; gateway takes `--laptop <ip>` or `LAPTOP_IP`, and refuses to start in MODE A without one instead of silently retrying a wrong address. |
| B11 | Low | Camera sketch and VCU bench block had placeholder credentials. | Set to the project network. **Do not push these two files to a public repository.** |
| B12 | Low | `status` gave no view of the CAN controller. | `status` prints TWAI state, TEC/REC, bus errors and a "nobody is ACKing" hint. |
| B13 | Low | `-Wvolatile` warning on core 3.3.x. | Fixed; 0 warnings. |

**Superseded:** G9 above (SX1278 on SPI0 CE1) — the radio is on **SPI1** (NSS GPIO18, RST 17, DIO0 27) because the 2-channel HAT uses both SPI0 chip selects.

**Still open (hardware):** ADS1115 (steering/LR/RR run open-loop until fitted, so a jammed servo on those three is not detected); INA260 VBUS must be connected on the module; everything in §5.

### Verification at the end of Stage 2b

| Check | Result |
|---|---|
| VCU firmware, esp32 core **3.3.12**, `--warnings all`, as shipped (ADS1115 0, TWAI, real sensors), CDC on boot off and on | **0 warnings**, 376 936 B flash (28 %) |
| VCU variants: `VCU_HAVE_ADS1115 1` · `VCU_TRANSPORT_WIFI_UDP 1` · `VCU_SENSORS_REAL 0` | compile; the sample build has the expected unused-variable warnings |
| Ground radio bridge (RadioLib 7.1.2, ESP32 Dev Module) | compiles, 0 warnings, 23 % flash |
| ESP32-CAM stream (AI Thinker ESP32-CAM) | compiles, 32 % flash |
| `HARDWARE/tests` | **48 passed** (38 + 10 new in `test_stage2b_fixes.py`), 4 consecutive runs |
| `SOFTWARE/backend/tests` | 37 passed |
| `SOFTWARE/analysis/tests` | 25 passed |

Still **not hardware validated**: the fixes above are compile- and test-verified only. The bring-up records from `BringUpGuide.md` are what validates them.
