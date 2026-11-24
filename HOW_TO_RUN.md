# How to run the whole system — FW 2.3.0 (network as of 2026-11-12)

> **FW 2.3.0:** copy the new `main.py` to the Pi and the new `bridge.py`, backend and dashboard to
> Laptop 1 (they all changed), flash the VCU. Then: Vehicle Setup → **Servo calibration** to set
> the end points, Car control → **Run motor** for the motor. The knob on the car steers.
> Details: `SOFTWARE/Documentation/ChangeLog_FW2.3.md`.

## Addresses (Wi-Fi SSID `CTRL`)
| Device | IP | What runs there |
|---|---|---|
| Laptop 1 (engineer) | **10.49.218.128** | backend :8000, dashboard :3000, `bridge.py` + ground radio on USB |
| Raspberry Pi 4B | **10.49.218.178** | `main.py` gateway (CAN ↔ LoRa / Wi-Fi) |
| ESP32-CAM | **10.49.218.243** | MJPEG stream `http://10.49.218.243:81/stream` |
| Laptop 2 (car side) | — | VCU Serial Monitor (115200), SSH to the Pi |

The router hands these out (DHCP), so they can change after a restart. If something stops
connecting, check the IPs first: `hostname -I` on the Pi, `ipconfig` on Windows, the camera's
Serial Monitor at boot.

**Start order:** backend → dashboard → `bridge.py` → Pi gateway → power the car and the camera.

---

## 0. One-time steps

**Flash (Arduino IDE):**
- VCU, classic ESP32 stand-in: board **ESP32 Dev Module**, sketch `HARDWARE/esp32/main_updated_corrected__1_/`.
- ESP32-CAM: `HARDWARE/esp32_cam/esp32_cam_stream/` (BACKEND_HOST is already 10.49.218.128).
- Ground radio (classic ESP32 + Ra-02): `HARDWARE/Ground Station/ground_radio_bridge/`, if not
  reflashed since Stage 2d.

**Copy the Pi code** (from laptop 1, in the folder you unzipped):
```powershell
ssh pi@10.49.218.178 "mkdir -p ~/MP/HARDWARE"
scp -r HARDWARE\raspberry_pi pi@10.49.218.178:~/MP/HARDWARE/
ssh pi@10.49.218.178 "chmod +x ~/MP/HARDWARE/raspberry_pi/setup/*.sh"
```
(Use your Pi user name instead of `pi` if it differs.)

**Pi packages and config** (only on a fresh Pi; Stage 2c already did this):
```bash
cd ~/MP/HARDWARE/raspberry_pi/setup
sudo ./pi_setup.sh          # must print OK for spi, mcp2515-can0, mcp2515-can1, spi1-1cs
sudo reboot                 # only if /boot/firmware/config.txt was edited
```

**Laptop packages** (first time only):
```powershell
cd SOFTWARE\backend;  pip install -r requirements.txt
cd SOFTWARE\dashboard; npm install
pip install websockets pyserial
```

### File tree on the Pi
```
/home/pi/
└── MP/
    └── HARDWARE/
        └── raspberry_pi/
            ├── main.py                      the gateway, self-contained
            ├── logs/                        created on first run: full-rate CSV logs
            └── setup/
                ├── pi_setup.sh              one-time: packages, Wi-Fi power save off, config check
                ├── can_up.sh                every boot: brings up can0/can1, finds the VCU
                ├── config_txt_snippet.txt   lines for /boot/firmware/config.txt
                └── vcu-gateway.service      optional: start the gateway at boot
```
Only this folder goes on the Pi. Backend, dashboard, `bridge.py` and `drive_client.py` stay on
the laptops.

---

## 1. Laptop 1 — three terminals

```powershell
# Terminal 1 - backend
cd SOFTWARE\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2 - dashboard
cd SOFTWARE\dashboard
npm run dev
#   this laptop: http://localhost:3000    laptop 2: http://10.49.218.128:3000    -> Live Mode

# Terminal 3 - ground station (LoRa, Mode B)
cd "HARDWARE\Ground Station"
python bridge.py --radio serial --serial-port COM5 -v
```
- COM port: Device Manager → Ports. Close the Arduino Serial Monitor first.
- Windows Firewall: allow Python and Node on **private** networks, or the Pi and the camera
  cannot reach ports 8000 / 3000.

## 2. Raspberry Pi

```bash
ssh pi@10.49.218.178
sudo ~/MP/HARDWARE/raspberry_pi/setup/can_up.sh
#   car powered: ends with "VCU found on canX -> run the gateway with: --can-channel canX"
cd ~/MP/HARDWARE/raspberry_pi
python3 main.py --ground-link lora --lora-backend sx1278 --can-channel canX --aero-mode ai -v
```

**Fallback, Mode A (Wi-Fi instead of LoRa):** do not start `bridge.py`; on the Pi run
```bash
python3 main.py --ground-link direct --laptop 10.49.218.128 --can-channel canX --aero-mode ai -v
```

Logs to the laptop afterwards: `scp pi@10.49.218.178:~/MP/HARDWARE/raspberry_pi/logs/*.csv .`

## 3. Car and camera
- **VCU:** power it; Serial Monitor at 115200 (line ending Newline). `status` must show
  `MODE=TRACK`, `state=IDLE`, `selftest=0x00`, CAN rx counting and the Pi heartbeat.
  The boot log must show `[CAN] TWAI 500 kbit/s TX=16 RX=17 : started`.
  **First run on the stand-in: recalibrate** (`VCU_classic_ESP32_stopgap.md` §6).
- **ESP32-CAM:** power it. It announces itself to the backend every 10 s and the Camera page
  connects on its own. If it does not, paste `http://10.49.218.243:81/stream` on the Camera page.
  Never open the stream in a browser tab: the camera serves one client, the backend.

## 4. Driving (TRACK mode, later)
```powershell
cd "HARDWARE\Ground Station"
python drive_client.py --pi 10.49.218.178 --input test           # link check, no motor
python drive_client.py --pi 10.49.218.178 --max-throttle 0.25    # keyboard/gamepad, hold SPACE (A) to ARM
```

## 5. If something does not connect
| Symptom | Check |
|---|---|
| `can_up.sh`: no VCU frames | boot log `TX=16 RX=17`; GND to the HAT's G; CANH/CANL not swapped; ~60 Ω across H–L with power off |
| Pi stops with a radio wiring message | Ra-02 wiring / 3.3 V (pin list: `VCU_final_connections.txt` §7) |
| Dashboard: no live data | Terminal 3 shows received packets? Antennas on both Ra-02s; same code version on both ends |
| Laptop 2 cannot open the dashboard | `ping 10.49.218.128`; firewall; dashboard started with `npm run dev` |
| Camera page empty | `ping 10.49.218.243`; paste the stream URL; only one client at a time |

Full demo procedure (DYNO presentation, TRACK driving, faults): `SOFTWARE/Documentation/DemoRunGuide_FW2.2.md`.
