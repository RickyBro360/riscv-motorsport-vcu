# Firmware Architecture — ESP32-C6 VCU

Stage 2. Stage-1 version (described a `Firmware/` C tree that no longer exists): `Stage1_Archive/FirmwareArchitecture.md`.

> **Stage 2d additions** are not described in this document:
> - the run states (bench motor / dyno cal / bench laps / track drive);
> - `TrackRunner`, `ConfigStore`, `DynoCalibrator`, `StallDetector` and `VibrationMeter`;
> - the L298N output on GPIO17.
>
> They are described in `ChangeLog_Stage2d.md` §3, `DemoRunGuide_Stage2d.md` and `CANProtocol.md` §3b. The code comments in `vcu_logic.h` are the detailed reference.

**Location:** `HARDWARE/esp32/main_updated_corrected__1_/`
**Firmware version:** `2.0.0-stage2` · **CAN protocol:** v2
**Status:** compiles for ESP32-C6 with 0 warnings; logic unit-tested on a PC; NOT YET run on the car.

## 1. Two files, one rule

| File | Contains | Depends on |
|---|---|---|
| `vcu_logic.h` | Configuration, state machine, faults and safety manager, AI validation, active aero, servo calibration maths, motor and BTS7960 mapping, supervisor, wheel-speed and attitude maths, CAN v2 codec | C++ standard library only |
| `main_updated_corrected__1_.ino` | Pin map, sensor drivers, PWM outputs, TWAI, FreeRTOS tasks, watchdog, NVS storage, self-test, serial console | Arduino-ESP32 core 3.1.x (ESP-IDF 5.3) |

**Rule:** every decision lives in `vcu_logic.h`; the sketch only moves data between hardware and that logic. Because the header has no hardware dependency, `HARDWARE/tests/firmware_host_test.cpp` compiles the *same* file on a PC and tests it (state machine, safety, motor, servo maths, and byte-exact CAN parity with the Pi). No external Arduino libraries are required.

## 2. Build

- Arduino IDE: board **ESP32C6 Dev Module**, default partition scheme. *USB CDC On Boot*: **Enabled** if the USB port enumerates as "USB JTAG/serial debug unit" (VID 303A — the ESP32-C6-Pico's only USB-C port is the chip's native USB on GPIO12/13); *Disabled* only for boards with a CH340/CP210x USB-UART chip. The wrong setting leaves the console silent.
- `VCU_HAVE_ADS1115` (default **0** until the ADS1115 is fitted): pack voltage from the INA260 VBUS register; steering/LR/RR feedback treated as not wired. `VCU_FB_WIRED_MASK` lists which pot-feedback wires exist (FW 2.1.1: `0x18` = LR, RR — the rear MG996R pair; Stage 2c used `0x06` = LF, RF). See `ProjectAudit_Stage2.md` §9.
- CLI: `arduino-cli compile --fqbn esp32:esp32:esp32c6 "<sketch folder>"`
- Production build: 342 807 B flash (26 %), 15 428 B RAM (4 %).

| Switch (top of `.ino`) | Production | Bench demo | Meaning |
|---|---|---|---|
| `VCU_TRANSPORT_TWAI` | 1 | 1 or 0 | Real CAN via SN65HVD230 |
| `VCU_TRANSPORT_WIFI_UDP` | 0 | 1 | Stage-1 UDP substitute to the Pi (`--can-interface wifi`); set `WIFI_SSID/PASSWORD/PI_IP` |
| `VCU_SENSORS_REAL` | 1 | 0 | 0 = deterministic sample waveform, no sensors needed |

## 3. Runtime structure

```
setup()  motor EN low + PWM 0 FIRST → serial → NVS calibration → servos to failsafe
         → ADC, I2C 400 kHz, 1-Wire, Hall interrupts → TWAI → task watchdog (3 s) → tasks

 ┌─────────────── sensorTask  prio 3, 100 Hz ─────────────────┐
 │ MPU6050 accel/gyro + complementary filter                  │
 │ Hall wheel speeds (ISR periods)                             │
 │ INA260 current 20 Hz                                        │
 │ ADS1115: steering 40 Hz, flap LR/RR + battery 20 Hz each    │
 │ servo feedback ADC ×4 (4-sample average)                    │
 │ DS18B20: convert every 1 s, read 800 ms later               │
 └──────────────────────┬──────────────────────────────────────┘
                        │ g_sensors  (mutex, copied in/out)
 ┌──────────────────────▼────── controlTask  prio 4, 100 Hz ──┐
 │ boot sequence BOOT→INIT→SELF_TEST→IDLE|FAULT               │
 │ position monitors → SafetyManager.evaluate()               │
 │ Supervisor.step()  (arm / drive / fault / e-stop)          │
 │ steering slew 300 °/s · MotorController · hbridgeMap()     │
 │ ActiveAeroController (baseline + lateral term + AI blend)  │
 │ servo + motor outputs · FAULT_EVENT on change              │
 └──────────────────────┬──────────────────────────────────────┘
                        │ g_control / g_comms
 ┌──────────────────────▼──────── canTask  prio 3 ────────────┐
 │ RX (2 ms wait): 0x118 driver, 0x190 heartbeat,             │
 │                 0x160/0x161 AI pair, 0x180 config          │
 │ TX schedule from a 10 ms tick (see CANProtocol.md §3)      │
 │ bus-off recovery every 100 ms                              │
 └────────────────────────────────────────────────────────────┘
 loop()  serial console, 20 ms
```

