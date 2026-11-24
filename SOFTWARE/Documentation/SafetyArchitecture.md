# Safety Architecture

Stage 2. Stage-1 version: `Stage1_Archive/SafetyArchitecture.md`.
Implementation: `vcu_logic.h` §4 (faults, `SafetyManager`), §5 (AI validation), §9 (`Supervisor`).
Every behaviour below is asserted by `HARDWARE/tests/firmware_host_test.cpp` (self-test) or `test_firmware_host.py`.

> This is a 1:10 model car, not a certified safety system. There is no redundancy, no independent hardware interlock and no mechanical kill switch in the BOM. Treat the software limits as the *first* line of defence, and keep the wheels off the ground until you have watched each one work.

## 1. Authority chain

```
Sensor value / AI recommendation / driver command    ← all treated as untrusted input
        ▼
SafetyManager.evaluate()        faults, severity, permits_drive, aero_failsafe
        ▼
Supervisor.step()               state machine decides what is allowed at all
        ▼
Control (steering / motor / aero)  limits, ramps, blend caps
        ▼
Actuators
```

Nothing outside the VCU writes to an actuator. The Pi relays, the laptop recommends, the VCU decides.

## 2. Guarantees

| # | Guarantee | Enforced by |
|---|---|---|
| S1 | The motor runs only in DRIVE, with no fault of severity ≥ FAULT, and a driver command newer than 250 ms. | `stateAllowsMotor() && permits_drive && driverCommandFresh()` |
| S2 | DRIVE is reachable only from ARMED, which needs ARM held, a fresh driver link and \|throttle\| < 5 % held for 300 ms. | `Supervisor.step()` |
| S3 | Releasing ARM (or losing the link) disarms to IDLE. ARM is a deadman, not a toggle. | `Supervisor.step()`; `drive_client.py` sends ARM only while held |
| S4 | E-stop latches from any state. Clearing it needs ARM released **and** an explicit reset, and re-runs INIT + self-test. | `stateTransition()` |
| S5 | A fault of severity ≥ FAULT forces FAULT; it clears only after the cause has been gone for 1 s **and** ARM is released. Never straight to ARMED/DRIVE. | `Supervisor.step()` |
| S6 | In FAULT, EMERGENCY_STOP or SHUTDOWN, steering goes to 0° and the flaps to the failsafe angle immediately, without rate limiting. | `stateIsSafeOverride()`, `ActiveAeroController::compute()` |
| S7 | An AI recommendation can move a flap at most ±20° from the VCU's own baseline, always inside 0–45°, rate-limited to 120 °/s. | `ActiveAeroController::compute()` |
| S8 | A recommendation is rejected unless confidence ≥ 0.55, age ≤ 500 ms, and all four angles are finite and within 0–45°. | `aiRecommendationCheck()` |
| S9 | A recommendation is never applied in a safe-state or when aero mode ≠ AI-assist. | same |
| S10 | Configuration writes (aero mode) are refused while in DRIVE. | `canTask` |
| S11 | Loss of the Pi (no CAN frame for 500 ms) raises a warning; loss of the driver link while ARMED/DRIVE raises COMM_LOSS, which is a FAULT in DRIVE and stops the motor. | `SafetyManager::evaluate()` |
| S12 | If any VCU task stalls for 1 s, the CRITICAL watchdog fault fires; if a task stalls for 3 s the ESP-IDF task watchdog panics and resets the chip (actuators are set safe at the start of `setup()`). | control task + `esp_task_wdt` |
| S13 | Power-on and reset leave the motor bridge disabled and every servo at failsafe before anything else runs. | first lines of `setup()` |
| S14 | Transient spikes do not trip the car: undervoltage 2 s, overcurrent 150 ms, overtemperature 1 s, servo position error 400 ms of persistence. | `Persist` timers |

## 3. Fault table

Bits are wire format (CAN 0x100/0x170; the low 16 bits also travel over LoRa, so bit 15 is the last one that reaches the dashboard). Dashboard level is what `FaultPayload.level` carries — the dashboard has no separate FAULT tier, so FAULT and CRITICAL both display as `critical`.

