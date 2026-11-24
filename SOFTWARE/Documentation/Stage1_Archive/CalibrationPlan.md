# Calibration Plan

Every parameter below is a placeholder pending physical measurement.
Source of truth: `Firmware/Configuration/vehicle_config.h` (firmware) and
`RaspberryPi/config/settings.py` (Pi) — this document explains *why* each
one needs calibration and *how* to measure it; it does not duplicate the
numeric values themselves (those live in the config files and would go
stale here).

| Parameter | Where | How to calibrate |
|---|---|---|
| `CFG_WHEEL_DIAMETER_M`, `CFG_WHEEL_PULSES_PER_REV` | vehicle_config.h | Measure wheel diameter directly; count hall-sensor magnets and verify pulse count against a known-distance rolling test |
| `CFG_VEHICLE_MASS_KG` | vehicle_config.h | Weigh the assembled car with battery installed |
| Battery cell limits | vehicle_config.h | Standard LiPo chemistry values (3.3V/4.2V per cell) — likely fine as-is, confirm against the specific cell datasheet |
| `CFG_BATTERY_OVERCURRENT_A`, `CFG_MOTOR_OVERCURRENT_A` | vehicle_config.h | Must not exceed the ESC/motor/battery's rated continuous current — check all three datasheets, use the lowest |
| `CFG_MOTOR_OVERTEMP_C`, `CFG_BATTERY_OVERTEMP_C` | vehicle_config.h | Motor and cell manufacturer thermal limits |
| Servo pulse widths (`pulse_us_at_min/max` in `Control/control_manager.c`'s `*_cfg()` functions) | control_manager.c | Bench-test each physical servo's actual travel range with a servo tester before wiring into the loop |
| `CFG_STEERING_RATE_MAX_DPS`, `CFG_WING_RATE_MAX_DPS` | vehicle_config.h | Measure each servo's actual slew rate under load, set below its physical maximum |
| `CFG_AERO_SPEED_GAIN`, `CFG_AERO_LATERAL_G_GAIN`, `CFG_AERO_YAW_GAIN` | vehicle_config.h | Requires real track testing — start conservative, iterate using logged telemetry (`Firmware/Logger`) vs subjective handling feedback |
| `CFG_AI_MIN_CONFIDENCE`, `CFG_AI_MAX_DELTA_DEG` | vehicle_config.h | Tune once `ai/models.py`'s `ONNXModel` has a trained model to evaluate against; meaningless to tune around `RuleBasedModel`'s deterministic output |
| Scheduler periods (`CFG_TASK_PERIOD_*`) | vehicle_config.h | Profile actual task execution time on real ESP32-C6 hardware once `idf.py build` is available; current values are placeholders based on typical control-loop rates for a project of this scope, not measured |
| `steering_center_offset_deg` | configuration.c | Mechanical trim after steering linkage is assembled |
| I2C mux channel assignments, SPI CS/reset/DIO pins, GPIO pin numbers | sensor_manager.c, control_manager.c, main/app_main.c | All currently `0`/placeholder — assign once the PCB/wiring layout is finalized |
| CAN bitrate, LoRa frequency/TX power | RaspberryPi/config/settings.py, Firmware equivalents | CAN bitrate must match on both MCP2515 nodes; LoRa frequency must match regional ISM band allocation |
