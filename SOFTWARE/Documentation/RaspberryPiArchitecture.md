# Raspberry Pi 4B Gateway Architecture

Stage 2. Stage-1 version (described a deleted multi-package `RaspberryPi/` tree with camera, OpenCV and AI modules): `Stage1_Archive/RaspberryPiArchitecture.md`.

**Code:** `HARDWARE/raspberry_pi/main.py` — one self-contained file.
**Role:** vehicle telemetry and communications gateway. **It is not the AI**, runs no vision, and never commands an actuator.
**Status:** IMPLEMENTED; tested with python-can's virtual bus and UDP radio substitutes; NOT YET VALIDATED on `can0` or a real SX1278.

## 1. Responsibilities

1. Receive and decode every VCU CAN frame (`CANProtocol.md`).
2. Keep the VCU informed that the gateway is alive: CAN 0x190 every 100 ms, **independent of Wi-Fi, backend or radio state**.
3. Forward fresh driver commands from `drive_client.py` (UDP) to CAN 0x118 — only when a new packet arrives, never replayed.
4. Record full-rate telemetry to microSD (CSV).
5. Deliver telemetry to the ground station: WebSocket (MODE A) or SX1278 LoRa at 5 Hz (MODE B).
6. Relay recommendations and config writes from the ground to CAN (0x160/0x161, 0x180).
7. Report its own health (real CPU, memory, SoC temperature) in diagnostics.
8. Derive what the sensors already imply — distance, wheel rpm, power, energy, Wh/km, coulomb SoC, pack resistance — so the dashboard and the logs carry measured values instead of zeros (`DerivedMetrics.md`).

## 2. File map (`main.py`)

| Section | Contents |
|---|---|
| Network config | Laptop/Pi IPs, ports, 20 Hz rate, 1 s link timeout (`WIFI_PASSWORD` is informational only and set to `SET_ME`) |
| 1 Configuration | `CanConfig`, `LoRaConfig` (433 MHz, SF7, BW250, CR4/5, 14 dBm, sync 0x1424, 5 Hz uplink) |
| 2 CAN | v2 IDs, encoders/decoders, `_round_half_away`, `MockCanInterface`, **`SocketCanInterface`**, `WifiCanInterface` (bench) |
| 3 LoRa | Envelope + CRC, payload codecs, time-on-air, SLIP, `_VehicleRadioBase`, **`SX1278SpiRadio`**, **`SerialSlipRadio`**, `UdpLoRaRadio` (bench) |
| 4 AI fetch | `fetch_engineer_station_recommendation()` — HTTP GET, returns `None` on any failure |
| 6 Telemetry | `VehicleTelemetry`, `to_dashboard_frame()`, fault decoding, host metrics, diagnostics |
| 6d/6e | `VehicleDataLogger`; drive packet codec and `DriveLinkListener` |
| 6f | `DerivedEstimator` — distance, wheel rpm, power, energy, Wh/km, coulomb SoC, pack internal resistance (`DerivedMetrics.md`) |
| 7 Backend | Session creation, `TelemetryGatewayClient` (WebSocket) |
| 8 Sources | `SampleTelemetrySource` (synthetic), `CanTelemetrySource` (accumulates decoded frames) |
| 9 Main loop | `run()` tasks, `build_arg_parser()`, `main()` |

## 3. Concurrency model (`run()`)

All tasks share one asyncio event loop. All CAN sends happen on that thread; blocking HTTP calls run in `asyncio.to_thread`.

| Task | Rate | Does | Must never |
|---|---|---|---|
| `heartbeat_task` | 10 Hz | CAN 0x190 with LoRa-ok and backend-ok bits | wait on anything network-related |
| `drive_task` | 100 Hz poll | newest UDP drive packet → 0x118; LoRa downlinks → 0x160/0x161, 0x118, 0x180 | replay an old command |
| telemetry loop | `--rate` 20 Hz | decode CAN → link freshness → CSV → WebSocket (MODE A) or LoRa uplink decimated to `--lora-uplink-hz` (MODE B); FAULT_EVENT sent immediately on change | publish frozen data when the VCU has gone quiet (> 1 s → withheld) |
| `backend_task` (MODE A) | as needed | create session, connect, reconnect with 2 → 10 s backoff | block the loop |
| `ai_task` (MODE A) | `--ai-hz` 5 Hz | `GET /api/ai/recommendation` → 0x160/0x161, only while backend connected and VCU link fresh | invent a recommendation |
| `startup_config_task` | once | `--aero-mode` → 0x180 three times | — |
| | `--wheel-diameter-m` | 0.064 | must match the firmware; affects distance, rpm and slip |
| | `--pack-capacity-ah` | 2.2 | for coulomb-counted state of charge |

