# Software Development Status — Firmware/RaspberryPi Integration Pass

> **SUPERSEDED / HISTORICAL RECORD.** This document is a point-in-time
> record of an earlier `Firmware/`/`RaspberryPi/` integration pass. That
> granular `RaspberryPi/` package (including its `ai/` model interface and
> `wing_optimizer/`) was later consolidated into the single self-contained
> `HARDWARE/raspberry_pi/main.py` gateway script. **In the CURRENT
> implementation, the Raspberry Pi is a telemetry/communications gateway
> only and does not generate AI recommendations**; the sole AI
> recommendation generator is `SOFTWARE/backend/app/ai_engine.py`'s
> deterministic, rule-based `generate_recommendation()`. This file is kept
> for historical traceability of what that earlier pass verified, not as a
> description of the current architecture — see `Architecture.md` and
> `RaspberryPiArchitecture.md` for current-vs-historical notes.

This document records what this specific pass added on top of the
pre-existing `SoftwareArchitectureStatus.md` (dashboard/backend snapshot,
unchanged) and the actual verification commands run, with real output.

## What was added

- `Firmware/` — ESP32-C6 VCU software baseline (C, ESP-IDF-shaped
  directory layout): HAL, 10 driver modules, Sensor Manager, Vehicle
  State Estimator, Control Manager + Active Aero Controller, Safety
  Manager + Watchdog Manager, State Machine, CAN protocol, LoRa protocol,
  Scheduler, Data Logger, Diagnostics, Configuration, and a host-buildable
  test suite.
- `RaspberryPi/` — Python package for the AI co-processor: camera,
  OpenCV pipeline, track analysis, AI model interface (rule-based
  default), CAN/LoRa interfaces mirroring the firmware protocol, telemetry
  adapters to the existing dashboard contract, video streaming, wing
  optimizer, simulation bridge, and a pytest suite.
- `Documentation/` additions: this file, TestingStrategy.md,
  CalibrationPlan.md, HardwareIntegration.md, SafetyArchitecture.md,
  FirmwareArchitecture.md, RaspberryPiArchitecture.md, CANProtocol.md,
  LoRaProtocol.md.

## What was explicitly NOT touched

`racing-engineer-suite/` (frontend) and `backend/` source files were not
modified. `Documentation/SoftwareArchitectureStatus.md` and
`Documentation/TelemetryProtocol.md` (pre-existing) were not modified —
they remain the authoritative record of the dashboard/backend's own
status and the frontend-facing telemetry contract respectively.

## Verification actually performed in this environment

| Check | Command | Result |
|---|---|---|
| Firmware host tests | `gcc ... Tests/test_crc.c ...` (see TestingStrategy.md for full command) | **6/6 PASS**: test_crc, test_can_codec, test_lora_codec, test_state_machine, test_safety, test_active_aero |
| Raspberry Pi tests | `pytest RaspberryPi/tests/ -v` | **21/21 PASS** |
| Frontend types | `npx tsc --noEmit` (in `racing-engineer-suite/`) | **PASS**, no errors |
| Frontend build | `npm run build` | **PASS** — all 16 routes generated |
| Frontend lint | `npm run lint` | **PASS**, 0 errors (3 pre-existing `no-explicit-any` warnings, unrelated files, unchanged by this pass) |
| Backend tests | `pytest backend/tests/ -v` | **15/15 PASS**, unchanged from before this pass |
| Firmware target build | `idf.py build` | **NOT ATTEMPTED — no ESP-IDF toolchain is installed in this environment.** This is reported honestly rather than claimed. `main/main.c` and `Scheduler/tasks.c` are `#ifdef ESP_PLATFORM`-gated specifically so they never entered the host build and were never syntax-checked by any compiler. |

## Known gaps as of THIS pass (see below, "Hardening / final-correctness pass")

