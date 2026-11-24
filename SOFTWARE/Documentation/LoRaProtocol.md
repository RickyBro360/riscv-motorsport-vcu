# LoRa Protocol — v3 (Stage 2d)

Stage 2. Stage-1 version: `Stage1_Archive/LoRaProtocol.md`.

**Implementations:** `raspberry_pi/main.py` SECTION 3 (vehicle) and `Ground Station/bridge.py` SECTIONS 1–2 (ground), deliberately duplicated (two machines, no shared library) and kept identical by `HARDWARE/tests/test_stage2_gateway.py` and `test_ai_recommendation_chain.py`.
**Radio firmware (ground, or optionally Pi):** `Ground Station/ground_radio_bridge/ground_radio_bridge.ino`.
**Status:** codec IMPLEMENTED and tested over the UDP substitute; radios NOT YET VALIDATED.

## 1. Radio configuration

Both ends **must** match exactly.

| Parameter | Value | Where set |
|---|---|---|
| Module | Semtech **SX1278** (Ai-Thinker Ra-02), 410-525 MHz | — |
| Frequency | **433.000 MHz** — the Ra-02's native band (SX1278 covers 410-525 MHz). De-licensed in India only at very low power with conditions — both ends run 10 dBm; verify before raising it | `LoRaConfig.frequency_hz` / `--lora-freq-hz`; `LORA_FREQ_MHZ` |
| Spreading factor | SF7 | `spreading_factor`; `LORA_SF` |
| Bandwidth | 250 kHz | `bandwidth_hz`; `LORA_BW_KHZ` |
| Coding rate | 4/5 | `coding_rate = 5`; `LORA_CR = 5` |
| Sync word | private network: **0x12** | SX127x has a single sync-word register; both ends write 0x12 |
| Preamble | 8 symbols | both |
| Header / CRC | explicit header, LoRa CRC on | both |
| TX power | 17 dBm through PA_BOOST `[PLACEHOLDER]` — check antenna and regulation | `--lora-tx-dbm`; `LORA_POWER_DBM` |
| RF switch / TCXO | none: the SX1278 switches its own RF path and runs on a crystal | — |

Why SF7/BW250: a 50-byte telemetry packet takes **48.8 ms** on air, so 5 Hz uses ~24 % of the channel and leaves time for the downlink. At SF9/BW125 the same packet takes 329 ms, which cannot sustain even 3 Hz. Trade-off: SF7/BW250 has the shortest range of the common settings; raise SF only if range tests demand it and lower `--lora-uplink-hz` accordingly.

## 2. Packet envelope

