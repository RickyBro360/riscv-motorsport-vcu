# CAN Protocol — v2

Stage 2. Stage-1 (v1) version: `Stage1_Archive/CANProtocol.md`.

**Authoritative implementation:** `vcu_logic.h` §11 (firmware) and `raspberry_pi/main.py` SECTION 2 (gateway).
**Byte-level agreement is enforced by** `HARDWARE/tests/test_firmware_host.py`, which compiles the real firmware header on a PC and compares output byte for byte.
**Status:** codec IMPLEMENTED and tested; physical bus NOT YET VALIDATED.

## 1. Physical layer

| Item | Value |
|---|---|
| VCU controller | ESP32-C6 native TWAI (no MCP2515 on the VCU) |
| VCU transceiver | SN65HVD230 (3.3 V). TX = GPIO5 → D, RX = GPIO15 ← R, Rs → GND (high-speed mode) |
| Pi controller | CAN HAT with MCP2515, Linux SocketCAN interface `can0` |
| Bit rate | **500 kbit/s** both ends (`TWAI_TIMING_CONFIG_500KBITS()`; `ip link set can0 up type can bitrate 500000`) |
| Identifiers | Standard 11-bit, data frames only; extended and RTR frames are ignored |
| Termination | 120 Ω at **each** physical end (VCU end and Pi end). Measure ~60 Ω between CANH and CANL with power off |
| Cable | Twisted pair CANH/CANL + common ground, as short as practical |

## 2. Encoding rules

- All multi-byte fields are **little-endian**.
- Scaled values are rounded **half away from zero** on both ends (Stage 2 change; truncation disagreed between float32 and double — see audit G5), then clamped to the field's range.
- A receiver ignores any frame shorter than the DLC listed below.
- **v1 → v2 breaking changes:** aero angles milli-degrees → **centi-degrees**; currents milli-amps → **centi-amps**. v1 aero fields clamped at 32.767° and currents at 32.767 A.

## 3. Frame table

### VCU → Pi

| ID | Name | DLC | Period | Layout |
|---|---|---|---|---|
| 0x100 | VEHICLE_STATE | 5 | 20 ms | `state u8` · `fault_flags u32` |
| 0x110 | WHEEL_SPEED | 8 | 20 ms | `FL i16` `FR i16` `RL i16` `RR i16` — km/h × 100 |
| 0x115 | DRIVER_INPUTS | 4 | 20 ms | `steering i16` milli-deg (measured if calibrated, else commanded) · `throttle u8` % (motor duty) · `brake u8` % (100 while braking) |
| 0x120 | IMU_ACCEL | 6 | 10 ms | `x i16` `y i16` `z i16` — milli-g (X forward, Y left, Z up) |
| 0x121 | IMU_GYRO | 6 | 10 ms | `x i16` `y i16` `z i16` — deg/s × 10 |
| 0x122 | IMU_ATTITUDE | 6 | 20 ms | `roll i16` `pitch i16` `yaw i16` — deg × 10 (yaw is gyro-integrated and drifts) |
| 0x130 | BATTERY_POWER | 4 | 50 ms | `voltage u16` mV (ADS1115 divider) · `current i16` A × 100 (INA260) |
| 0x140 | TEMPERATURE | 6 | 100 ms | `motor_temp i16` °C × 10 · `battery_temp i16` °C × 10 · `motor_current i16` A × 100 (= pack current; no motor sensor in the BOM) |
| 0x141 | VEHICLE_DERIVED | 6 | 100 ms | `slip i16` per-mille (driven vs reference axle) · `seconds_to_motor_limit u16` · `seconds_to_battery_limit u16` (0xFFFF = not trending toward the limit). See `DerivedMetrics.md` |
| 0x150 | AERO_MEASURED | 8 | 20 ms, only if ≥ 1 flap feedback calibrated | `LF` `RF` `LR` `RR` i16 — deg × 100 (uncalibrated channels encode 0; see mask in 0x171) |
| 0x151 | AERO_TARGET | 8 | 20 ms | `LF` `RF` `LR` `RR` i16 — deg × 100 (rate-limited setpoint actually sent to the servos) |
| 0x170 | FAULT_EVENT | 6 | on change | `fault_flags u32` · `worst_severity u8` · `state u8` |
| 0x171 | VCU_STATUS | 8 | 100 ms | `aero_mode u8` · `last_ai_reason u8` · `driver_link_ok u8` · `fb_calibrated_mask u8` (bit0 steering, bit1 LF … bit4 RR) · `uptime u16` s · `loop_overruns u8` · `protocol_version u8` (= 2) |