| Bit | Code | Condition | Severity | Dashboard | Reaction |
|---|---|---|---|---|---|
| 0 | SAFE-0x01 | Sensor or CAN task has not run for 1 s | CRITICAL | critical | EMERGENCY_STOP |
| 1 | SNS-0x02 | IMU > 100 ms, power > 300 ms, or temperatures > 5 s stale (only once running) | FAULT | critical | FAULT, motor off, aero failsafe |
| 2 | CAN-0x03 | No CAN frame from the Pi for 500 ms | WARNING | warning | Logged; driving continues on the VCU's own loop |
| 3 | LORA-0x04 | Pi heartbeat reports the ground link down | WARNING | warning | Logged |
| 4 | BMS-0x05 | Pack < 9.9 V (3.30 V/cell) for 2 s | CRITICAL | critical | EMERGENCY_STOP |
| 5 | PWR-0x06 | \|Pack current\| > 35 A for 150 ms | CRITICAL | critical | EMERGENCY_STOP |
| 6 | THR-0x07 | Motor > 80 °C for 1 s | FAULT | critical | FAULT |
| 7 | BMS-0x08 | Battery > 60 °C for 1 s | FAULT | critical | FAULT |
| 8 | SNS-0x09 | NaN/Inf from a sensor, or pack voltage outside 0.5–14 V (divider disconnected) | FAULT | critical | FAULT |
| 9 | STR-0x0A | Steering feedback differs from the command by > 8° for 400 ms | FAULT | critical | FAULT (jammed or de-calibrated servo) |
| 10 | AERO-0x0B | A flap's feedback differs by > 8° for 400 ms | WARNING | warning | Flaps forced to failsafe; driving continues |
| 11 | AI-0x0C | The last AI recommendation was rejected | INFO | warning | Advisory only |
| 12 | COM-0x0D | Driver link stale while ARMED/DRIVE | FAULT in DRIVE, WARNING in ARMED | critical | Motor off; FAULT in DRIVE |
| 13 | SAFE-0x0E | Emergency stop latched | INFO | critical | Status flag while latched |
| 14 | SAFE-0x0F | Boot self-test failed | FAULT | critical | Stays in FAULT until a passing re-test |
| 15 | PWR-0x10 | **Stage 2d.** Motor duty > 25 % but no rear hall pulse for 2.5 s | WARNING | warning | Motor off, run aborted (`trackrun` switched off), flag shown for 5 s. The motor stays locked out until a new bench run is started, or in DRIVE until the throttle returns to neutral. Cause: jammed rear axle, or rollers too stiff for the duty floor |

Severity ordering: none < info < warning < fault < critical. `permits_drive` is false at ≥ FAULT; `aero_failsafe` is true at ≥ FAULT or on an aero position error.

## 4. AI rejection reasons

Reported in CAN 0x171 `last_ai_reason` and shown on the dashboard: `accepted`, `not_populated`, `low_confidence`, `future_timestamp`, `stale`, `non_finite`, `out_of_range`, `safe_state_override`, `ai_mode_disabled`, `none_received`.

**Known limitation.** Staleness is measured from the moment the CAN pair arrives at the VCU, so it bounds the *transport* age, not the age of the telemetry the recommendation was computed from. In MODE B that telemetry can be ~0.5 s old and still be accepted. This is acceptable because the AI can only nudge the flaps by ±20° from the VCU's own speed/lateral-g baseline — but do not describe the recommendation as real-time.

## 5. Failure drills to run during bring-up (wheels off the ground)

| Drill | Expected |
|---|---|
| Unplug the CAN cable while in DRIVE | Driver commands stop → COMM_LOSS → motor stops within 250 ms; car stays in a safe state |
| Close `drive_client.py` | Same; the client also sends three zero-throttle packets on exit |
| Release ARM | Immediate disarm to IDLE, motor off |
| Press E-STOP | EMERGENCY_STOP latched; flaps and steering to failsafe; reset only works after releasing ARM |
| Kill the backend / the laptop | Heartbeat and driving continue unaffected; no new recommendations; flaps fall back to the physics baseline |
| Block a flap by hand for ~1 s | AERO-0x0B, flaps to failsafe, driving continues |
| Block the steering servo | STR-0x0A → FAULT, motor off |
| Pull a DS18B20 | SNS-0x02 after 5 s → FAULT |
| **Stage 2d:** during `lap run`, hold both rear wheels | PWR-0x10 after 2.5 s → motor off, run aborted |
| **Stage 2d:** during `lap run`, type `estop` | Motor off, flaps and steering to failsafe, run ended |

Record each of these in the report: they are the evidence that the safety layer exists, not just the code.
