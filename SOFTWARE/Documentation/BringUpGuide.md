# Bring-Up and Full-System Run Guide (Stage 2b)

Written for the hardware actually on the bench: Waveshare **ESP32-C6-Pico** VCU with a VP230 transceiver, **Raspberry Pi 4B** with a 2-channel MCP2515 CAN HAT, two **Ai-Thinker Ra-02** (SX1278, 433 MHz) radios — one on the Pi's SPI1, one on a **classic ESP32 DevKit** at the ground station — an **ESP32-CAM**, and a laptop on the `CTRL` hotspot. **No ADS1115 yet.**

Record the result of every step (screenshot, console paste, photo). Those records are your project evidence.

**Rules for the whole process:** wheels off the ground until §9 is complete · antennas on both Ra-02s before either is powered · one new subsystem at a time · when something misbehaves, read `status` on the VCU console before changing any code.

### Network sheet (fill the blanks)

| Device | Address | How to find it |
|---|---|---|
| Wi-Fi | SSID `CTRL`, password in the camera sketch | — |
| Raspberry Pi 4B | `10.49.218.178` | `hostname -I` on the Pi |
| Laptop (backend + dashboard) | `______________` | `ipconfig` (Windows) / `ip a` (Linux) / `ipconfig getifaddr en0` (macOS) |
| ESP32-CAM | `______________` | printed on its serial monitor at boot; also `/status` |

A phone hotspot re-issues addresses; re-check the laptop and camera IPs at the start of every session.

---

## 1. Pre-power wiring checks (10 min, meter in hand, everything OFF)

1. **CAN:** ≈ 60 Ω across CANH/CANL. CANH→H, CANL→L on the HAT terminal.
2. **CAN ground:** ESP32-C6 GND → the HAT's **G** terminal. (Without it: no frames, Pi goes ERROR-PASSIVE — the fault you already hit.)
3. **Common ground** between the pack, all bucks, the Pi, the VCU, the servo rail and the BTS7960.
4. **No shorts** 3V3 / 5 V / servo rail / pack to GND.
5. **Servo feedback wipers (LF → GPIO0, RF → GPIO1):** move each servo by hand end to end with the servo supply on and the VCU off; the divider output must stay **below 3.3 V** at both ends.
6. **INA260:** pack + → IN+, IN− → distribution. Check the module's **VBUS** is connected (jumper or wire). It is the VCU's only pack-voltage source until the ADS1115 arrives.
7. **BTS7960:** R_EN/L_EN read 0 V with the ESP32-C6 held in reset (10 kΩ pull-down fitted). Level shifter on RPWM/LPWM/EN.
8. **Ra-02 on the Pi:** VCC to **3V3 (pin 1/17), never 5 V**. Antenna fitted. Same for the ground Ra-02.
9. **Servos on their own supply** (≥ 10 A, 6 V), not the Pi's 5 V.

## 2. Laptop

Python 3.11–3.13 (the pinned pydantic may not build on 3.14) and Node 20+.

```bash
# terminal 1 — backend (reachable from the Pi, so bind to all interfaces)
cd SOFTWARE/backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# terminal 2 — dashboard
cd SOFTWARE/dashboard
npm install
npm run dev          # open the URL it prints (usually http://localhost:3000)

# terminal 3 — ground-station tools (once)
cd "HARDWARE/Ground Station"
pip install pygame pyserial websockets
```

- **Firewall:** allow inbound TCP 8000 (Windows: allow Python when prompted, or `New-NetFirewallRule -DisplayName "vcu-backend" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow` in an admin PowerShell). The hotspot network is usually classed "Public" on Windows — the rule must apply to Public.
- **Laptop Wi-Fi power saving:** Windows → Power options → Wireless Adapter Settings → *Maximum Performance* (plugged in and on battery). It delays the 50 Hz drive packets.
- **Dashboard starts in Simulation Mode.** Click the mode button in the top bar until it reads **Live Mode**. In Simulation Mode everything you see is generated in the browser.

