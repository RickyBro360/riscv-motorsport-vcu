# Firmware Architecture (ESP32-C6 VCU)

## Module map

```
Configuration  (vehicle_config.h — single source of tunables)
     |
HAL  (hal_gpio/i2c/spi/uart/pwm/watchdog/time — thin ESP-IDF wrappers, host-mocked for tests)
     |
Drivers  (ICM42688P, AS5600 x5, INA226, ADS1115, DS18B20, HallSensor x4,
          MCP2515, TJA1050(passive), SX1278, Servo x6, MicroSD)
     |
Sensors/sensor_manager.c  --owns every driver instance, only module that touches Drivers/--
     |
Control/vehicle_state_estimator.c  (simple deterministic estimator, NOT a Kalman filter)
     |
     +--> Safety/safety_manager.c  --evaluates fault monitors, drives StateMachine--
     |          |
     |          v
     +--> Control/control_manager.c  --owns all 6 servo_t instances--
                |
                +--> Control/active_aero.c  (AI-validated + physics-baseline blend)
                +--> Control/steering_control.c, motor_control.c

CAN/can_manager.c + can_protocol.h/can_codec.c  (transport-agnostic, MCP2515 mock/real)
LoRa/lora_manager.c + lora_protocol.h/lora_codec.c  (transport-agnostic, SX1278 mock/real)
Logger/data_logger.c  (MicroSD mock/real via storage_backend_t)
Diagnostics/diagnostic_events.h  (ring buffer of fault_event_t)
Scheduler/scheduler.c  (host test-loop driver) + Scheduler/tasks.c  (real FreeRTOS entry points, ESP_PLATFORM-gated)
main/app_main.c  (wires one of everything; vcu_boot()/vcu_step() are the testable core)
main/main.c  (ESP-IDF app_main() wrapper, ESP_PLATFORM-gated)
```

## Why the estimator is "simple"

`Control/vehicle_state_estimator.c` averages the four wheel speeds for
`speed_kmh`, and derives roll/pitch from static accelerometer geometry
with yaw integrated from `gyro_z_dps`. This is a documented placeholder —
not a Kalman filter, not sensor-fusion-grade. It's deliberately isolated
behind `vse_update()` so a real filter can replace the implementation
without touching any caller (Control/Safety/CAN/Logger all depend only on
the `vehicle_telemetry_t` fields it fills in, never on the estimator's
internals).

## Why active_aero.c is the safety-critical center of gravity

It's the one place an untrusted external input (the Pi's AI
recommendation) is allowed to influence an actuator target at all — see
SafetyArchitecture.md for the full validation chain. Every other control
path (steering, throttle) only ever transforms driver input.

## Scheduler model

`Scheduler/scheduler.h` documents an intended FreeRTOS priority/period
table (`Firmware/Configuration/vehicle_config.h`'s `CFG_TASK_PERIOD_*`
constants). Two implementations exist:
- `scheduler_run_once_host()` (`scheduler.c`) — a plain priority-ordered
  loop used by host tests and any future simulation harness. **No real
  preemption** — this is an intentional simplification for a codebase with
  no RTOS available on the build host.
- `Scheduler/tasks.c`'s `generic_task_entry()` — the real FreeRTOS
  per-task wrapper (watchdog register/feed + `vTaskDelayUntil`), compiled
  only under `ESP_PLATFORM` (i.e. only inside an actual ESP-IDF build).

## Build status

