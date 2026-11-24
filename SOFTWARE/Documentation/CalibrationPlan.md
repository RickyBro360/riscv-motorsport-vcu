# Calibration Plan

Stage 2. Stage-1 version (referred to a deleted `vehicle_config.h`): `Stage1_Archive/CalibrationPlan.md`.

Every value tagged `[PLACEHOLDER]` in the code is a guess until you measure it. This document says what each one means, how to measure it, and what goes wrong if you do not. Values live in the code, not here, so this page cannot go stale:

- firmware: `vcu_logic.h` §1 (`CFG_*`) and `main_updated_corrected__1_.ino` SECTION 2 (hardware constants)
- servo calibration: stored in the VCU's NVS, captured with the console
- gateway: `main.py` SECTION 1 (`LoRaConfig`, `CanConfig`)

## 1. Mechanical and sensor constants (firmware)

| Constant | Where | How to measure | If wrong |
|---|---|---|---|
| `CFG_WHEEL_DIAMETER_M` | `vcu_logic.h` | Measure the loaded tyre diameter with callipers | Speed scales wrong everywhere, including the aero baseline |
| `CFG_WHEEL_PULSES_PER_REV` | `vcu_logic.h` | Count magnets per wheel | Speed off by an integer factor |
| `CFG_HALL_DEBOUNCE_US` | `vcu_logic.h` | Raise if speeds jump erratically at high rpm; lower if the top speed reads clipped | Missed or double-counted pulses |
| `CFG_WHEEL_STOP_TIMEOUT_MS` | `vcu_logic.h` | Must exceed the slowest pulse interval you care about (500 ms ≈ 0.4 km/h with one magnet on a 64 mm wheel) | Speed drops to 0 while still creeping |
| `IMU_AXIS_MAP`, `IMU_AXIS_SIGN` | `.ino` | Mount the IMU, then tilt the car: nose up must give positive pitch, left turn positive `gForceLat`, forward acceleration positive X | Aero asymmetry applied to the wrong side — the one error that makes the whole active-aero result meaningless |
| Gyro bias | automatic | Runs at every self-test; the car must be **still** (rejected if std-dev > 2 °/s) | Yaw drifts fast |
| (none — the INA260 has a fixed internal 2 mΩ shunt and no calibration register) | `.ino` | Verify the reading against a clamp meter at 5–10 A. **The INA260 saturates near 15 A**, so `CFG_BATTERY_OVERCURRENT_A` is set by the sensor, not the motor | Current, protection and energy figures wrong |
| `BATTERY_DIVIDER_RATIO` | `.ino` | Measure the divider resistors, then correct against a multimeter reading of the pack | Undervoltage protection triggers early or never |

## 2. Servo calibration (per servo, stored in NVS)

Captured with the console (`FirmwareArchitecture.md` §8); procedure in `BringUpGuide.md` §3.

| Field | Meaning | Method |
|---|---|---|
| `pulse_min_us`, `pulse_max_us` | PWM widths at the servo's angle limits | `pulse <id> <min> <max>`; start at 1000/2000 µs and narrow until the servo reaches the mechanical stops without straining |
| `angle_min_deg`, `angle_max_deg` | Logical range | Steering ±`CFG_STEERING_MAX_DEG`; flaps 0–45° (`CFG_WING_ANGLE_MIN/MAX_DEG`) |
| `fb_raw_at_min`, `fb_raw_at_max` | Feedback reading at those angles | `cal <id> min` / `cal <id> max` at the corresponding bench position |
| `fb_calibrated` | Set automatically when both readings are non-zero and differ by > 100 counts | — |

Until a servo is calibrated, its feedback is unusable: the VCU reports the commanded angle instead of the measured one, `AERO_MEASURED` (0x150) is not sent for that channel, and the position monitors cannot detect a jam. The calibrated mask is visible in CAN 0x171 and on the dashboard diagnostics.

**Re-calibrate** after any linkage change, servo replacement, or if `status` shows measured vs commanded drifting apart.

## 3. Aerodynamic and control constants