## 3. Flash the three ESP32 boards

| Board | Sketch | Arduino IDE settings | Libraries |
|---|---|---|---|
| ESP32-C6-Pico (VCU) | `HARDWARE/esp32/main_updated_corrected__1_` | **ESP32C6 Dev Module**; **USB CDC On Boot: Enabled** (see note) | none |
| Classic ESP32 DevKit (ground radio) | `HARDWARE/Ground Station/ground_radio_bridge` | **ESP32 Dev Module** | RadioLib 7.x |
| ESP32-CAM + MB board | `HARDWARE/esp32_cam/esp32_cam_stream` | **AI Thinker ESP32-CAM** | none |

esp32 board package: use the current 3.3.x (this code was compile-checked on 3.3.12; the earlier Stage-2 code also built on 3.1.1). If you are on an older 3.x and get a compile error, update the board package first.

**USB CDC On Boot on the C6-Pico:** its only USB-C port is the chip's native USB. In Device Manager / `ls /dev/tty*` it shows as *USB JTAG/serial debug unit* (VID 303A). With CDC On Boot **Disabled** the firmware runs but prints to UART0 pins that are not connected to USB — the console looks dead. If your port instead shows as CH340/CH343/CP210x, use Disabled.

**VCU build options** (top of the `.ino`, SECTION 0) — as shipped:

```
#define VCU_TRANSPORT_TWAI 1      // real CAN
#define VCU_SENSORS_REAL 1        // real sensors
#define VCU_HAVE_ADS1115 0        // not fitted yet: pack V from INA260; STEER/LR/RR open-loop
#define VCU_FB_WIRED_MASK 0x06    // LF+RF pot wires on GPIO0/1
```

If the LF/RF pot wires are **not** soldered yet, set `VCU_FB_WIRED_MASK 0x00`, otherwise self-test bit 6 keeps the VCU in FAULT.

If the C6 refuses to enter download mode: hold BOOT (GPIO9), tap RESET, release BOOT. GPIO9 also carries the DS18B20 bus — its 4.7 kΩ pull-up is fine, but a sensor shorting the line will block flashing.

## 4. Raspberry Pi one-time setup

Copy the project to the Pi **without spaces in the path** (systemd and shell quoting get simpler), e.g. `/home/pi/MP`:

```bash
# from the laptop
scp -r "Major Project Hardware Software" pi@10.49.218.178:/home/pi/MP
# on the Pi
cd ~/MP/HARDWARE/raspberry_pi/setup
sudo ./pi_setup.sh
```

`pi_setup.sh` installs `can-utils python3-can python3-websockets python3-serial python3-spidev python3-rpi.gpio` + `LoRaRF`, turns **Wi-Fi power save off** (persistent through NetworkManager), and checks `/boot/firmware/config.txt`. Every line it reports as MISSING must be added **under `[all]`** (not `[pi5]` — a Pi 4 ignores that section, which is the bug you already fixed once). Template: `setup/config_txt_snippet.txt`.

**Read the crystal on the CAN HAT** (a small silver can marked 8.000 / 12.000 / 16.000). The `oscillator=` value on both mcp2515 lines must equal it. A wrong value still passes the loopback test you ran, but the bus then runs at the wrong bitrate and nothing is ever acknowledged.

Reboot after any `config.txt` change. Then `ls /dev/spidev*` must show `spidev1.0` (the Ra-02) alongside the SPI0 devices claimed by the HAT, and `dmesg | grep -i mcp251` must show both chips.

## 5. CAN link: VCU ↔ Pi

1. Power the VCU (USB to the laptop is fine for this step; open the Arduino serial monitor at 115200).
2. On the Pi:
   ```bash
   cd ~/MP/HARDWARE/raspberry_pi/setup
   sudo ./can_up.sh
   ```
   It brings up every `canX` at 500 kbit/s with automatic bus-off recovery (`restart-ms 100`), prints `canX -> spi0.Y`, listens on each for VCU frames and prints the line to use, e.g. **`VCU found on can1 -> --can-channel can1`**. Write that down — it is not necessarily `can0`, because Linux names the interfaces in probe order.
