# Change log — Stage 2d (dyno track runs, AI engineer, circuit maps, camera recording)

Baseline: **Stage 2c**, the version tested on hardware. That test covered 4 halls with Hall RL on GPIO8, MG996R LF/RF with feedback, the MPU6050 and the DS18B20s.

**None of Stage 2d has run on the car yet.** Everything below passes compilation and the automated tests, and a full software rehearsal (`demo_car.py`). Section 7 lists what needs re-testing on hardware.

## 1. What Stage 2d adds

| Feature | Where |
|---|---|
| **24 stored circuits**: the 2026 calendar plus Jeddah, default Monza. The car "drives" one on the dyno. Its steering follows the corners, its rear duty follows the speed profile, and its flaps switch between straight mode and corner mode at the right places. | `SOFTWARE/tracks/`, `track_maps.h`, VCU `TrackRunner` |
| **Position on the circuit is VCU odometry**: rear hall pulses × wheel circumference, with one lap = `lap len` metres of wheel travel. The map, the flaps and the dashboard all use this one number. | `vcu_logic.h` §TrackRunner, CAN 0x145 |
| **Dyno calibration** (`dyno cal`) finds the start duty and the top rear-wheel speed. It replaces the missing motor RPM rating. | `DynoCalibrator` |
| **Tunable setup, changed only through the VCU**: 16 writable keys, staged changes apply at the start/finish line, and an ACK plus a round-robin report prove what is on the car. | `ConfigStore`, CAN 0x180/0x181/0x182 |
| **AI engineer**: inferences from the data, position-aware look-ahead aero, and proposals that go to the car only after operator approval. | `backend/app/ai_engineer.py`, `twin.py`; `AIEngineer.md` |
| **Digital twin** with a saturating aero map, which gives a per-circuit optimum flap angle | `backend/app/twin.py` |
| **Dashboard**: every measured, derived and modelled quantity, tagged by source, with scales set to the real hardware. Adds the live circuit map, flap diagram, g-g plot, VCU tunables, twin panel and demo-feed badge. | `SOFTWARE/dashboard/` |
| **Camera**: the backend relays the ESP32-CAM stream. Recordings download as .avi, as .mp4, and as a telemetry CSV covering the same time window. | `backend/app/camera_relay.py`, `app/camera/page.tsx` |
| **Stall watchdog** (fault bit 15, PWR-0x10) and **vibration RMS** | `StallDetector`, `VibrationMeter` |
| **Rehearsal tool**: a stand-in car plus a synthetic camera | `backend/tools/demo_car.py` |

## 2. Wiring changes (see `VCU_wiring_changes.txt`)

| Signal | Stage 2c (tested) | Stage 2d | Why |
|---|---|---|---|
| Hall RL | GPIO8 | **GPIO3** | GPIO8 is a strapping pin, and a magnet parked at the sensor pulls it LOW, so the board won't boot |
| Flap RR servo | — | **GPIO8** + **R9 10k to 3V3** | A servo input can't pull the strap LOW; R9 guarantees HIGH at reset |
| Motor | direct GPIO (plan abandoned) | **L298N ENA+ENB ← GPIO17**, 1 kHz, **R2 10k to GND**. IN1/IN3 → 5 V, IN2/IN4 → GND, jumpers removed | Forward only; one PWM pin. R2 keeps the motor off while the chip boots |

## 3. Firmware (`HARDWARE/esp32/main_updated_corrected__1_/`)

FW version `2.1.0-stage2d`. The CAN protocol version is still 2, because every change is additive.

- **`vcu_logic.h`**
  - `MotorController(reverse_enabled)` gains `set_limits()` and supply-current derating.
  - `l298nDuty()`.
  - `ActiveAeroController`: `baseline_override` and `set_rate_dps`.
  - New: `VibrationMeter`; `ConfigStore` / `Tunables` (bounds, idle-only, staged, immediate); `TrackRunner`; `StallDetector` (pulse-based, 2.5 s at > 25 % duty); `DynoCalibrator`.
  - New encoders and decoders for 0x145/0x146/0x147/0x181/0x182, and 0x141 is extended to DLC 8.
