# Testing Strategy

Stage 2. Stage-1 version (described a deleted CMake/`Firmware/` test tree): `Stage1_Archive/TestingStrategy.md`.

## 1. What is tested, and what testing cannot tell you

| Layer | Tested by | Proves | Does **not** prove |
|---|---|---|---|
| VCU logic (`vcu_logic.h`) | `HARDWARE/tests/firmware_host_test.cpp` compiled for the PC | State machine, safety manager, motor, servo maths, aero blend behave as specified | Anything about real sensors, timing or actuators |
| Wire protocols | `test_firmware_host.py` (firmware ↔ gateway), `test_stage2_gateway.py` (gateway ↔ bridge ↔ drive client) | The three codebases produce identical bytes | That a physical CAN or LoRa link carries them |
| Gateway behaviour | `test_stage2_gateway.py` with a fake VCU on a python-can **virtual** bus | Heartbeat continues while the backend is down; drive packets reach CAN; CSV logging; LoRa uplink decimation; config write path | `can0` with an MCP2515, or an SX1278 |
| AI chain | `test_ai_recommendation_chain.py`, `test_mode_a_live_recommendation.py` | Backend recommendation → radio → Pi → CAN → VCU validation, including rejection cases, against a **real** running backend | That the recommendation is aerodynamically good |
| Backend | `SOFTWARE/backend/tests/` | REST/WebSocket contracts, AI engine rules, camera signalling, **measured energy** | — |
| Offline analysis | `SOFTWARE/analysis/tests/` against synthetic logs with analytically known answers | Energy, drag, understeer, slip, laps, thermal fit, servo identification, residuals and twin error are computed correctly | That the car behaves as the model assumes |
| Dashboard | `tsc --noEmit`, `eslint` | Type and lint correctness | Visual/UX behaviour |
| Firmware build | `arduino-cli compile` | It builds for the ESP32-C6 with no warnings | That it runs on the board |

**Nothing in this repository has been executed on the physical car.** Every "pass" below is a software result.

## 2. Running everything

```bash
# firmware logic + protocol parity + gateway integration      (needs g++, python-can, pytest)
cd "HARDWARE" && python -m pytest -q tests

# backend
cd "SOFTWARE/backend" && python -m pytest -q tests

# offline analysis (standard library only)
cd "SOFTWARE/analysis" && python -m pytest -q tests

# dashboard
cd "SOFTWARE/dashboard" && npx tsc --noEmit && npx eslint .

# firmware builds
arduino-cli compile --fqbn esp32:esp32:esp32c6 "HARDWARE/esp32/main_updated_corrected__1_"
arduino-cli compile --fqbn esp32:esp32:esp32c6 "HARDWARE/Ground Station/ground_radio_bridge"   # needs RadioLib 7.x

# firmware self-test on its own (useful while editing vcu_logic.h)
g++ -std=c++17 -Wall -Wextra -Werror -I"<sketch folder>" HARDWARE/tests/firmware_host_test.cpp -o /tmp/fht && /tmp/fht selftest
```

`test_firmware_host.py` skips itself if `g++` is unavailable; `test_stage2_gateway.py` skips if `python-can` is missing.

## 3. HARDWARE test suite (38 tests)

**`test_firmware_host.py` (10)** — compiles the real firmware header and drives it through a line protocol:
firmware self-test; AI recommendation bytes identical to the gateway for 70 cases including every 0.1° step the LoRa link can deliver; the firmware decodes the gateway's frames; documented golden bytes; all VCU→Pi frames decode on the Pi with correct scaling (including 34.56 A and 45°, which v1 clamped); all Pi→VCU frames decode in the firmware with range rejection; AI acceptance matches `vcu_sim.py` across 12 edge cases (NaN, stale, future, out of range, boundary confidence); aero blend matches `vcu_sim.py` for 54 speed/state/recommendation combinations; state indices and all 15 fault bits agree across firmware, gateway and bridge.

**`test_stage2_gateway.py` (7)** — drive packet codec identical in client and gateway (and CRC rejection); SLIP interop both directions with escape bytes and arbitrary chunking; LoRa v2 currents above 32 A survive; downlink message types decode; air-time budget stays under half the channel; **MODE A** with the backend unreachable (heartbeat gap < 300 ms, counter increments, ≥ 30 driver commands forwarded with correct values, CSV log written with correct voltage and current); **MODE B** end-to-end (uplink decimated to ~5 Hz not 20 Hz, ground config write arrives as CAN 0x180, heartbeat reports the LoRa link up).

**`test_ai_recommendation_chain.py` (18) and `test_mode_a_live_recommendation.py` (3)** — Stage-1 suites, still passing: full recommendation chain, VCU rejection in FAULT/EMERGENCY_STOP, malformed packet handling, CRC corruption, duplicate sequence drop, AI-unavailable behaviour, and a live end-to-end run against a real uvicorn backend.

## 4. Backend suite (37 tests)

`test_api.py` (sessions, frames, faults, configuration, WebSocket round trip), `test_ai_engine.py` (deterministic rules, unavailability shape), `test_camera_signal.py` (signalling relay roles), `test_energy.py` (energy integrated from recorded frames, `null` rather than a guess when the data is thin, `/api/ai/energy`).

## 4b. Analysis suite (25 tests)

Every metric in `SOFTWARE/analysis/metrics.py` is checked against a synthetic log whose answer is known by hand: 20 W for 20 s must integrate to 0.111 Wh; a coast-down built from `a = 0.3 + 0.004 v²` must return those coefficients and the matching CdA; a run needing 4°/g of extra steering must fit a +4 deg/g understeer gradient; a 10 s repeating yaw signature must yield a 10 s lap; a first-order thermal rise with τ = 120 s must recover τ and the steady state; a closed circle must dead-reckon back to its start within 2 %. Two tests assert the code **refuses** to answer — no coasting means no CdA, a straight line means no laps.

## 5. Regression rules

1. **Any change to a wire format must be made in all three places** (`vcu_logic.h`, `main.py`, `bridge.py`) and the parity tests must still pass. They are designed to fail loudly rather than let the copies drift.
2. Bump `CAN_PROTOCOL_VERSION` / `LORA_PROTOCOL_VERSION` when a layout changes; old packets are then rejected instead of silently misread.
3. Add the golden bytes for any new frame to `test_golden_bytes_documented_in_canprotocol_md` **and** to `CANProtocol.md` §5.
4. Timing-sensitive gateway tests use short durations; if you change a task's rate, update the expected counts in `test_stage2_gateway.py` rather than loosening the assertion.

## 6. Hardware test plan (not yet executed)

Software tests end where hardware begins. The on-vehicle equivalents are the step exit criteria in `HardwareIntegration.md` §2 and the failure drills in `SafetyArchitecture.md` §5. Run them in order, record the result of each, and keep the Pi CSV logs — they are the experimental evidence for the report.
