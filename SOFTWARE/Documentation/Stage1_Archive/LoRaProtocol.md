# LoRa Protocol

Status key: same as CANProtocol.md.

Separate low-bandwidth ground-link protocol, distinct from both CAN and
the dashboard's WebSocket TelemetryFrame JSON. Implemented identically
(byte-for-byte) in `Firmware/LoRa/lora_protocol.h`/`lora_codec.c` (C) and
`RaspberryPi/lora/protocol.py` (Python). Two independent verification
paths now exist: (1) each language's own round-trip test suite
(`Firmware/Tests/test_lora_codec.c`, `RaspberryPi/tests/test_lora_codec.py`),
and (2) as of the hardening pass, a genuine shared-golden-byte check —
`Testing/test_vectors/lora_vectors.json` fixes exact encoded bytes for
three envelopes (TELEMETRY_COMPACT, HEARTBEAT, FAULT_EVENT) plus one
CRC-corruption case, and `Firmware/Tests/test_lora_vectors.c` /
`RaspberryPi/tests/test_can_lora_vectors.py` both parse that same file
and assert against it — see TestingStrategy.md's "Cross-language protocol
verification" section.

## Frame layout (little-endian)

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | magic | uint8 | `0xA5` |
| 1 | version | uint8 | `1` (protocol version, checked on decode) |
| 2 | msg_type | uint8 | see message types below |
| 3 | sequence | uint8 | wraps at 256, used for duplicate detection |
| 4 | timestamp | uint32 | ms since boot, wraps ~49 days (acceptable for one session) |
| 8 | payload_len | uint8 | 0-64 |
| 9.. | payload | bytes | |
| last 2 | crc16 | uint16 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over everything before it |

## Message types

| Value | Name | Direction | Notes |
|---|---|---|---|
| 1 | TELEMETRY_COMPACT | vehicle→ground | reduced-rate telemetry, payload shape TBD alongside a real bandwidth budget |
| 2 | FAULT_EVENT | vehicle→ground | |
| 3 | CONFIG_READ_REQ | ground→vehicle | read-only |
| 4 | CONFIG_READ_RESP | vehicle→ground | |
| 5 | HEARTBEAT | either | link-quality/keepalive; the message type actually exercised by tests today |

`HEARTBEAT` has end-to-end round-trip test coverage in both languages'
own test suites (`Firmware/Tests/test_lora_codec.c`,
`RaspberryPi/tests/test_lora_codec.py`). The hardening pass's shared
golden-byte vectors additionally exercise TELEMETRY_COMPACT (with a
realistic telemetry-shaped payload) and FAULT_EVENT (including the
documented ~49-day `timestamp_ms` wrap boundary at `0xFFFFFFFF`) — see
`Testing/test_vectors/lora_vectors.json`. In every case this tests the
envelope only: `lora_manager_send()`/`lora_manager_poll()` (both
languages) take/return raw `payload` bytes, so there is still no defined,
implemented payload *schema* for TELEMETRY_COMPACT/FAULT_EVENT/CONFIG_* —
callers pack whatever bytes they want. Defining and codifying those
payload schemas remains out of scope (would be a new feature, not a
correctness fix) and is not implemented.

## Safety boundary (Phase 5, non-negotiable)

**No command in this protocol can move an actuator.** Ground→vehicle
messages are restricted to `CONFIG_READ_REQ` (read-only) by design — there
is no message type for actuator commands, and the VCU has no code path
that would interpret a LoRa payload as a servo/motor command. VCU safety
authority (`Firmware/Safety/safety_manager.c`) never consults the LoRa
link for anything beyond "is it alive" (`FAULT_BIT_LORA_TIMEOUT`, a
`WARNING`, not a control input).

## Reliability features (IMPLEMENTED, SIMULATED transport)
- **Duplicate detection**: `lora_manager_poll` drops a packet whose
  sequence matches the last accepted sequence.
- **Timeout detection**: `lora_manager_is_timed_out(now, timeout_ms)`.
- **Packet-loss stats**: `packets_sent`/`packets_received`/`duplicates_dropped`/`decode_errors`
  counters on `lora_manager_t`.

## What's simulated vs hardware-dependent
The framing, CRC, sequence/duplicate logic, and timeout detection are
IMPLEMENTED and unit-tested against `Drivers/SX1278/sx1278.c`'s in-process
mock queue transport. The real SX1278 SPI/UART bring-up
(`RaspberryPi/lora/interface.py`'s `SX1278Backend`, firmware's eventual
real `sx1278_as_transport`) is STUB / NOT HARDWARE VALIDATED — no RF link
budget, range, or interference testing has been done.