### Pi → VCU

| ID | Name | DLC | When | Layout |
|---|---|---|---|---|
| 0x118 | DRIVER_COMMAND | 6 | each fresh drive packet (~50 Hz); never replayed | `sequence u8` · `steering i16` ‰ (−1000 full left … +1000 full right) · `throttle i16` ‰ (−1000 brake/reverse … +1000) · `flags u8` |
| 0x160 | AI_RECOMMENDATION_A | 6 | per recommendation (≤ 5 Hz) | `sequence u8` · `LF i16` · `RF i16` (deg × 100) · `pad u8` |
| 0x161 | AI_RECOMMENDATION_B | 6 | immediately after A | `sequence u8` · `LR i16` · `RR i16` (deg × 100) · `confidence u8` (value / 255, truncated) |
| 0x180 | CONFIG_WRITE | 5 | on demand | `key u8` · `value i32` |
| 0x190 | PI_HEARTBEAT | 2 | 100 ms, always | `counter u8` · `flags u8` (bit0 LoRa downlink seen < 3 s, bit1 backend connected) |

**Driver flags (0x118):** bit0 `ARM` — held for as long as the driver holds the arm control (deadman) · bit1 `ESTOP` · bit2 `ESTOP_RESET`.
**AI pairing:** the VCU accepts B only if its sequence equals the pending A and it arrives within `CFG_AI_PAIR_WINDOW_MS` = 100 ms. The recommendation is time-stamped on arrival of B.
**Config keys (0x180):** Stage 2d — see §3b. Every write is answered with CONFIG_ACK (0x181).

## 3b. Stage 2d frames (additive — CAN protocol version stays 2)

Old receivers ignore these IDs. Implemented in `vcu_logic.h` §10c–§11 and `raspberry_pi/main.py` SECTION 2; byte parity is checked by `test_stage2d_frames_decode_identically_on_the_pi`.

| ID | Name | Dir | DLC | Period | Layout |
|---|---|---|---|---|---|
| 0x141 | VEHICLE_DERIVED | VCU→Pi | **8** (was 6) | 100 ms | as before · **`vibration u16` milli-g RMS** (bytes 6–7) |
| 0x145 | TRACK_POSITION | VCU→Pi | 8 | 50 ms | `pos u16` (lap fraction × 65535) · `lap u16` · `track u8` · `flags u8` · `profile duty u8` % · `model lat g i8` (0.05 g) |
| 0x146 | LAP_TIMING | VCU→Pi | 8 | 200 ms + at each start/finish crossing | `last lap u32` ms · `current lap u32` ms |
| 0x147 | DYNO | VCU→Pi | 8 | 100 ms | `odometer u32` cm · `vibration u16` mg · `run state u8` · `dyno-cal phase u8` |
| 0x180 | CONFIG_WRITE | Pi→VCU | 5 | on demand | `key u8` · `value i32` (unchanged layout; many keys now) |
| 0x181 | CONFIG_ACK | VCU→Pi | 6 | reply to every 0x180 (and console `set`) | `key u8` · `status u8` · `value i32` (the value now in effect, or staged) |
| 0x182 | CONFIG_REPORT | VCU→Pi | 6 | 100 ms, one key each, round robin | `key u8` · `value i32` · `pending u8` (1 = staged changes wait for the line) |
| **0x148** | **VCU_MODE** (FW 2.2) | VCU→Pi | 7 | 100 ms | `drive mode u8` (0 DYNO · 1 TRACK) · `hw profile u8` (0 prototype · 1 PCB rev B) · `flags u8` · `run laps u8` · `TC cut u8` % · `knob i16` detents |

**TRACK_POSITION flags:** bit0 run active · bit1 straight mode (front flaps) · bit2 map braking zone · bit3 config pending · bit4 bench run (console `lap run`) · bits5–6 sector 0–2 · bit7 stalled.
**VCU_MODE flags (0x148):** bit0 motor power (BTS7004 kill switch on) · bit1 kill switch fitted · bit2 run started remotely (dashboard) · bit3 traction control cutting · bit4 ground link OK (as the VCU sees it) · bit5 track-mode aero in straight mode · bit6 encoder knob fitted.
**Run state (0x147):** 0 off · 1 track run in DRIVE · 2 bench laps · 3 bench motor · 4 dyno calibration.
**Dyno-cal phase:** 0 idle · 1 finding start duty · 2 holding the cap · 3 done · 4 failed.