- **`track_maps.h`** (generated): 24 circuits × 512 samples, about 50 KB.
- **`.ino`**
  - Pins as §2.
  - Build flags `VCU_MOTOR_DRIVER_L298N` and `VCU_MOTOR_FORWARD_ONLY` (it `#error`s on any other combination).
  - `CFG_WHEEL_DIAMETER_M` = 0.070 (**assumed**, in `vcu_logic.h`) and `CFG_WHEEL_STOP_TIMEOUT_MS` = 1500.
  - The control task now runs these states: off, bench motor, dyno cal, bench laps, track drive.
  - Rear flaps switch 150 ms after the front pair, which spreads the supply load.
  - Stall lockout.
  - NVS keys `tun2d`, `track`, `laplen`, `dyn_vmax`, `dyn_start`.
- **Console**: `motor`, `sweep`, `dyno cal`, `track list|<id>`, `lap len|run [n]|stop`, `trackrun on|off`, `tune`, `set <key> <value>`, and `aero` (now through the config store).
- Compiles for ESP32-C6: 436,949 bytes (33 %), no sketch warnings.

## 4. Gateway and ground station

LoRa is now **v3**, so update the Pi and `bridge.py` together.

- **`raspberry_pi/main.py`**
  - v3 codec with a 64-byte `TELEMETRY_COMPACT` that carries track position, lap timing, dyno, vibration and config state.
  - `CONFIG_ACK` message (type 9).
  - The config report now runs its own rotation, so changed or ACKed keys go first. Before this, only the latest report was forwarded and some were lost.
  - SX1278 RegVersion check (0x42 must read 0x12).
  - MODE A polls `/api/vehicle/commands/next`.
  - `WHEEL_DIAMETER_M` = 0.070.
- **`Ground Station/bridge.py`**
  - v3 decode.
  - Builds `vcuConfig`, `configAcks` and `track` into each dashboard frame.
  - Polls approved commands and sends them as CONFIG_WRITE.
  - Fault bit 15.
- **`ground_radio_bridge.ino`**: the `# tx= rx=` status lines are now SLIP END-delimited, so they show up before the first packet arrives.

## 5. Backend (`SOFTWARE/backend/`)

- **New modules**:
  - `vcu_config.py`: key table mirrored from the firmware.
  - `tracks.py`
  - `twin.py`
  - `ai_engineer.py`
  - `camera_relay.py`
  - `services/vehicle_commands.py`: the approval outbox. It resends every 3 s, up to 6 tries, and reconciles against `CONFIG_REPORT`.
  - `routers/vehicle.py`
  - `routers/engineer.py`
- **Changed**:
  - `db.py` adds the `vehicle_commands` and `ai_suggestions` tables and a `received_at` column, with a migration.
  - `recording.py`
  - `routers/ai.py`: position-aware look-ahead while a run is active.
  - `main.py`: routers, and a camera lifespan that reads `VCU_CAMERA_URL`.
  - `requirements.txt`: adds `imageio-ffmpeg` for .mp4.
- **New tool**: `tools/demo_car.py`.

## 6. Dashboard (`SOFTWARE/dashboard/`)

- **New components**:
  - `source-tag`, a measured / derived / modelled / vcu / simulated badge on every value
  - `circuit-map`
  - `flap-diagram`
  - `gg-plot`
  - `vcu-tunables`
  - `twin-panel`
- **New support code**:
  - `lib/tracks.ts`
  - `lib/scales.ts`, the hardware-true axis ranges
  - `hooks/use-tracks.ts`
  - `hooks/use-smooth-position.ts`, dead reckoning between 5 Hz frames
  - `services/track-run-simulator.ts`, the new default simulation scenario "Dyno track run"
- **Pages rewritten**: Track Map (circuit selector → Send to car), Live Telemetry, AI Engineer, Camera.
- **Pages updated**: Vehicle Setup, Digital Twin, Home, and the top bar (supply-voltage pill, DEMO FEED badge).
- `constants/index.ts`: `LIMITS` set to the real hardware: 0–8 km/h wheel speed (rescaled from `dyno cal`), 9–13 V, 0–3 A with a warning at 2.0 A, ±30° steering, 0–45° flaps, 0–600 wheel rpm.

## 7. Must be verified on hardware before the demo

