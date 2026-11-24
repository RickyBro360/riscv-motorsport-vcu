# Testing Strategy

> **SUPERSEDED / HISTORICAL RECORD (updated for documentation
> consistency).** This document describes the `Firmware/`/`RaspberryPi/`
> package layout and CMake/pytest test tree as it existed during an
> earlier integration/hardening pass. That layout, including any
> `RaspberryPi/ai/`, `wing_optimizer/`, or `opencv/` module referenced
> below, was later consolidated: firmware now lives under
> `HARDWARE/esp32/`, the Pi gateway is the single file
> `HARDWARE/raspberry_pi/main.py`, and cross-language/chain tests live
> under `HARDWARE/tests/` (pytest) alongside `SOFTWARE/backend/tests/`
> (pytest). **In the CURRENT implementation the Raspberry Pi does not run
> AI inference** — the sole AI recommendation generator is
> `SOFTWARE/backend/app/ai_engine.py`'s deterministic, rule-based
> `generate_recommendation()`. The detailed test descriptions below are
> kept as a historical record of what that earlier pass verified and are
> not a description of the current test tree or current architecture.

This document describes the CURRENT state of the repository after the
hardening/final-correctness pass, AS OF THAT PASS (see the note above for
what has since changed at the packaging level). It supersedes the version
of this file written during the original Firmware/RaspberryPi integration
pass, which described an earlier, less complete baseline (no self-test
gate, no HAL backends beyond time/watchdog/pwm, no cross-language golden
vectors).

## Firmware (host-side, C, gcc — no ESP-IDF required)

Every `Firmware/Tests/test_*.c` file is a standalone `main()` using plain
`assert()` (no test framework dependency). `Firmware/host_build/CMakeLists.txt`
is the authoritative build: it globs every non-`ESP_PLATFORM`-gated `.c`
file under `CAN/`, `Common/`, `Configuration/`, `Control/`, `Diagnostics/`,
`Drivers/`, `HAL/host_sim/`, `LoRa/`, `Logger/`, `Safety/`, `Scheduler/scheduler.c`,
`Sensors/`, `StateMachine/`, and `main/app_main.c` into a static library
(`vcu_core`), then builds+registers one executable per `Tests/test_*.c`
file automatically:

```
cd Firmware/host_build
cmake -B build -S .
cmake --build build
ctest --test-dir build --output-on-failure
```

`cmake`/`ctest` were **not available in the sandbox this hardening pass
ran in** (no `cmake` binary, no network access to install one). As a
substitute, every source file was compiled and every test binary was
built and run with a manual `gcc` invocation that mirrors the CMake
glob exactly (`-std=c11 -Wall -Wextra -pedantic`, zero warnings, all
binaries link and pass) — see SoftwareDevelopmentStatus.md for the exact
commands and output captured during this pass. The CMakeLists.txt itself
is unchanged in behavior (one addition: a `TEST_VECTORS_DIR` compile
definition, see "Cross-language protocol verification" below) and should
be used as-is once a machine with `cmake` is available; nothing about this
pass depends on the manual-gcc substitute being permanent.

Current test files (9): `test_active_aero.c`, `test_can_codec.c`,
`test_can_vectors.c`, `test_crc.c`, `test_lora_codec.c`,
`test_lora_vectors.c`, `test_safety.c`, `test_self_test_manager.c`,
`test_state_machine.c`. All 9 built cleanly and passed in this
environment (see SoftwareDevelopmentStatus.md).

### HAL backend status (host mock vs. ESP-IDF vs. hardware-validated)

All seven `Firmware/HAL/*.h` interfaces now have both backends:

| Interface | HOST MOCK IMPLEMENTATION | ESP-IDF IMPLEMENTATION |
|---|---|---|
| `hal_time.h` | `HAL/host_sim/hal_time_host.c` | `HAL/esp32/hal_time_esp32.c` |
| `hal_watchdog.h` | `HAL/host_sim/hal_watchdog_host.c` | `HAL/esp32/hal_watchdog_esp32.c` |
| `hal_pwm.h` | `HAL/host_sim/hal_pwm_host.c` | `HAL/esp32/hal_pwm_esp32.c` |
| `hal_i2c.h` | `HAL/host_sim/hal_i2c_host.c` | `HAL/esp32/hal_i2c_esp32.c` |
| `hal_spi.h` | `HAL/host_sim/hal_spi_host.c` | `HAL/esp32/hal_spi_esp32.c` |
| `hal_uart.h` | `HAL/host_sim/hal_uart_host.c` | `HAL/esp32/hal_uart_esp32.c` |
| `hal_gpio.h` | `HAL/host_sim/hal_gpio_host.c` | `HAL/esp32/hal_gpio_esp32.c` |