**CONFIG_ACK status:** 0 applied · 1 staged (takes effect at the next start/finish crossing) · 2 rejected: out of range · 3 rejected: not allowed in this state · 4 rejected: unknown key.

**Config keys** (bounds enforced by `ConfigStore::write`; backend `app/vcu_config.py` mirrors them, a test keeps them equal):

| Key | Name | Units (wire) | Range | Applies |
|---|---|---|---|---|
| 1 | aero mode | 0 baseline · 1 AI-assist · 2 failsafe hold | 0–2 | not in DRIVE |
| 2 | track id | index into `track_maps.h` | 0–23 | only when no lap run is active and not in DRIVE |
| 3 | dyno lap length | cm of rear-wheel travel per lap | 500–100 000 | same as key 2 |
| 4 | track run in DRIVE | 0/1 | 0–1 | enabling refused in DRIVE; disabling always accepted |
| 5 | **drive mode** (FW 2.2) | 0 DYNO · 1 TRACK | 0–1 | only in IDLE with no run active; stored in NVS; boots TRACK if NVS is empty |
| 6 | **run command** (FW 2.2) | 0 stop · 1 start circuit run · 2 dyno calibration | 0–2 | COMMAND, decided now: start needs DYNO + IDLE + no fault + ground link; stop always accepted |
| 7 | **laps per dashboard run** (FW 2.2) | laps (0 = until stopped) | 0–99 | immediate |
| 8 | **e-stop** (FW 2.2) | 1 trigger · 2 reset request | 1–2 | COMMAND: trigger always accepted (latches EMERGENCY_STOP); reset only from EMERGENCY_STOP, re-runs the self-test |
| 9 | **motor** (FW 2.3) | duty ‰, 0 = stop | 0–1000 | COMMAND: any drive mode; start needs IDLE + no FAULT + ground link (the link is the deadman: lost for 1 s → stop, stays stopped). A new value while running changes the duty at once. Reported value = duty in force |
| 10 / 11 | straight-mode flap front / rear | centi-deg | 0–4500 | staged while a lap run is active, else immediate |
| 12 / 13 | corner-mode flap front / rear | centi-deg | 0–4500 | staged |
| 14 | asymmetric gain | centi-deg per modelled g (capped ±10°) | 0–500 | staged |
| 15 | straight mode opens later by | full-scale m | −100–300 | staged |
| 16 | straight mode closes early by | full-scale m | 0–400 | staged |
| 17 | motor duty cap | ‰ | 200–1000, > floor | staged |
| 18 | motor duty floor | ‰ | 0–800, < cap | staged |
| 19 | flap slew rate | deg/s | 20–300 | staged |
| 20 | steering gain | ‰ (1000 = true geometry) | 500–3000 | staged |
| 21 | supply current limit (motor derate) | centi-amps | 50–1200 | immediate (protection) |
| 22 | **TC slip target** (FW 2.2) | ‰ rear-over-front slip | 0 (off) or 50–500 | immediate |
| 23 | **centre steering** (FW 2.3) | 1 | 1 | COMMAND: knob position back to 0 (wheels straight) |
| 24 | **servo calibration** (FW 2.3) | 1 save on the car (NVS) · 2 revert to `vcu_calibration.h` | 1–2 | COMMAND |
| 25 | **DRS** (FW 2.3.3) | 1 = all flaps FLAT 5 s, all MAX 1 s, then the steering law (again = restart) · 0 = cancel. Reported value = phase 0 off / 1 flat / 2 max hold. Refused in E-STOP / boot | 0–1 | COMMAND |
| 26 | **demo sequence** (FW 2.3.4) | 1 = run the 21 s demo corner sequence, 0 = stop. Reported: 1 while running. Run state 5 = demo | 0–1 | COMMAND |
| 106 | speed source (FW 2.3.4) | 0 wheel sensors · 1 estimated from motor duty | – | READ-ONLY |
| 30 / 31 / 32 | steering pulse at full LEFT / straight / full RIGHT (FW 2.3) | µs | 500–2500 | immediate, any state; the servo moves to the new pulse for 2.5 s |
| 33–40 | flap pulse FLAT (0°) / MAX (45°) for FL, FR, RL, RR (FW 2.3; 33 FL flat, 34 FL max, … 40 RR max) | µs | 500–2500 | immediate, any state; preview as above; RL/RR also capture the pot feedback end |
| 41 | steering lock (FW 2.3) | centi-deg | 500–4500 | immediate |
| 42 / 43 | flap neutral angle / flap swing at full lock (FW 2.3) | centi-deg | 0–4500 | immediate |
| 44 | steering per knob click (FW 2.3) | centi-deg | 50–1000 | immediate |
| 45 | knob reversed (FW 2.3) | 0/1 | 0–1 | immediate |
| 100–105 | dyno vmax, dyno start duty, track count, wheel diameter, **hardware profile, wheel-sensor pulses/rev** | read-only | — | reported only |