1. Hall RL on GPIO3. Boot with a magnet parked at every sensor.
2. R9 (GPIO8 pull-up) and the RR servo on GPIO8.
3. The L298N on GPIO17 with R2: both rear wheels turn forward, and the current stays under 2 A.
4. Steering servo and SG90 LR/RR. These were never tested in Stage 2c.
5. `dyno cal` on the real rollers, then set the duty floor.
6. Measure the tyre diameter and replace the assumed 70 mm in **both** places: `CFG_WHEEL_DIAMETER_M` in `vcu_logic.h` and `WHEEL_DIAMETER_M` in the Pi's `main.py`.
7. The LoRa v3 link, Pi to ground (RegVersion check passes, and the bridge decodes).
8. ESP32-CAM through the backend relay: record, then download .avi, .mp4 and CSV.

## 8. Test status at the end of Stage 2d

| Suite | Result |
|---|---|
| `HARDWARE/tests`: firmware host, protocol parity, gateway, full chain | **55 passed** |
| `SOFTWARE/backend/tests` | **53 passed** |
| `SOFTWARE/analysis` | **25 passed** |
| Dashboard `tsc --noEmit` / `next build` | clean |
| Dashboard eslint | 0 errors (3 warnings) |
| ESP32-C6 compile | clean |

- The **full-chain test** (`test_stage2d_chain.py`) runs, with real processes, the backend, `bridge.py` and the Pi gateway against a fake VCU on virtual CAN. It follows an AI proposal from approval to CONFIG_WRITE, staging, applying at the lap line, and the ACK, and checks the dashboard frames at each step.
- Tests cannot prove timing on the real ESP32, radio range, servo torque, or the behaviour of the 12 V adapter under load.

## 9. FW 2.1.1-stage2d — servo set and PCB rev (2026-10-01)

Servo set: **MG995** steering (open loop), **2× SG90** front flaps (open loop), **2× MG996R** rear flaps with pot feedback.

| Area | Change |
|---|---|
| `.ino` | `VCU_FB_WIRED_MASK` 0x06 → **0x18**. Feedback ADC pins now `PIN_FB_LR` = GPIO0 (D0) and `PIN_FB_RR` = GPIO1 (D1), matching R3/R7/C2 and R2/R8/C1 on the PCB. |
| `.ino` | Per-model default pulse ranges: MG995 steering 1150–1850 µs (±30°), flaps 1000–1500 µs (0–45°). `SERVO_MODELS` shown in `help` and `cal show`. |
| `.ino` | Servo calibration NVS keys renamed `cal<i>` → `sv<i>`. The Stage 2c calibration (MG996R on the front channels) is ignored, and the boot log says so. |
| `.ino` | CAN 0x150 (measured flap angles): channels without calibrated feedback now carry their command instead of 0. No protocol change. |
| `.ino` | ADS1115 build path (not fitted) remapped to steering / LF / RF, so it never collides with the on-chip rear feedback. |
| `vcu_logic.h` | `FW_VERSION` = 2.1.1-stage2d. |
| Backend | AI engineer flap tracking checks all four corners and reports the ones with real feedback (RL/RR). |
| Dashboard | Flap diagram: feedback dots on LR/RR, front shown as open loop (SG90). Live Telemetry aero charts use the rear pair. Simulator and `demo_car.py` mirror the VCU's reporting. |
| Tests | +2 backend tests (rear-feedback tracking; firmware wiring matches the PCB). Hardware 55, backend 55, analysis 25 pass; ESP32-C6 compile clean (437 KB). |

Hardware: the enable circuit (R11, C3, SW1) is removed and pin 37 is unconnected. The firmware never used it.

**Arduino IDE build fix (2026-10-01).** The IDE inserts auto-generated function prototypes just above the first function in the `.ino`. `fbWired()` sat above the `SensorData` struct, so `rearKmh(const SensorData&)` (added in 2d) failed with *'SensorData' does not name a type*. `fbWired()` now sits below all type definitions. The compile check now uses Arduino's own ctags (built from github.com/arduino/ctags), so prototype problems are caught. The earlier check used a stub that skipped prototype generation, which is why this slipped through.

After flashing: `pulse 0 <min> <max>` for the MG995, then `cal 3 min|max` and `cal 4 min|max` on the rear pair, then `cal save`.