3. Watch the traffic: `candump -tA can1` (use the interface it found). Expect IDs `120 121` every 10 ms, `100 110 115 122 151` every 20 ms, `130` every 50 ms, `140 141 171` every 100 ms.

**If `can_up.sh` finds nothing** — go down this list and stop at the first failure:

| Check | How |
|---|---|
| VCU is alive and transmitting | console `status` → last line `TWAI RUNNING TEC=… REC=…`. `TEC ≥ 128` with `rx=0` = it transmits but nobody ACKs |
| Common ground | meter: C6 GND ↔ HAT G = 0 Ω |
| Oscillator matches crystal | `grep mcp2515 /boot/firmware/config.txt` vs the crystal marking |
| Wired to the other channel | `can_up.sh` already listens on both; if the Pi side shows `bus-errors` climbing on one interface, that is where the wires are |
| H/L swapped | swap and retry (only after the three above) |
| Bus-off on the Pi | `ip -details -statistics link show can1` → state; `can_up.sh` again resets it |

## 6. VCU console check

Type `status`. First line: `state=… faults=… selftest=0x..`. Self-test bits:

| Bit | Mask | Meaning | Usual cause |
|---|---|---|---|
| 0 | 0x01 | MPU6050 | not at 0x68 (AD0 must be low), SDA/SCL swapped |
| 1 | 0x02 | INA260 | not at 0x40, or a clone with a different die ID |
| 2 | 0x04 | ADS1115 | only when `VCU_HAVE_ADS1115 1` |
| 3 | 0x08 | DS18B20 | no sensor found on GPIO9 (4.7 kΩ pull-up missing) |
| 4 | 0x10 | gyro calibration | car moved during the ~1.5 s boot calibration — keep it still |
| 5 | 0x20 | battery | pack voltage outside 9.9–14 V: pack not connected, INA260 VBUS floating, or pack flat. **USB power alone always sets this** |
| 6 | 0x40 | servo feedback | a wired feedback reads < 50 mV or > 3.25 V: wire missing, servo supply off, divider wrong |
| 7 | 0x80 | CAN | TWAI driver failed to start (not a wiring fault) |

`i2cscan` must list `0x40` and `0x68` (plus `0x48` once the ADS1115 is fitted). `ds` lists the DS18B20 ROM codes — warm one with your fingers, watch which reading moves in `status`, then `tempmap <battery_index> <motor_index>` and `cal save`.

A self-test failure **latches** FAULT. After fixing the cause, either power-cycle, or type `estop` then `reset` (re-runs the self-test). Other faults clear by themselves 1 s after their cause disappears.

The target at this point, with the pack connected: `selftest=0x00`, `state=IDLE`, Battery line shows the pack voltage (INA260) within ~0.1 V of a multimeter.

## 7. Gateway, Wi-Fi mode (MODE A)

On the Pi:

```bash
cd ~/MP/HARDWARE/raspberry_pi
python3 main.py --laptop <LAPTOP_IP> --can-channel <canX> --aero-mode baseline -v
```

Expected once a second: `state=IDLE … link=fresh backend=ok`. On the dashboard (Live Mode) the Live Telemetry page updates; the status indicator shows live hardware. On the VCU, `status` now shows `Pi heartbeat seen` and `lastRx` of a few ms, and `backend=1`.

`backend=down` → from the Pi, `curl http://<LAPTOP_IP>:8000/docs` must return HTML; if not, it is the laptop firewall or the wrong IP. `link=STALE` → no CAN frames for 1 s; back to §5.

A full-rate CSV log is written to `logs/` on the Pi while the link is fresh.

## 8. Servos on the bench (linkages disconnected first)

Console, VCU in IDLE or FAULT (the motor is always off there):

