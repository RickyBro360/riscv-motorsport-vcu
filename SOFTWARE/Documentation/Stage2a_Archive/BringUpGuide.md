# Bring-Up Guide

Stage 2 — new document. Order matters: each step assumes the previous one passed. Nothing below has been performed yet; record the result of every step, because those records are your project evidence.

**Rules for the whole process:** wheels off the ground until step 8 · antennas fitted before any radio is powered · one new subsystem at a time · when something behaves oddly, stop and check `status` on the VCU console before changing code.

---

## 0. Software on the laptop

```bash
# backend
cd SOFTWARE/backend && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# dashboard (second terminal)
cd SOFTWARE/dashboard && npm install && npm run dev      # http://localhost:8080 or as printed
```
Check `http://<laptop-ip>:8000/docs` loads from another device on the Wi-Fi; if not, open the port in the firewall. Note the laptop's IP — the Pi needs it.

## 1. VCU alone, no sensors (30 min)

1. Open the sketch folder in Arduino IDE. Board **ESP32C6 Dev Module**, *USB CDC On Boot: Disabled*. Connect the **UART** USB-C port.
2. Build as shipped (`VCU_TRANSPORT_TWAI 1`, `VCU_SENSORS_REAL 1`) and flash.
3. Open the serial monitor at 115 200.

Expected: version banner, `[CAN] TWAI 500 kbit/s ... started`, a self-test mask with several bits set (no sensors yet), then FAULT. That is correct behaviour.

4. Type `help`, then `status`. Confirm the state machine is alive and the fault list matches what is missing.

If you want a running demo without sensors, set `VCU_SENSORS_REAL 0` and re-flash: the sample waveform build reports a clean self-test and reaches IDLE.

## 2. Sensors, one bus at a time

Power off between changes.

| Order | Add | Verify |
|---|---|---|
| 2a | I2C: MPU6050, INA260, ADS1115 | `i2cscan` lists `0x40 0x48 0x68`. `status` shows ~1 g on Z, gyro near 0 when still |
| 2b | Battery divider on ADS1115 AIN0 | `status` battery voltage matches a multimeter within ~0.1 V; adjust `BATTERY_DIVIDER_RATIO` if not |
| 2c | INA260 in the pack's positive line (IN+ from the battery, IN− to the distribution point) | With a known load, current matches a clamp meter. Remember it saturates near 15 A |
| 2d | DS18B20 chain | `ds` lists ROM codes; warm one sensor by hand to identify it, then `tempmap <battery_index> <motor_index>` and `cal save` |
| 2e | Hall sensors + magnets | Turn each wheel by hand; the matching wheel speed in `status` goes non-zero and returns to 0 after ~0.5 s |

The self-test mask should reach `0x00` (except CAN, until the transceiver is connected) and the VCU should settle in IDLE.

## 3. Servos and feedback calibration (1–2 h)

Wheels off the ground, servo supply on its own buck, linkages **disconnected** first.

1. In IDLE (or FAULT), `servo 0 0` — the steering servo should hold centre.
2. `pulse 0 1100 1900` to set its travel, then walk the angle in steps and watch for mechanical binding. Adjust the pulse limits until the servo reaches its ends without straining.
3. Repeat for flaps 1–4 with `pulse <id> 1000 1500` as a starting point.
4. Calibrate feedback for each servo: move to the minimum angle, `cal <id> min`; move to the maximum, `cal <id> max`; then `cal show` and `cal save`.
5. Confirm `status` shows a measured angle that tracks the commanded one within a few degrees.
6. Only now reconnect the linkages and re-check the end stops.

Detail and the meaning of every constant: `CalibrationPlan.md`.

## 4. Motor (bench, wheels off the ground)

1. Connect the BTS7960 through its level shifter, pack via a fuse, motor unloaded.
2. With the VCU in reset, confirm the enable line is low and the motor does not twitch on power-up.
3. The motor cannot be driven from the console by design — it needs the driver link (step 6). Until then just verify quiescent behaviour.

## 5. CAN between VCU and Pi