**Why this matters.** The VCU flags `CAN_TIMEOUT` after 500 ms without a Pi frame and stops the motor 250 ms after the last driver command. The Stage-1 single loop awaited backend reconnects and a 1 s HTTP timeout inline and would have starved both. `test_stage2_gateway.py` runs the gateway against a fake VCU on a virtual CAN bus with the backend unreachable and asserts the largest heartbeat gap is under 300 ms.

**Known blocking point.** `SX1278SpiRadio._tx()` waits for the transmission to finish (~49 ms for telemetry, bounded). At 5 Hz this delays other tasks by up to that long, well inside both VCU timeouts.

## 4. Command-line reference

```
python main.py [options]
```

| Group | Flag | Default | Meaning |
|---|---|---|---|
| backend | `--laptop` / `--url` | none — `--laptop <ip>` or env `LAPTOP_IP` is required in MODE A | backend WebSocket (`ws://<ip>:8000/ws/telemetry`) |
| | `--http-url` | derived | REST base |
| | `--no-backend` | off | never contact the backend |
| | `--session-id` | new session | reuse a session |
| | `--ai-hz` | 5 | AI fetch rate (MODE A) |
| vehicle | `--source` | `can` | `can` or `sample` |
| | `--can-interface` | `socketcan` | `socketcan`, `wifi` (bench), `mock` |
| | `--can-channel` | `can0` | names starting `virtual` use python-can's virtual bus |
| | `--can-bitrate` | 500000 | informational; set by `ip link` |
| | `--listen-port`, `--esp32-ip`, `--esp32-port` | 5005, —, 5006 | Wi-Fi bench transport |
| | `--rate` | 20 | telemetry loop Hz |
| | `--aero-mode` | — | `baseline`, `ai`, `hold` at startup |
| driver | `--drive-port` | 5020 | UDP port for `drive_client.py`; 0 disables |
| LoRa | `--ground-link` | `direct` | `direct` (MODE A) or `lora` (MODE B) |
| | `--lora-backend` | `sx1278` | `sx1278`, `serial`, `udp` |
| | `--lora-uplink-hz` | 5 | warns if uplink > 50 % of air time |
| | `--lora-freq-hz`, `--lora-tx-dbm` | 433000000, 17 | |
| | `--lora-serial-port`, `--lora-serial-baud` | `/dev/serial0`, 115200 | serial backend |
| | `--sx1278-spi-bus`, `--sx1278-spi-cs` | **1**, 0 | SPI1: the CAN HAT owns both SPI0 chip selects (`dtoverlay=spi1-1cs`) |
| | `--sx1278-reset`, `--sx1278-dio0` | 17, 27 | BCM numbers; the SX1278 has no BUSY pin |
| | `--sx1278-rfo` | off | Set only if the module uses the RFO output instead of PA_BOOST |
| | `--ground-host`, `--ground-port`, `--radio-listen-port` | 127.0.0.1, 5010, 5011 | UDP substitute |
| misc | `--log-dir`, `--no-log` | `logs` | CSV logging |
| | `--duration-s` | 0 (forever) | stop after N s |
| | `-v` | off | once-per-second status line and event logs |

Common invocations:

```bash
# Bench, no hardware at all
python main.py --source sample --can-interface mock --no-backend -v

# MODE A on the car (can0 up, laptop backend reachable), AI-assist aero
python main.py --url ws://<laptop-ip>:8000/ws/telemetry --aero-mode ai -v

# MODE B on the car with the SX1278 on SPI
python main.py --ground-link lora --lora-backend sx1278 -v

# Stage-1 style bench: VCU firmware built with VCU_TRANSPORT_WIFI_UDP
python main.py --can-interface wifi -v
```

## 5. Dependencies

| Package | Needed for |
|---|---|
| Python ≥ 3.10 | everything (uses `X \| None` annotations) |
| `websockets` | MODE A backend connection |
| `python-can` | `socketcan` interface |
| `LoRaRF`, `spidev`, `RPi.GPIO` | `--lora-backend sx1278` (needs `dtoverlay=spi1-1cs`) |
| `pyserial` | `--lora-backend serial` |

Install on Raspberry Pi OS: `pip install websockets python-can LoRaRF spidev RPi.GPIO pyserial --break-system-packages`. Imports are lazy: a missing package only matters when that feature is selected, and produces a clear message.

## 6. On-vehicle log

`logs/vehicle_YYYYmmdd_HHMMSS.csv` — first column `wall_time_s`, then every `VehicleTelemetry` field (timestamps, state, wheel speeds, IMU, attitude, battery, temperatures, measured and target wing angles, fault flags, link status, fault severity). Written at the telemetry loop rate while the VCU link is fresh, flushed every second. Roughly 1 MB per 10 minutes at 20 Hz. Copy off with `scp pi@<pi-ip>:~/.../logs/*.csv .`.

## 7. Running as a service

See `BringUpGuide.md` §9 for a `systemd` unit that brings up `can0` and starts the gateway at boot.