The two gaps below were true when this document was first written but are
**no longer true** — corrected here rather than left stale (see the
hardening-pass section for what changed and why):
- ~~No `hal_i2c`/`hal_spi`/`hal_uart`/`hal_gpio` mock or real backend
  exists yet~~ — all seven HAL interfaces now have both a host mock
  (`HAL/host_sim/*`) and an ESP-IDF backend (`HAL/esp32/*`, HARDWARE-DEPENDENT
  / NOT HARDWARE VALIDATED). See TestingStrategy.md.
- ~~No cross-language (C ↔ Python) protocol round-trip test~~ — see
  "Hardening / final-correctness pass" below and TestingStrategy.md's
  "Cross-language protocol verification" section.

Gaps that remain accurate:
- No dedicated test calls `main/app_main.c`'s `vcu_boot()`/`vcu_step()`
  directly (compiled into every test binary, not independently tested;
  `Tests/test_self_test_manager.c` exercises the same wiring/state-machine
  sequence `vcu_boot()` uses, see TestingStrategy.md)
- `Firmware/Logger/data_logger.c` has no dedicated test file
- `FAULT_BIT_SERVO_POSITION_ERROR` and `FAULT_BIT_COMM_LOSS` are defined
  but not yet wired into `safety_manager_evaluate()`
- CAN IDs 0x122 (IMU attitude), 0x140 (temperature), 0x150 (aero
  measured), 0x170 (fault flags as its own frame), 0x180 (config write)
  are reserved in the ID map but have no codec implementation yet

## Hardening / final-correctness pass

This section records a later, narrower pass on top of everything above:
final self-test wiring, cross-language golden-byte protocol tests, and
this documentation correction — no redesign, no new product features.
See `Firmware/Safety/self_test_manager.{h,c}`,
`Testing/test_vectors/{can,lora}_vectors.json`,
`Firmware/Tests/test_can_vectors.c`, `Firmware/Tests/test_lora_vectors.c`,
`Firmware/Tests/support/tiny_json.h`,
`RaspberryPi/can/telemetry_codec.py`, and
`RaspberryPi/tests/test_can_lora_vectors.py`. Two small existing bugs were
found and fixed as part of writing the golden vectors — see
TestingStrategy.md's "Cross-language protocol verification" for detail:
a confidence-byte rounding mismatch and a 1-byte frame-padding mismatch,
both in `RaspberryPi/can/codec.py`.

Verification performed for this pass, in the same sandbox (still no
`cmake`, no `pytest`, no ESP-IDF/network access — see TestingStrategy.md
for the manual-gcc / ad-hoc-Python-harness substitutes used):

| Check | Result |
|---|---|
| Firmware host tests (9, manual `gcc -Wall -Wextra -pedantic`, zero warnings) | **9/9 PASS**: test_active_aero, test_can_codec, test_can_vectors, test_crc, test_lora_codec, test_lora_vectors, test_safety, test_self_test_manager, test_state_machine |
| Raspberry Pi tests (ad-hoc harness, `pytest` module unavailable) | All non-pytest-dependent tests pass; `test_can_lora_vectors.py`'s golden-vector assertions independently verified via a standalone script (all pass) since `pytest.mark.parametrize` couldn't run through the ad-hoc harness |
| Backend pytest | Not re-run in this pass (no backend source touched, no `pytest` available; see the original verification table above for the last real run) |
| Frontend TypeScript / build / lint | Not re-run in this pass (no frontend source touched; `npx`/`npm` toolchain not present in this sandbox) |
| ESP-IDF target build | **NOT RUN — TOOLCHAIN NOT INSTALLED.** Same as the original pass; unchanged. |

This is a materially weaker verification environment than the original
pass had (that pass reports `pytest`/`npm`/real `cmake` all working; this
one had none of them). The Firmware C results above are trustworthy (real
compiler, real execution, zero warnings). The Raspberry Pi results are
trustworthy for the specific assertions checked but were not run through
the actual `pytest` collector. The frontend/backend rows are carried
forward, unverified in this pass, because no relevant source changed.