Every task registers with the ESP-IDF task watchdog (3 s → panic reset). The control task additionally checks that the sensor and CAN tasks have run within `CFG_WATCHDOG_TIMEOUT_MS` (1 s) and raises the CRITICAL watchdog fault if not.

## 4. Drivers (no external libraries)

| Device | Bus / address | Notes |
|---|---|---|
| MPU6050 | I2C 0x68 | DLPF ~44 Hz, 200 Hz sample rate, ±500 °/s, ±4 g. Clone `WHO_AM_I` values accepted with a warning. Gyro bias captured at self-test (car must be still; rejected if std-dev > 2 °/s). Axis remap `IMU_AXIS_MAP/SIGN` `[PLACEHOLDER]`. |
| INA260 | I2C 0x40 | Manufacturer (0x5449) and die ID (0x227x) checked. Integrated 2 mΩ shunt: fixed 1.25 mA/bit and 1.25 mV/bit, no calibration register. **Saturates near 15 A**, which is what sets `CFG_BATTERY_OVERCURRENT_A` |
| ADS1115 | I2C 0x48 | Single-shot, ±4.096 V, 860 SPS, cycled `1,2,1,3,0`. AIN0 battery divider (`BATTERY_DIVIDER_RATIO` `[PLACEHOLDER]`), AIN1 steering feedback, AIN2 flap LR, AIN3 flap RR |
| DS18B20 | 1-Wire GPIO9 | Bit-banged with critical sections; Maxim AN187 ROM search (up to 4); CRC-checked; the 85 °C power-on value is rejected. Role mapping (`tempmap`) stored in NVS. |
| Hall ×4 | GPIO14/2/3/4 (FL/FR/RL/RR, Stage 2d), falling edge | 2 ms debounce; period → km/h with `CFG_WHEEL_DIAMETER_M`, `CFG_WHEEL_PULSES_PER_REV`; 0 after `CFG_WHEEL_STOP_TIMEOUT_MS` (1500 ms in Stage 2d) without a pulse. Stage 2d also counts rear pulses for odometry. |
| MG996R ×5 | LEDC 50 Hz, 14-bit | Pulse from per-servo `ServoCal` (angle ↔ µs); feedback raw → angle by two-point calibration. |
| BTS7960 | LEDC 20 kHz 10-bit on RPWM, LPWM level, EN | Sign-magnitude on one PWM channel (§6). |
| SN65HVD230 | TWAI TX GPIO5 / RX GPIO15 | 500 kbit/s, 32-frame queues, accept-all filter. |

## 5. State machine

```
BOOT ─boot done─► INIT ─init done─► SELF_TEST ─pass─► IDLE ◄──────────────┐
                    ▲                   └──fail──► FAULT ──faults clear 1 s─┘
                    │                                 ▲
                    │ reset (ARM released)            │ fault raised
             EMERGENCY_STOP ◄── e-stop / CRITICAL ── any state
                                                      │
 IDLE ─ARM held + driver fresh + |throttle| < 5 %─► ARMED ─neutral held 300 ms─► DRIVE
   ▲                                                  │                           │
   └──────────────────── ARM released ────────────────┴───────────────────────────┘
```

Forbidden shortcuts, asserted exhaustively in the host test: FAULT never goes directly to ARMED/DRIVE; EMERGENCY_STOP never goes directly to IDLE/ARMED/DRIVE (reset re-runs INIT and the self-test); DRIVE is reachable only from ARMED; SHUTDOWN is terminal.

| State | Steering | Motor | Aero |
|---|---|---|---|
| BOOT, INIT, SELF_TEST | failsafe 0° | off | failsafe 0° |
| IDLE | slews to 0° | off | baseline (flaps 0° below 8 km/h) |
| ARMED | follows driver | off | baseline / AI-assist |
| DRIVE | follows driver | on | baseline / AI-assist |
| FAULT, EMERGENCY_STOP | **forced** 0° | off | **forced** failsafe 0° |