On the Pi (`/boot/firmware/config.txt`, values from your HAT's documentation):

```
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=2000000
```

Reboot, then:

```bash
sudo ip link set can0 up type can bitrate 500000
ip -details link show can0          # state ERROR-ACTIVE
candump can0                        # frames from the VCU appear immediately
```

If you see nothing: check the oscillator value matches the crystal on your HAT (8/12/16 MHz), CANH/CANL orientation and termination. If `candump` shows errors only, the VCU is transmitting without an acknowledging node — confirm both ends are at 500 kbit/s.

Then start the gateway:

```bash
cd HARDWARE/raspberry_pi
pip install websockets python-can --break-system-packages
python main.py --url ws://<laptop-ip>:8000/ws/telemetry -v
```

Expected: a once-per-second status line with a fresh link, and live values on the dashboard's Live Telemetry page. On the VCU, `status` should show `Pi heartbeat seen` and a recent `lastRx`.

## 6. Driver link (wheels off the ground)

On the laptop:

```bash
cd "HARDWARE/Ground Station"
pip install pygame
python drive_client.py --pi <pi-ip> --input test        # sine steering, no throttle
```

The steering servo should sweep. Then switch to real input with a conservative cap:

```bash
python drive_client.py --pi <pi-ip> --max-throttle 0.25
```

Hold ARM with neutral throttle → the VCU goes IDLE → ARMED → DRIVE after 300 ms. Apply a little throttle; the wheels should spin. Release ARM → instant disarm.

Now run the failure drills in `SafetyArchitecture.md` §5 and write down what happened for each.

## 7. LoRa link

1. Flash `Ground Station/ground_radio_bridge/` to the ground ESP32 (install RadioLib 7.x first). Serial at 115 200 should print `ground_radio_bridge ready: 433.0 MHz SF7 ...`.
2. Wire the Pi's Ra-02 per `PinoutAndWiring.md` §3, add `dtoverlay=spi1-1cs` to `/boot/firmware/config.txt`, reboot, confirm `/dev/spidev1.0` exists, then `pip install LoRaRF spidev RPi.GPIO --break-system-packages`.
3. Ground station: `python bridge.py --radio serial --serial-port <COMx or /dev/ttyUSB0> -v`
4. Pi: `python main.py --ground-link lora --lora-backend sx1278 -v`

Expected: ~5 uplinks per second logged by the bridge with an RSSI value, the dashboard updating, and the VCU receiving recommendations. Walk the car away from the ground station to find the range at which loss becomes noticeable, and record it.

Before radios arrive, rehearse the whole path with the UDP substitute:

```bash
python bridge.py --radio udp -v
python main.py --ground-link lora --lora-backend udp --ground-host 127.0.0.1 -v
```

## 8. ESP32-CAM

Flash the stock `CameraWebServer` example with your Wi-Fi credentials, note the IP it prints, open the dashboard's Live Camera page, enter `http://<ip>:81/stream`, press Apply. The URL is remembered in the browser.

## 9. Running the gateway at boot (optional)

`/etc/systemd/system/vcu-gateway.service`:

```ini
[Unit]
Description=VCU telemetry gateway
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Major Project Hardware Software/HARDWARE/raspberry_pi
ExecStartPre=/sbin/ip link set can0 up type can bitrate 500000
ExecStart=/usr/bin/python3 main.py --url ws://<laptop-ip>:8000/ws/telemetry
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now vcu-gateway`. `ExecStartPre` needs root: either run the service as root or give the user a sudoers entry for `ip link`.

## 10. First track session

1. Full charge, fresh log directory, `--aero-mode baseline` for the first runs.
2. Drive slowly, watch temperatures and pack voltage on the dashboard.
3. Switch to `--aero-mode ai` only once the baseline behaviour is understood.
4. After each run copy the CSV: `scp pi@<pi-ip>:.../logs/*.csv .` — this is the data for your report's results section.

## 11. Troubleshooting quick table

| Symptom | Likely cause |
|---|---|
| VCU stuck in FAULT at boot | Self-test bit set — read the mask in the banner, check that sensor |
| `status` shows CAN rx = 0 | Wrong bitrate, CANH/CANL swapped, missing termination, or `can0` down |
| Gateway logs "link STALE" | No CAN frames for > 1 s — VCU reset or bus problem |
| Motor never spins | Not in DRIVE (check ARM, neutral hold, faults), level shifter missing, or enable pull-down holding it off |
| Servos jitter or the Pi reboots under load | Shared/undersized servo supply |
| Recommendations always rejected | Check `last_ai_reason` in `status`: `stale` = link too slow, `ai_mode_disabled` = not in AI-assist, `low_confidence` = the AI itself is unsure |
| LoRa: nothing received | Frequency/SF/BW/sync mismatch, antenna missing, or the two ends are different module families |
| Dashboard shows a frozen frame | Gateway withholds telemetry when the VCU link is stale — that is intended |