| Constant | Meaning | How to choose |
|---|---|---|
| `CFG_WING_ANGLE_MIN/MAX_DEG` | Flap travel | Physical limit of the linkage, measured with a protractor |
| `CFG_WING_FAILSAFE_ANGLE_DEG` | Safe angle | Lowest-drag, stable setting — verify the car is controllable with the flaps there |
| `CFG_AERO_MIN_SPEED_KMH` | Below this, flaps stay at failsafe | Set just above the speed where aero forces become measurable |
| `CFG_AERO_SPEED_GAIN_DEG_PER_KMH` | Baseline slope | Start at 1 °/(km/h) so full travel is reached near your top speed; refine from CFD (`/areas/active-aerodynamics-research`) or coast-down tests |
| `CFG_AERO_LATERAL_G_GAIN_DEG`, `CFG_AERO_LATERAL_MAX_DEG` | Asymmetric term | Begin conservative (8 °/g, ±10°); increase only after comparing cornering data with and without it |
| `CFG_WING_RATE_MAX_DPS` | Flap slew limit | Measure the servo's actual no-load speed (MG996R ≈ 0.17 s/60° ≈ 350 °/s at 6 V) and stay well under it |
| `CFG_STEERING_MAX_DEG`, `CFG_STEERING_RATE_MAX_DPS` | Steering lock and slew | Measure the road-wheel angle at full lock; set the rate so the linkage never binds |
| `CFG_SERVO_POS_ERR_MAX_DEG`, `..._PERSIST_MS` | Jam detection | Log the normal command-vs-feedback error while driving, then set the threshold above the worst honest value |
| `CFG_AI_MAX_DELTA_DEG` | How far the AI may nudge a flap | Keep small until you trust the recommendations |

## 4. Powertrain and battery limits

| Constant | How to choose | Risk if too high |
|---|---|---|
| `CFG_MOTOR_MAX_DUTY` | Start at 0.5 for early tests, raise later | Loss of control |
| `CFG_MOTOR_RAMP_PER_S` | Slow enough that the pack does not sag into undervoltage | Brown-outs |
| `CFG_MOTOR_REVERSE_MAX_DUTY`, `CFG_MOTOR_REVERSE_ENTRY_KMH` | Reverse authority and the speed below which negative throttle reverses instead of braking | Gear/driveline shock |
| `CFG_MOTOR_CURRENT_LIMIT_A` | Motor's continuous rating | Overheating |
| `CFG_BATTERY_OVERCURRENT_A` | **Lowest** of: INA260 range (~15 A), pack C-rating, BTS7960 rating, fuse. Currently 14 A, set by the sensor | Above the sensor's range the protection is blind |
| `CFG_CELL_DERATE_V`, `CFG_CELL_UNDERVOLT_V` | Standard LiPo values (3.50 / 3.30 V per cell); confirm against the cell datasheet | Permanent pack damage |
| `CFG_BATTERY_OVERTEMP_C`, `CFG_MOTOR_OVERTEMP_C` | Pack datasheet; motor datasheet or measured steady-state | Thermal damage |
| `CFG_*_PERSIST_MS` | Long enough to ignore load spikes, short enough to protect | Nuisance faults or no protection |

## 5. Timing constants

`CFG_CAN_RX_TIMEOUT_MS` (500) must stay above the Pi's heartbeat period (100 ms) with margin. `CFG_DRIVER_CMD_TIMEOUT_MS` (250) must stay above the drive client's period (20 ms at 50 Hz) with margin for Wi-Fi jitter — if you lower the client rate, raise this, and vice versa. `CFG_AI_MAX_STALENESS_MS` (500) bounds transport age only (see `SafetyArchitecture.md` §4).

## 6. Radio parameters

`LoRaConfig.frequency_hz` must match the module variant (Ra-02 = 433 MHz) and the regulation that applies to it (see `ProjectAudit_Stage2.md` §5.7). `tx_power_dbm` must respect the regulation and the module's maximum. SF/BW/CR trade range against air time — if you raise SF, lower `--lora-uplink-hz` so the duty cycle stays under ~30 % (the gateway prints the calculation at startup and warns above 50 %).

## 7. Order of work

1. Mechanical: wheel diameter, magnets, steering lock, flap travel.
2. Electrical: divider ratio, shunt value.
3. IMU orientation — verify with the car in your hands before trusting any aero behaviour.
4. Servo pulse limits, then feedback capture, then `cal save`.
5. Conservative powertrain limits; raise only with data.
6. Aero gains last, once you can log and compare runs.

Record every measured value and the date in your logbook; the report needs the numbers, not just the method.