| Target | Status |
|---|---|
| Host-side (`gcc`, this repo's `Firmware/Tests/*.c` + all non-`ESP_PLATFORM`-gated `.c` files) | **BUILT AND PASSING** in this environment — see SoftwareDevelopmentStatus.md for the exact command line and output. |
| ESP-IDF target (`idf.py build`) | **NOT ATTEMPTED** — no ESP-IDF toolchain is installed in this environment. `main/main.c` and `Scheduler/tasks.c` are gated behind `#ifdef ESP_PLATFORM` specifically so the host build never needs to compile them; they have not been syntax-checked by any compiler. |

## What's simulated vs stubbed vs hardware-dependent (per module)

| Module | Status |
|---|---|
| StateMachine, Safety (logic), Control (logic), CAN codec, LoRa codec, Diagnostics, Logger (logic), Common/crc | IMPLEMENTED — real logic, unit-tested on host |
| Every Drivers/* file | MOCK/SIMULATED — deterministic synthetic data, no I2C/SPI/1-Wire transaction has ever reached silicon |
| HAL/host_sim/* (`hal_gpio_host.c`, `hal_i2c_host.c`, `hal_pwm_host.c`, `hal_spi_host.c`, `hal_time_host.c`, `hal_uart_host.c`, `hal_watchdog_host.c`) | HOST MOCK IMPLEMENTATION — all seven `hal_*.h` interfaces are implemented for the host build (`HAL_BACKEND_MOCK`). Deterministic, no real bus traffic (see each file's own header comment for exactly what it fakes vs. what it guarantees). This is what every `Firmware/Tests/*.c` binary actually links and runs against. |
| HAL/esp32/* (`hal_gpio_esp32.c`, `hal_i2c_esp32.c`, `hal_pwm_esp32.c`, `hal_spi_esp32.c`, `hal_time_esp32.c`, `hal_uart_esp32.c`, `hal_watchdog_esp32.c`) | ESP-IDF IMPLEMENTATION — present and written against real ESP-IDF driver APIs (`driver/i2c_master.h`, etc.), compiled only under `ESP_PLATFORM`. **HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED** — no ESP-IDF target build has been run in this environment (no toolchain installed), so these files have not even been syntax-checked by a compiler, let alone bench-tested against real silicon. Do not read "implemented" here as "working on hardware." |
| Self-test gate (`Safety/self_test_manager.c`) | IMPLEMENTED — calls each critical driver's real `self_test()`/`get_status()`/`.initialized` check (see the file's own header comment for exactly which function each driver gets). HOST MOCK IMPLEMENTATION: every check deterministically passes against the mock drivers above. On `HAL_BACKEND_REAL` a failing check correctly blocks `SELF_TEST -> IDLE` and routes to `FAULT` instead — this routing logic is unit-tested (`Tests/test_self_test_manager.c`), but has **NOT** been exercised against a real failing sensor, since that requires the ESP-IDF/hardware path above. |
| CAN/LoRa transport (`can_manager`, `lora_manager`) | IMPLEMENTED logic, HOST MOCK IMPLEMENTATION transport (MCP2515/SX1278 mock loopback: `Drivers/MCP2515/mcp2515_mock.c`, `Drivers/SX1278/sx1278_mock.c`). Real transport (`mcp2515_esp32.c`/`sx1278_esp32.c` equivalents under HAL/esp32 wiring) is HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED, same caveat as the HAL/esp32 row above. |
| CAN/LoRa cross-language codec parity | IMPLEMENTED and verified — `Firmware/Tests/test_can_vectors.c` / `test_lora_vectors.c` and `RaspberryPi/tests/test_can_lora_vectors.py` check the C and Python codecs against the literal same golden bytes in `Testing/test_vectors/*.json`. This is HOST-side byte-format verification only (both languages agree on what to put on the wire); it does not validate the physical CAN/LoRa transport. |
| main/app_main.c wiring | IMPLEMENTED and exercised both implicitly (every test links it) and directly — `vcu_boot()`'s `BOOT -> INIT -> SELF_TEST -> {IDLE|FAULT}` sequence is exercised by `Tests/test_self_test_manager.c` (via the same `sensor_manager_init`/`can_manager_init`/`lora_manager_init`/`data_logger_init` wiring `vcu_boot()` uses, plus direct state-machine assertions), though `vcu_boot()` itself is not called from a test (it has no public accessor for the resulting state — see TestingStrategy.md). |
| main/main.c, Scheduler/tasks.c | HARDWARE-DEPENDENT, NOT COMPILED (ESP_PLATFORM-gated, no toolchain here) |