Little-endian. Maximum 75 bytes.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | magic `0xA5` |
| 1 | 1 | protocol version = **2** (v1 packets are rejected) |
| 2 | 1 | message type |
| 3 | 1 | sequence (one counter per sender, all message types) |
| 4 | 4 | timestamp ms (sender's clock: Pi = ms since gateway start) |
| 8 | 1 | payload length (0–64) |
| 9 | n | payload |
| 9+n | 2 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over bytes 0 … 8+n |

A receiver drops the packet on wrong magic, wrong version, bad length or bad CRC, and drops a packet whose sequence equals the previous one (radio retransmission duplicates). The ground side estimates loss from gaps in the uplink sequence.

## 3. Message types

| Type | Name | Direction | Payload | Size |
|---|---|---|---|---|
| 1 | TELEMETRY_COMPACT | vehicle → ground | §4 | 39 B |
| 2 | FAULT_EVENT | vehicle → ground | `fault_flags u16` | 2 B |
| 3 | CONFIG_READ_REQ | reserved | — | — |
| 4 | CONFIG_READ_RESP | reserved | — | — |
| 5 | HEARTBEAT | reserved (Stage-1 mock only) | — | — |
| 6 | AI_RECOMMENDATION | ground → vehicle | `LF i16` `RF i16` `LR i16` `RR i16` deg × 10 · `confidence u8` (/255) | 9 B |
| 7 | DRIVER_COMMAND | ground → vehicle | `steering i16` ‰ · `throttle i16` ‰ · `flags u8` | 5 B |
| 8 | CONFIG_WRITE | ground → vehicle | `key u8` · `value i32` | 5 B |
| 9 | CONFIG_ACK | vehicle → ground | `key u8` · `status u8` · `value i32` (relayed CAN 0x181, sent immediately, bypasses decimation) | 6 B |

**Safety boundary.** Stage 1 limited ground → vehicle traffic to read-only requests, with type 6 marked "simulation only". Stage 2 formalises types 6–8 on this condition: **none of them is an actuator command.** The Pi turns each into the equivalent CAN frame (0x160/0x161, 0x118, 0x180) and the VCU validates it exactly as if it had come from any other source — AI checks and the ±20° blend limit, driver freshness/ARM/neutral rules, and "no config change while DRIVE". Type 7 exists for completeness; at ≤ 5 Hz with packet loss it cannot keep the VCU's 250 ms driver timeout satisfied, so **drive over Wi-Fi** (`drive_client.py`).

## 4. TELEMETRY_COMPACT payload (v3, 64 bytes)

Struct format `<BHhhBBhhhhhhHhhhBBBBBBBBhhhhHBBBBHHBiHh`. **v3 is not compatible with v2**: flash/update the Pi and `bridge.py` together (both reject the other version's packets).

| # | Field | Type | Scale / meaning |
|---|---|---|---|
| 1 | state index | u8 | as CAN 0x100 |
| 2 | fault flags (low 16 bits) | u16 | bits 0–15 |
| 3 | speed | i16 | **km/h × 100** (v2: × 10 — dyno speeds are a few km/h) |
| 4 | steering | i16 | deg × 10 |
| 5 | throttle (motor duty) | u8 | % |
| 6 | flags2 | u8 | bit0 brake/coast · bits1–2 aero mode · bits3–6 VCU verdict on the last AI message |
| 7–9 | accel x, y, z | i16 | milli-g |
| 10–12 | roll, pitch, yaw | i16 | deg × 10 |
| 13 | supply voltage | u16 | mV |
| 14 | supply current | i16 | A × 100 (v2 also sent a "motor current" copy of this; dropped) |
| 15 | motor temperature | i16 | °C × 10 |
| 16 | battery temperature | i16 | °C × 10 |
| 17–20 | flap LF, RF, LR, RR measured | u8 | deg × 5 (0.2° steps) |
| 21–24 | flap LF, RF, LR, RR VCU target | u8 | deg × 5 |
| 25–28 | wheel FL, FR, RL, RR | i16 | km/h × 100 |
| 29 | track position | u16 | lap fraction × 65535 |
| 30 | lap | u8 | completed laps (wraps at 256) |
| 31 | track id | u8 | index into the circuit library |
| 32 | track flags | u8 | as CAN 0x145 |
| 33 | run / cal | u8 | bits0–2 run state · bits3–5 dyno-cal phase · **bit6 drive mode (1 = TRACK) · bit7 motor power (kill switch on)** — FW 2.2, same packet size, old bridges simply ignore bits 6–7 |
| 34 | current lap | u16 | 10 ms units |
| 35 | last lap | u16 | 10 ms units |
| 36–37 | config report | u8 + i32 | ONE VCU config key/value per packet; the Pi rotates through its table and sends a value that just changed (or was just ACKed) first |
| 38 | vibration | u16 | milli-g RMS |
| 39 | yaw rate | i16 | deg/s × 10 |

Time on air at SF7/BW250: 75-byte packet ≈ 66 ms; 5 Hz uses ~33 % of the channel. Not carried (Pi CSV only): odometer, modelled lateral g, profile duty, pack resistance, full-rate IMU.

## 5. Link timing

```
Pi (vehicle)       TX telemetry 48.8 ms ─┐              ┌─ TX telemetry ─┐
                                          │  RX window   │                │
Ground bridge                RX ──────────┘ TX downlink ─┘ (≤ 1 per uplink)
                                          28.3 ms
```

- The Pi transmits TELEMETRY_COMPACT at `--lora-uplink-hz` (default 5 Hz). FAULT_EVENT bypasses the rate limit whenever the flags change.
- The SX1278 is half-duplex. The ground bridge therefore sends **at most one** downlink packet, **immediately after** receiving an uplink packet, when the Pi has just returned to RX. Priority: queued CONFIG_WRITE (approved AI-engineer changes, fetched from `GET /api/vehicle/commands/next`), then the latest AI recommendation if it is < 400 ms old, else a HEARTBEAT keep-alive. A recommendation is never sent twice.
- The Pi's `SX1278SpiRadio._tx()` blocks for the packet's air time (bounded at 3 × time on air, minimum 200 ms) and then returns to RX-continuous.

## 6. Serial framing (USB/UART radio modems)

`ground_radio_bridge.ino`, `SerialSlipRadio` (Pi) and `GroundSerialRadio` (laptop) carry each LoRa packet as one **SLIP** (RFC 1055) frame at 115 200 baud: `END=0xC0`, `ESC=0xDB`, `ESC_END=0xDC`, `ESC_ESC=0xDD`. After each received radio packet the modem also sends a link-statistics frame `[0xB5, rssi i16 (dBm × 10), snr i8 (dB × 4)]`. Human-readable status lines beginning with `#` are sent as their **own END-delimited frames** (Stage 2d fix: printed bare they sat in the host's SLIP buffer until the next packet, so `rx=0` never showed exactly when a dead link needed debugging). Both hosts recognise and log them instead of treating them as packets.

## 7. Compatibility matrix

| Pi radio (`--lora-backend`) | Ground radio (`bridge.py --radio`) | Works? |
|---|---|---|
| `sx1278` (SPI module on the Pi) | `serial` (ESP32 + SX1278 bridge firmware) | Yes — intended production pairing |
| `serial` (a second ESP32 bridge on the Pi's USB) | `serial` | Yes |
| `udp` | `udp` | Yes — bench substitute, no radios |
| `serial` with a transparent-mode UART LoRa module (e.g. E22-900T22S) | `serial` with the same module type | Probably — the module carries the SLIP bytes; its air settings are configured with the vendor tool, not by this code. No LINK_STATS frames, so RSSI/SNR show as 0. Untested |
| Any SX1278 end | Any SX126x module, or a transparent-mode UART module | **No.** SX126x and SX127x are different families and cannot talk to each other |


## 8. FW 2.2 additions (no format change — v3 stays)

* **run/cal byte bits 6–7** (above): the dashboard's drive-mode chip and motor-power state over LoRa. The rest of CAN 0x148 (TC cut, knob, ground-link flag, remote-run flag) is Pi CSV / MODE A only; over LoRa those fields of the `vcu` object are `null`.
* **Commands** (drive mode, run start/stop, laps, e-stop/reset) travel as ordinary CONFIG_WRITE downlink messages (keys 5–8). The bridge sends an e-stop and a run stop **before** anything else queued, and the backend outbox orders them first and re-sends every 1 s until the VCU ACKs (max 10 tries).
* Command poll interval on the Pi and the bridge: **0.25 s** (was 1 s). Measured end-to-end (dashboard button → VCU in EMERGENCY_STOP) in the virtual-car chain test: < 2.5 s over LoRa Mode B, < 1.5 s in Mode A. The driver's E key / gamepad B over the drive link is still the fastest stop (≤ 1 drive packet).