**HOST MOCK IMPLEMENTATION** (the `host_sim/` column): deterministic, no
real bus traffic, is what every `Firmware/Tests/*.c` binary actually links
and runs against, and is what `self_test_manager_run()` sees when the
self-test gate below runs on host.

**ESP-IDF IMPLEMENTATION** (the `esp32/` column): written against real
ESP-IDF driver APIs (`driver/i2c_master.h`, `driver/spi_master.h`, LEDC for
PWM, etc.), compiled only under `#ifdef ESP_PLATFORM`. This is
**HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED** — no ESP-IDF target build
has been run in this environment (no toolchain installed), so these files
have not been syntax-checked by any compiler, let alone bench-tested
against real silicon. "Implemented" here means "code exists and is
intended to be correct," not "has run on a board." Individual
`Drivers/*.c` real (non-mock) backends carry the same caveat — see
FirmwareArchitecture.md's status table for the full per-module breakdown.

### Self-test gate (`Safety/self_test_manager.c`)

`main/app_main.c`'s `vcu_boot()` no longer unconditionally fires
`VE_SELF_TEST_PASS`. It now calls `self_test_manager_run()`, which queries
every critical driver's actual self-test/status entry point:
- `icm42688p_self_test()` (IMU) and `as5600_self_test()` (steering +
  4 wing encoders) — a real check (WHO_AM_I / magnet-detect on real
  hardware), unconditionally `HAL_OK` on the mock backend.
- `i2c_mux_get_status()`, `ina226_get_status()`, `ads1115_get_status()`,
  `ds18b20_get_status()` — the drivers that only expose a status query,
  not a dedicated self-test.
- `.initialized` field checks for the four hall-effect wheel-speed
  sensors, the CAN transport, the LoRa transport, and the data logger,
  which expose neither.

Required boot flow (`StateMachine/vehicle_state_machine.c`, unchanged):
`BOOT -> INIT -> SELF_TEST -> {PASS -> IDLE | FAIL -> FAULT}`. A failure
pushes a `FAULT_BIT_SELF_TEST_FAIL` diagnostic event before firing
`VE_SELF_TEST_FAIL`.

`Tests/test_self_test_manager.c` exercises this directly: (1) the
deterministic-pass path on the host mock backend feeding through to
`VS_IDLE`, (2) a deinitialized steering encoder correctly failing and
routing to `VS_FAULT` (never `VS_IDLE`), (3) a never-initialized CAN
driver doing the same. **This does not validate real hardware self-test
behavior** — the underlying `self_test()`/`get_status()` calls only ever
run their mock path in this environment; a real failing sensor has not
been exercised, since that requires the ESP-IDF/hardware path above.

## Raspberry Pi (pytest)

```
cd RaspberryPi
pip install -r requirements.txt --break-system-packages
pytest tests/ -v
```

`pytest` (and therefore this entire suite) **could not actually be run in
the sandbox this hardening pass ran in** — `pip install pytest` failed
with no matching distribution (no network access; not pre-installed
either). As a substitute, a small ad-hoc harness imported every
`tests/test_*.py` module and called every `test_*` function directly
(bypassing pytest's collection/fixtures/`parametrize`/`pytest.raises`
machinery). Every test that doesn't itself depend on the `pytest` module
passed; the one test that calls `pytest.raises()` directly
(`test_onnx_model_raises_without_trained_model`) and the new
parametrized `tests/test_can_lora_vectors.py` (uses
`@pytest.mark.parametrize`) could not be exercised through that harness
and were instead verified with a separate standalone script that
reproduces their assertions without the pytest API — see
SoftwareDevelopmentStatus.md for the actual output. Once `pytest` is
installed, `pytest tests/ -v` should be run for real; nothing here should
be read as "pytest was run and passed."

## Cross-language protocol verification

**This is new in this hardening pass and replaces the previous "not
implemented, documented as a gap" state.**