## 6. Control laws (values in `vcu_logic.h` §1)

**Steering:** `target = steering‰ / 1000 × 30°`, slew-limited to 300 °/s.

**Motor (`MotorController`)**
- Throttle inside ±5 % → neutral. Forward duty = throttle × max duty (0.85).
- Negative throttle above 2 km/h → **brake** (duty 0 immediately, no ramp); at or below 2 km/h → reverse, capped at 0.40.
- Ramp 2.5 full-scale per second.
- Battery derate: linear from 3.50 V/cell down to 30 % of max duty at 3.30 V/cell.
- Current derate: above 20 A, max duty is divided by (I / 20 A), floor 0.1.
- `hbridgeMap`: forward RPWM = duty, LPWM low; reverse RPWM = 1 − |duty|, LPWM high (averages to −|duty| across the motor); disabled → EN low (coast).

**Active aero (`ActiveAeroController`, four independent flaps)**
1. Baseline `base = clamp((speed − 8 km/h) × 1 °/(km/h), 0, 45)`; 0 below 8 km/h.
2. Asymmetric term `lat = clamp(lat_g × 8°, ±10°)`: LF/LR = base − lat, RF/RR = base + lat (left-turn acceleration raises the outside, right-hand flaps — same sign convention as `ai_engine.py`).
3. AI blend in AI-assist mode only, if the recommendation passes validation: each channel moves toward the AI value by at most ±20°.
4. Rate limit 120 °/s per flap. Failsafe (safe state, aero position fault, mode 2) is applied immediately without rate limit.

## 6b. Derived metrics on the VCU

Two values are computed on the vehicle because a control loop could use them, and transmitted in CAN 0x141 at 10 Hz:

- **Wheel slip** (`slipRatioPermille`): driven axle versus reference axle, in per-mille, zero below 3 km/h.
- **Seconds to temperature limit** (`ThermalEstimator`): smoothed dT/dt extrapolated to `CFG_MOTOR_OVERTEMP_C` / `CFG_BATTERY_OVERTEMP_C`. `0xFFFF` = not trending toward the limit, 0 = already past it.

Assumptions and error sources: `DerivedMetrics.md` §1. Everything else derivable from these sensors is computed off the car (gateway and ground station) to keep the VCU's job small.

## 7. Self-test (boot and after e-stop reset)

| Bit | Check |
|---|---|
| 0x01 | MPU6050 responds and configures |
| 0x02 | INA260 manufacturer and die ID |
| 0x04 | ADS1115 responds |
| 0x08 | At least one DS18B20 found |
| 0x10 | Gyro calibration (car stationary) |
| 0x20 | Battery voltage within 9.9–14 V |
| 0x40 | Each servo feedback channel reads 50–3250 mV (wire connected), whether it comes from the ESP32 ADC or the ADS1115 |
| 0x80 | TWAI driver started |

Any failure → FAULT with `SELF_TEST_FAILED`. It stays in FAULT until an e-stop + reset re-runs a passing self-test. **This is deliberate**: the car will not arm with a missing sensor. For incremental bring-up use the bench build or the console (servo commands are allowed in IDLE **and FAULT**; the motor is always off in both).

## 8. Serial console (115 200 baud)

| Command | Purpose |
|---|---|
| `help`, `status` | Commands; full state, faults, sensors, feedback, commands, CAN counters, driver link |
| `i2cscan` | Expect 0x40, 0x48, 0x68 |
| `ds`, `tempmap <batt> <motor>` | List DS18B20 ROMs; assign roles |
| `servo <id> <deg>`, `bench off` | Move one servo (IDLE/FAULT only; position monitors paused); release. id 0 steer, 1 LF, 2 RF, 3 LR, 4 RR |
| `pulse <id> <min_us> <max_us>` | Set pulse widths at the servo's angle min/max |
| `cal <id> min` / `cal <id> max` | Capture feedback raw value at the current bench angle |
| `cal show`, `cal save`, `cal clear <id>` | Inspect, store to NVS, invalidate |
| `aero <0\|1\|2>` | Baseline / AI-assist / failsafe hold (refused in DRIVE) |
| `estop`, `reset` | Local emergency stop and reset request |

Calibration procedure: `CalibrationPlan.md`.

## 9. Storage (NVS namespace `vcu`)

`cal0` … `cal4` — `ServoCal` structs · `t_batt`, `t_motor` — DS18B20 indices. Everything else is compiled in.