FW 2.3: the steering angle in 0x115 is **+ = LEFT** (same as the circuit maps, the IMU lateral g and yaw). The driver packet 0x118 stays + = RIGHT; the VCU converts it.

Safety thresholds (over-temperature, over-current, undervoltage, timeouts, the remote-run link deadman) are **not** keys: nothing off the car can change them. Key 8 lets the dashboard *request* an e-stop or a reset; it cannot weaken a protection, and a reset still has to pass the self-test.

## 4. Enumerations

**`state` (0x100 byte 0, 0x170 byte 5)** — enum order is wire format:

| Value | VCU state | Dashboard `vcuState` |
|---|---|---|
| 0 | BOOT | POWER_ON |
| 1 | INIT | INITIALIZATION |
| 2 | SELF_TEST | SENSOR_CHECK |
| 3 | IDLE | IDLE |
| 4 | ARMED | IDLE (dashboard type has no ARMED) |
| 5 | DRIVE | DRIVE |
| 6 | FAULT | FAULT |
| 7 | EMERGENCY_STOP | EMERGENCY_STOP |
| 8 | SHUTDOWN | FAULT |

**`worst_severity`:** 0 none · 1 info · 2 warning · 3 fault · 4 critical.

**`fault_flags` bits:** listed with severities and reactions in `SafetyArchitecture.md` §3. Bits 0–15 are defined (Stage 2d added bit 15 `MOTOR_STALL`, WARNING: duty applied but the rear wheels are not turning — the run is aborted); the LoRa link carries the low 16 bits.

**`last_ai_reason` (0x171):** 0 accepted · 1 not_populated · 2 low_confidence · 3 future_timestamp · 4 stale · 5 non_finite · 6 out_of_range · 7 safe_state_override · 8 ai_mode_disabled · 9 none_received.

## 5. Worked examples (golden bytes)

These exact frames are asserted by `test_golden_bytes_documented_in_canprotocol_md`.

| Input | Frame (`ID:data`) | Decode |
|---|---|---|
| AI rec seq 7: LF 12.3°, RF 45°, LR 0°, RR 33.3°, confidence 0.9 | `160:07CE04941100` `161:070000020DE5` | `0x04CE` = 1230 → 12.30° · `0x1194` = 4500 → 45.00° · `0x0D02` = 3330 → 33.30° · `0xE5` = 229 → 0.898 |
| Battery 11.1 V, 34.5 A | `130:5C2B7A0D` | `0x2B5C` = 11100 mV · `0x0D7A` = 3450 → 34.50 A (v1 would have clamped at 32.767 A) |
| Aero target 0, 12.3, 33.3, 45° | `151:0000CE04020D9411` | 45.00° fits; v1 would have clamped to 32.767° |
| State DRIVE, faults 0x1001 | `100:0501100000` | state 5 · bits 0 (watchdog) and 12 (comm loss) |

## 6. Bus load

Per second from the VCU: 0x120/0x121 at 100 Hz (200 frames), six frames at 50 Hz (300), 0x130 and 0x145 at 20 Hz (40), 0x140/0x171/0x141/0x147/0x182 at 10 Hz (50), 0x146 at 5 Hz ≈ **595 frames/s** (Stage 2d). From the Pi: ~50 driver + 10 heartbeat + ≤ 10 AI ≈ 70 frames/s. At ~110 bits per 8-byte frame including stuffing, ≈ 73 kbit/s — about **15 % of 500 kbit/s**.

## 7. Bench substitute (kept)

With `VCU_TRANSPORT_WIFI_UDP 1` in the firmware and `--can-interface wifi` on the Pi, the same frames travel as UDP datagrams `[id_lo, id_hi, dlc, data×8]`: VCU → Pi port 5005, Pi → VCU port 5006. For development only.