`Testing/test_vectors/can_vectors.json` and `Testing/test_vectors/lora_vectors.json`
are the single source of truth: hand-computed golden bytes (using
exact-binary-fraction float inputs like `12.5`/`45.75` so float32
firmware arithmetic and float64 Python arithmetic cannot diverge by
rounding) for CAN telemetry (vehicle state, wheel speed w/ signed values +
scaling + clamping, battery power), CAN AI recommendation (both frames,
including a confidence-byte truncation boundary case), and the LoRa
envelope (three message types + one CRC-corruption rejection case), plus
a shared CRC-16/CCITT-FALSE reference vector (`"123456789"` -> `0x29B1`,
the standard published check value for this polynomial/init/reflection
choice).

Both languages are checked against the literal same file, not a
hand-duplicated copy:
- **C**: `Firmware/Tests/test_can_vectors.c` / `test_lora_vectors.c` parse
  the JSON directly at test time using `Firmware/Tests/support/tiny_json.h`
  (a minimal, header-only, read-only JSON parser written specifically for
  this — not a general-purpose library, not linked into any production
  module or the ESP-IDF target build). The absolute path to
  `Testing/test_vectors/` is baked in via a `TEST_VECTORS_DIR` compile
  definition set in `host_build/CMakeLists.txt` (or passed with `-D` when
  building manually — see SoftwareDevelopmentStatus.md).
- **Python**: `RaspberryPi/tests/test_can_lora_vectors.py` loads the same
  JSON with `json.load()`.

This pass also added `RaspberryPi/can/telemetry_codec.py` — the Python
side previously had no encoder/decoder for the VCU-originated telemetry
frames (vehicle state, wheel speed, battery), only for the
Pi-originated AI-recommendation frames, so there was nothing on the
Python side to check the "CAN telemetry" vectors against. It mirrors
`can_codec.c`'s scaling/layout exactly.

**Two real cross-language bugs were found and fixed by writing these
vectors** (i.e., the vectors are doing their job, not just passing by
construction):
1. `RaspberryPi/can/codec.py`'s AI-recommendation confidence byte used
   Python's `round()` (round-half-to-even); `can_codec.c`'s equivalent is
   a plain `(uint8_t)` cast (truncation toward zero). These disagree
   exactly at `confidence * 255 == x.5` (e.g. `confidence=0.5` ->
   Python gave 128, C gives 127). Fixed by switching Python to
   truncation (`int(...)`) to match the firmware. Regression-tested by
   `ai_rec_confidence_truncation_boundary` in `can_vectors.json`.
2. `encode_ai_recommendation()`'s frame A was 5 bytes in Python vs. 6
   bytes (with an explicit zero pad byte) from `can_encode_ai_recommendation()`
   in C. Functionally harmless (`decode_ai_recommendation()` only
   requires `dlc >= 5`), but not byte-identical. Fixed by padding the
   Python frame to match the firmware's wire bytes exactly.

## Frontend / backend

Not touched by this hardening pass (in scope was Firmware self-test,
cross-language protocol tests, and documentation only — see item 5 of the
pass instructions, "DO NOT redesign"). `npx tsc --noEmit`, `npm run
build`, `npm run lint`, and `pytest backend/tests/ -v` were re-run in this
environment during this pass — see SoftwareDevelopmentStatus.md for the
actual output. No frontend or backend source file was modified.

## What still has NO test coverage in this baseline

- `main/app_main.c`'s `vcu_boot()` itself is not called from any test
  (`vcu_boot()`/`vcu_step()` have no public accessor for the resulting
  state, and adding one was out of scope for this pass — "do not add new
  features"). `Tests/test_self_test_manager.c` exercises the identical
  wiring/state-machine sequence `vcu_boot()` uses, which is the closest
  coverage this pass could add without changing `app_main.h`'s public
  surface.
- `Scheduler/scheduler_run_once_host()`'s priority-ordering behavior under
  contention (no dedicated test file)
- `Firmware/Logger/data_logger.c` (no `test_data_logger.c`)
- Any true integration test that runs Firmware C code and RaspberryPi
  Python code against each other in the same process/live transport (the
  cross-language work in this pass verifies wire-format byte agreement,
  not a live C<->Python round trip over a shared mock bus)
- Everything gated behind `ESP_PLATFORM` (`main/main.c`, `Scheduler/tasks.c`,
  every `HAL/esp32/*.c` file, every driver's real/non-mock backend) —
  **NOT HARDWARE VALIDATED**, no ESP-IDF toolchain available in this
  environment (see "Final verification" in SoftwareDevelopmentStatus.md)
