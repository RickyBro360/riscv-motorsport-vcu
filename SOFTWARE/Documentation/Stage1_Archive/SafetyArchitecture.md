# Safety Architecture

## Authority chain

```
Sensors (or AI, for aero)
   -> untrusted input
   -> SafetyManager / active_aero validation
   -> VCU decision (state machine + control limits)
   -> actuator-safe state
```

The AI (Raspberry Pi) **never** commands an actuator directly. Its only
output is a wing-angle recommendation on CAN
(`CAN_ID_AI_RECOMMENDATION_A/B`), and `Firmware/Control/active_aero.c` is
the sole consumer — it validates expiry, confidence, and range before
blending the recommendation (capped delta) into a physics-baseline target
that is itself angle- and rate-limited before reaching a servo. See
`Firmware/Tests/test_active_aero.c` for the enforcement tests (rejects
stale/low-confidence/malformed recommendations; never exceeds configured
angle limits; rate-limited even for accepted high-confidence input).

## State machine (Phase 6)

```
BOOT -> INIT -> SELF_TEST -> IDLE -> ARMED -> DRIVE
ANY STATE (except SHUTDOWN)      --VE_FAULT_RAISED-->   FAULT
ANY STATE (except SHUTDOWN)      --VE_CRITICAL_FAULT--> EMERGENCY_STOP
FAULT            --VE_FAULT_CLEARED-->      IDLE   (never straight to DRIVE)
EMERGENCY_STOP    --VE_ESTOP_RESET_REQUEST--> INIT  (never straight to ARMED/DRIVE)
```

Implemented in `Firmware/StateMachine/vehicle_state_machine.c`; every
transition (including the two explicitly-forbidden ones above) is asserted
in `Firmware/Tests/test_state_machine.c`.

## Fault severities and monitors (Phase 7)

| Severity | Consequence |
|---|---|
| INFO | logged only |
| WARNING | logged, no state change (e.g. CAN/LoRa link loss alone) |
| FAULT | forces `VE_FAULT_RAISED` -> FAULT state |
| CRITICAL | forces `VE_CRITICAL_FAULT` -> EMERGENCY_STOP |
| EMERGENCY | reserved for future use beyond CRITICAL (not currently triggered by any monitor) |

Monitors implemented in `Firmware/Safety/fault_monitor.c`, evaluated each
cycle by `Firmware/Safety/safety_manager.c`:

| Condition | Bit | Severity |
|---|---|---|
| Watchdog timeout | `FAULT_BIT_WATCHDOG_TIMEOUT` | CRITICAL |
| Sensor timeout | `FAULT_BIT_SENSOR_TIMEOUT` | FAULT |
| CAN timeout | `FAULT_BIT_CAN_TIMEOUT` | WARNING |
| LoRa timeout | `FAULT_BIT_LORA_TIMEOUT` | WARNING |
| Battery undervoltage | `FAULT_BIT_BATTERY_UNDERVOLT` | CRITICAL |
| Overcurrent (pack or motor) | `FAULT_BIT_OVERCURRENT` | CRITICAL |
| Motor overtemp | `FAULT_BIT_MOTOR_OVERTEMP` | FAULT |
| Battery overtemp | `FAULT_BIT_BATTERY_OVERTEMP` | FAULT |
| Invalid sensor (NaN/Inf/out-of-range) | `FAULT_BIT_INVALID_SENSOR` | FAULT |
| Invalid AI recommendation | `FAULT_BIT_INVALID_AI_REC` | (raised by caller when `active_aero_compute` returns false; not auto-raised inside SafetyManager) |
| Servo position error | `FAULT_BIT_SERVO_POSITION_ERROR` | (threshold defined, not yet wired into SafetyManager's per-cycle check — STUB) |
| Communication loss (aggregate) | `FAULT_BIT_COMM_LOSS` | (bit reserved, not yet set by any monitor — STUB) |

All thresholds live in `Firmware/Configuration/vehicle_config.h`, tagged
DEFAULT/PLACEHOLDER/REQUIRES CALIBRATION — see CalibrationPlan.md.

## Emergency stop

`Firmware/Safety/emergency_stop.c`'s `emergency_stop_execute()` drives
every actuator (steering, throttle, 4× wing) to its own configured
failsafe angle unconditionally — no branching, no partial application.
`Firmware/Control/control_manager.c`'s `control_manager_force_failsafe()`
is the path actually wired into the control loop when
`safety_permits_drive` is false.

## Watchdog (Phase 8)

`Firmware/Safety/watchdog_manager.c` implements real per-task
registration (not a boolean flag): each FreeRTOS task registers a slot at
startup, must call `watchdog_manager_feed()` every scheduler period, and
`watchdog_manager_is_healthy()` returns false the instant ANY registered
task misses `CFG_WATCHDOG_TIMEOUT_MS` (1000ms default). On-target this
wraps the ESP-IDF Task Watchdog Timer via `HAL/hal_watchdog.h`; the host
build uses a no-op HAL stub so `watchdog_manager.c`'s own logic — the part
that actually matters — is what's under test.

## What is NOT yet implemented in Safety
- Servo position-error fault is defined (`CFG_WING_POSITION_ERROR_MAX_DEG`)
  but not yet checked inside `safety_manager_evaluate()` — servo.c tracks
  the error (`servo_get_position_error()`), the wiring into SafetyManager
  is a TODO.
- Aggregate `FAULT_BIT_COMM_LOSS` is reserved but unused; CAN/LoRa are
  currently reported as separate WARNING-level bits only.
- Fault latching (the `latched` field on `fault_event_t`) is defined but
  no monitor currently sets it — every fault today is re-evaluated fresh
  each cycle rather than requiring explicit clearing.
