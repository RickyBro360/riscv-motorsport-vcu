# CAN Protocol

Status key: **IMPLEMENTED** (codec exists, unit-tested) / **SIMULATED**
(only exercised via MockCanInterface / MCP2515 mock transport) /
**HARDWARE-DEPENDENT** (needs the real MCP2515/TJA1050 bus) / **NOT YET
VALIDATED**.

This is a **software baseline**, not a final electrical CAN configuration
— IDs/periods may be revised after hardware bring-up (project brief Phase 4).

## Physical layer (HARDWARE-DEPENDENT, NOT YET VALIDATED)
MCP2515 SPI CAN controller + TJA1050 transceiver, standard 11-bit IDs,
125-500 kbps (bitrate not yet chosen/validated), 120Ω termination at each
physical bus end.

## Message map

| CAN ID | Name | Direction | Period | Payload | Status |
|---|---|---|---|---|---|
| 0x100 | VEHICLE_STATE | VCU→bus | 20ms | state(u8), fault_flags(u32 LE) | IMPLEMENTED (codec), SIMULATED (bus) |
| 0x110 | WHEEL_SPEED | VCU→bus | 20ms | 4× int16, kmh×100, LE | IMPLEMENTED, SIMULATED |
| 0x120 | IMU_ACCEL | VCU→bus | 10ms | 3× int16, milli-g, LE | IMPLEMENTED, SIMULATED |
| 0x121 | IMU_GYRO | VCU→bus | 10ms | 3× int16, deci-dps, LE | IMPLEMENTED, SIMULATED |
| 0x122 | IMU_ATTITUDE | VCU→bus | 20ms | reserved (not yet encoded) | STUB (ID reserved only) |
| 0x130 | BATTERY_POWER | VCU→bus | 50ms | voltage(u16 mV), current(int16 mA), LE | IMPLEMENTED, SIMULATED |
| 0x140 | TEMPERATURE | VCU→bus | 100ms | reserved (not yet encoded) | STUB (ID reserved only) |
| 0x150 | AERO_MEASURED | VCU→bus | 20ms | reserved (not yet encoded) | STUB |
| 0x151 | AERO_TARGET | VCU→bus | 20ms | 4× int16, milli-deg, LE | IMPLEMENTED, SIMULATED |
| 0x160 | AI_RECOMMENDATION_A | Pi→VCU | 50ms | seq(u8), LF(int16), RF(int16), LE | IMPLEMENTED, SIMULATED |
| 0x161 | AI_RECOMMENDATION_B | Pi→VCU | 50ms | seq(u8), LR(int16), RR(int16), confidence(u8) | IMPLEMENTED, SIMULATED |
| 0x170 | FAULT_FLAGS | VCU→bus | event | reserved (folded into 0x100 today) | STUB (ID reserved only) |
| 0x180 | CONFIG_WRITE | ground→VCU | reserved | not implemented | STUB, non-safety-critical only when built |

Fields not marked "reserved" round-trip correctly through
`Firmware/CAN/can_codec.c` (see `Firmware/Tests/test_can_codec.c`).
Reserved IDs are documented placeholders so the numbering scheme is fixed
even though not every message is encoded yet.

Python-side mirrors: the AI-recommendation direction
(`RaspberryPi/can/codec.py`, tested by `RaspberryPi/tests/test_can_codec.py`)
existed before the hardening pass. `RaspberryPi/can/telemetry_codec.py`
(added in that pass) mirrors the VCU-originated telemetry direction
(VEHICLE_STATE, WHEEL_SPEED, BATTERY_POWER) — it did not exist previously,
so there was nothing on the Python side to check those three IDs' bytes
against. All of the above (both directions) are now checked against the
literal same golden bytes as the C side via
`Testing/test_vectors/can_vectors.json` — see TestingStrategy.md's
"Cross-language protocol verification" section, including two real C/Python
mismatches that exercise found and fixed.

## AI recommendation framing

A 4×int16 + confidence + sequence payload does not fit one 8-byte classic
CAN frame, so it is split across 0x160 (LF/RF) and 0x161 (LR/RR +
confidence), correlated by a shared `sequence` byte. A B-frame whose
sequence doesn't match the last A-frame is rejected as stale
(`can_decode_ai_recommendation` returns -1 / `decode_ai_recommendation`
returns `None`). Frame A is `dlc=6` (5 meaningful bytes + 1 zero pad byte)
in both `can_codec.c` and `can/codec.py` — decode only requires `dlc >= 5`,
so the pad byte is not functionally required, but the hardening pass made
both encoders emit it identically (previously the Python encoder omitted
it, a byte-level, not functional, mismatch caught by the golden vectors).

## Timeout / link health
`Firmware/CAN/can_manager.c` marks the link timed out if no frame has been
received within `CFG_CAN_RX_TIMEOUT_MS` (200ms default) —
`Firmware/Safety/safety_manager.c` treats CAN loss alone as a `WARNING`,
not an emergency stop (LoRa/CAN loss don't strand the car; VCU-local
control keeps operating).
