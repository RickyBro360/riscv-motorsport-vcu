# Hardware Integration Plan (Phase 32/36 — Mock -> Real replacement)

> **SUPERSEDED / HISTORICAL RECORD.** The `Firmware/` and `RaspberryPi/`
> package layout referenced throughout this plan (including the `ai/`,
> `wing_optimizer/`, `opencv/`, and `camera/` Pi-side modules) predates the
> project's final packaging consolidation. **In the CURRENT
> implementation, the Raspberry Pi (`HARDWARE/raspberry_pi/main.py`) is a
> telemetry/communications gateway only and does not run AI inference, an
> optimizer, or a camera/vision pipeline** — the sole AI recommendation
> generator is `SOFTWARE/backend/app/ai_engine.py`'s deterministic,
> rule-based `generate_recommendation()`. The rows below describing a
> Pi-side `RuleBasedModel` -> `ONNXModel` upgrade path are historical
> design notes, not a currently active plan; any future on-vehicle camera
> input is expected to feed the Racing Engineer Station AI rather than run
> inference locally on the Pi. The ESP32/firmware driver rows below (I2C
> sensors, MCP2515, SX1278, servos, etc.) are unaffected by this note.

Nothing below has been performed on real hardware. This documents the
intended path only.

Note on HAL status (see FirmwareArchitecture.md's fuller table): all seven
`Firmware/HAL/*.h` interfaces (`hal_gpio`, `hal_i2c`, `hal_pwm`, `hal_spi`,
`hal_time`, `hal_uart`, `hal_watchdog`) now have BOTH a `HAL/host_sim/*`
mock backend (used by every host test) AND a `HAL/esp32/*` real backend
written against ESP-IDF driver APIs. The `HAL/esp32/*` backends are
HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED — they compile only under
`ESP_PLATFORM` and no ESP-IDF target build has been run in this
environment, so "written" does not mean "tested against silicon." The
per-driver mock-to-real replacement work in the table below is a separate
layer on top of the HAL (each driver's own `.c` file deciding how to use
`hal_i2c_*`/`hal_spi_*`/etc., plus per-device register maps/timing), not
blocked on the HAL itself.

| Mock class today | Real replacement | File to change | Callers that must NOT change |
|---|---|---|---|
| `icm42688p_t` (`use_mock=true`) | Real I2C read using `HAL/hal_i2c.h` | `Firmware/Drivers/ICM42688P/icm42688p.c` | `Sensors/sensor_manager.c`, everything downstream |
| `as5600_t` ×5 (`use_mock=true`) | Real I2C read (mux-selected) | `Firmware/Drivers/AS5600/as5600.c` | `sensor_manager.c` |
| `ina226_t` | Real I2C read | `Firmware/Drivers/INA226/ina226.c` | `sensor_manager.c` |
| `ads1115_t` | Real I2C ADC read + divider math | `Firmware/Drivers/ADS1115/ads1115.c` | `sensor_manager.c` |
| `ds18b20_t` ×2 | Real 1-Wire conversion (async 750ms) | `Firmware/Drivers/DS18B20/ds18b20.c` | `sensor_manager.c` |
| `hall_sensor_t` ×4 | Real GPIO interrupt pulse counting | `Firmware/Drivers/HallSensor/hall_sensor.c` | `sensor_manager.c` |
| `mcp2515_as_transport()` mock FIFO | Real MCP2515 SPI register driver implementing the same `can_transport_t` | `Firmware/Drivers/MCP2515/mcp2515.c` | `CAN/can_manager.c`, `Safety/safety_manager.c`, `Control/*` — none touch MCP2515 directly today |
| `sx1278_as_transport()` mock queue | Real SX1278 SPI/UART driver implementing the same `lora_transport_t` | `Firmware/Drivers/SX1278/sx1278.c` | `LoRa/lora_manager.c` |
| `servo_t` (mock PWM via `hal_pwm_host.c`) | Real LEDC-backed `HAL/esp32/hal_pwm_esp32.c` (already written; HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED — no ESP-IDF build has been run against it, see TestingStrategy.md) | select via ESP-IDF build (`ESP_PLATFORM`), not a code change | `Control/control_manager.c`, `Drivers/Servo/servo.c` itself doesn't change |
| `microsd_stub_backend()` | Real SPI/SDMMC card backend implementing `storage_backend_t` | `Firmware/Drivers/MicroSD/microsd.c` | `Logger/data_logger.c` |
| Raspberry Pi `MockCanInterface` | `SocketCanInterface` (already written, STUB status) | select via `config/settings.py`'s `CanConfig.interface` | `wing_optimizer/optimizer.py`, `simulation/bridge.py` don't change |
| Raspberry Pi `MockLoRaBackend` | `SX1278Backend` (already written, STUB status) | `config/settings.py`'s `LoRaConfig.interface` | nothing else |
| Raspberry Pi `SimulationCamera`/`FileCamera` | `PiCameraSource` (raises today, needs picamera2 + real implementation) | `camera/backends.py` | `opencv/pipeline.py`, `inference/pipeline.py` |
| Raspberry Pi `RuleBasedModel` | `ONNXModel` + a trained `.onnx` file (doesn't exist yet) | `ai/models.py` | `inference/pipeline.py`, `wing_optimizer/optimizer.py` |

## What is expected to change on real hardware arrival (per the project brief)

1. Configure GPIO/I2C/SPI/UART pins (currently all `0`/placeholder in
   `Firmware/Control/control_manager.c`'s `*_cfg()` functions and
   `Sensors/sensor_manager.c`'s `sensor_manager_init()`).
2. Replace each mock driver per the table above — interface signatures do
   not change, so `Sensors/sensor_manager.c`, `Control/control_manager.c`,
   `CAN/can_manager.c`, `LoRa/lora_manager.c`, `Logger/data_logger.c`
   require **zero code changes**, only the `use_mock`/backend selection.
3. Calibrate sensors (zero offsets, ADC divider ratios) — see
   CalibrationPlan.md.
4. Configure electrical interfaces (CAN bitrate, LoRa frequency/power) —
   currently placeholder values in `RaspberryPi/config/settings.py` and
   `Firmware/Configuration/vehicle_config.h`.
5. Tune limits/gains (`CFG_AERO_*`, servo rate limits) against real vehicle
   testing.
6. Test CAN, test LoRa, test servos, test the motor, validate safety
   (re-run the Phase 27 scenario set against real telemetry), perform
   vehicle testing — none of this has been done; this software baseline
   only claims to be ready to receive real drivers, not that it has been
   validated against them.