1. `servo 0 0` — steering holds centre. `servo 0 -20`, `servo 0 20` — watch for binding; adjust `pulse 0 <min_us> <max_us>` until the ends are reached without straining.
2. Flaps 1–4: `servo <id> 0`, `servo <id> 45`, start with `pulse <id> 1000 1500`.
3. Feedback calibration (**LF = 1, RF = 2 only** until the ADS1115 is fitted): `servo 1 0` → `cal 1 min`, `servo 1 45` → `cal 1 max`; same for 2; `cal show`; `cal save`.
4. `bench off`, reconnect the linkages, re-check end stops.

Steering, LR and RR run open-loop for now: they move normally, but a jammed servo on those three will not be detected.

## 9. Driver link and safety drills (wheels OFF the ground)

Laptop, with the gateway from §7 running:

```bash
cd "HARDWARE/Ground Station"
python drive_client.py --pi 10.49.218.178 --input test          # steering sweeps, no throttle
python drive_client.py --pi 10.49.218.178 --max-throttle 0.25   # real input (pygame window must have focus)
```

Keyboard: A/D steer, W throttle, S brake/reverse, **hold SPACE = ARM**, E = E-STOP, R = RESET. Gamepad: left stick, triggers, hold A = ARM, B = E-STOP, X = RESET.

Hold ARM with neutral throttle → IDLE → ARMED → DRIVE after 300 ms. Small throttle → wheels spin. Release ARM → disarm immediately.

**Drills — do every one and write down what happened:**

| Drill | Expected |
|---|---|
| Release ARM at speed | motor off at once, state IDLE |
| Close `drive_client` while in DRIVE | motor off within 250 ms, COMM_LOSS fault |
| Stop the gateway (Ctrl-C) while in DRIVE | motor off within 250 ms (no driver packets), then CAN_TIMEOUT after 0.5 s |
| Pull the CAN pair while in DRIVE | same as above |
| E key / B button | E-STOP latched; R/X (with ARM released) resets and re-runs self-test |
| Walk the laptop away | disarms when Wi-Fi drops; note the distance |

If you get random disarms while standing still, Wi-Fi power save is on somewhere (Pi: `iw dev wlan0 get power_save` must say *off*; laptop: §2).

## 10. LoRa link (MODE B)

1. The ground ESP32 + Ra-02 on the laptop's USB. **Close the Arduino Serial Monitor** (only one program can own the port).
2. Laptop (backend still running):
   ```bash
   cd "HARDWARE/Ground Station"
   python bridge.py --radio serial --serial-port COM5 -v        # Windows; Linux: /dev/ttyUSB0
   ```
   Within 10 s it logs `radio modem: # tx=… rx=…` (the modem's status line). No such line = wrong port, or the modem is stuck in `# FATAL SX1278 begin` — open the Serial Monitor briefly and press EN to see its boot message (`… ready: 433.0 MHz SF7 BW250 CR4/5 10 dBm`).
3. Pi — stop the MODE A gateway first, then:
   ```bash
   python3 main.py --ground-link lora --lora-backend sx1278 --can-channel <canX> --aero-mode baseline -v
   ```
   It logs `SX1278 up: 433.000 MHz SF7 BW250k CR4/5 10 dBm`. `SX1278 did not respond on SPI` = wiring, `spi1-1cs` missing, or 5 V on VCC (the module may be damaged).
4. Expected: ~5 uplinks/s on the bridge with RSSI/SNR, dashboard updating at ~5 Hz, VCU `status` showing `lora=1`.
5. The drive client still talks to the **Pi over Wi-Fi** in this mode — same command as §9.
6. Range test: walk the car away, note RSSI and where packets start dropping.

Both radios transmit at **10 dBm**. See `ProjectAudit_Stage2.md` §5.7 before raising it.

## 11. ESP32-CAM

Power it from the car's logic buck (with 470–1000 µF across its 5 V). Its serial output prints `Dashboard URL: http://<ip>:81/stream`. Dashboard → **Live Camera** → paste the URL → Apply (remembered by the browser). Only one viewer at a time. Lag: see `HARDWARE/esp32_cam/README.md` — QVGA and JPEG quality 15–20 help most; expect 100–300 ms on a phone hotspot regardless.

## 12. Running the full system — the order every session

1. Pack charged; wheels up for the first minute of every session.
2. **Laptop:** hotspot on, laptop joined, note laptop IP → backend (`uvicorn … --host 0.0.0.0`) → dashboard → **Live Mode**.
3. **Car power on** (VCU, Pi, camera, servos). Wait ~40 s for the Pi to boot.
4. **Pi** (SSH): `sudo ~/MP/HARDWARE/raspberry_pi/setup/can_up.sh` → note the channel.
5. **Pi:** start the gateway — MODE A (§7) or MODE B (§10, plus `bridge.py` on the laptop).
6. **Dashboard:** live values, state IDLE, no FAULT. Open the camera page.
7. **Laptop:** `drive_client.py --pi 10.49.218.178 --max-throttle 0.25`.
8. ARM, check steering and a wheel spin in the air, disarm, put the car down.
9. After the run: Ctrl-C the gateway (it prints distance, energy, SoC), then copy the log:
   `scp pi@10.49.218.178:~/MP/HARDWARE/raspberry_pi/logs/*.csv .`
10. Switch to `--aero-mode ai` only once baseline runs are understood.

Optional — gateway at boot: edit and install `HARDWARE/raspberry_pi/setup/vcu-gateway.service` (instructions in the file). Only worth it once the laptop IP stops changing.

## 13. When the ADS1115 arrives

1. Wire it at 0x48 (ADDR → GND): AIN0 battery divider (10 k / 2.7 k), AIN1 steering wiper, AIN2 LR wiper, AIN3 RR wiper — all through the dividers in `PinoutAndWiring.md` §5.
2. Set `VCU_HAVE_ADS1115 1` (the feedback mask becomes `0x1F` automatically), re-flash.
3. `i2cscan` shows 0x48; compare `status` battery voltage (ADS1115) with the INA260 bus voltage — adjust `BATTERY_DIVIDER_RATIO` if they differ by > 0.1 V.
4. Feedback calibration for servos 0, 3 and 4 (§8 step 3), `cal save`.

## 14. Troubleshooting

| Symptom | Likely cause |
|---|---|
| VCU console silent | USB CDC On Boot setting (§3) |
| VCU stuck in FAULT at boot | self-test bit (§6); remember it latches until power-cycle or `estop`+`reset` |
| `selftest=0x20` on the bench | running from USB only, or INA260 VBUS not connected |
| No CAN frames on the Pi | §5 table: ground, oscillator, channel, H/L |
| Frames appear then stop; Pi `BUS-OFF` | ground or H/L intermittent; `can_up.sh` restores the link |
| Gateway `backend down` | laptop firewall / wrong `--laptop` IP / backend bound to 127.0.0.1 |
| Dashboard not changing | still in Simulation Mode; or gateway `link=STALE` (it deliberately withholds stale data) |
| Random disarms | Wi-Fi power save (Pi or laptop), weak hotspot signal |
| Motor never spins | not in DRIVE (ARM + 300 ms neutral first), level shifter missing, EN pull-down fault, FAULT active |
| Servos jitter / Pi reboots | shared or undersized servo supply |
| `Recommendations rejected` in `status` | `stale` = link too slow, `ai_mode_disabled` = not `--aero-mode ai`, `low_confidence` = the engine is unsure |
| LoRa: bridge sees nothing | antennas, both ends 433.0 MHz/SF7/BW250/sync 0x12, Pi `spidev1.0` present, Pi gateway actually in `--ground-link lora` |
| `Cannot open COMx` | Arduino Serial Monitor still open |
| `Permission denied` / `bad interpreter: ^M` running a setup script | the copy lost the execute bit or gained Windows line endings: `sudo bash can_up.sh`, or `sed -i 's/\r$//' *.sh` once |
