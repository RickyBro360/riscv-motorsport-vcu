# Stage 2c — Two-laptop run with the current bench hardware

Hardware in this configuration: ESP32-C6-Pico VCU with **MPU6050, 2x DS18B20, 4x Hall, INA260,
2x MG996R with pot feedback (flap LF + flap RF channels)**, powered from a **12 V / 2.2 A adapter** instead of the pack;
VP230 CAN transceiver; Raspberry Pi 4B + MCP2515 CAN HAT + Ra-02 (car-side LoRa);
classic ESP32 + Ra-02 (ground LoRa); ESP32-CAM. **No ADS1115, no motor, no other servos.**

| Machine | IP | Runs |
|---|---|---|
| Laptop 1 (engineer) | 10.118.112.128 | backend, dashboard, `bridge.py`, ground ESP32 + Ra-02 on USB |
| Laptop 2 (car side) | 10.118.112.175 | SSH to the Pi, VCU USB console, ESP32-CAM serial monitor, optional `drive_client.py` |
| Raspberry Pi 4B | 10.118.112.178 | gateway `main.py` (CAN in, LoRa out), car-side Ra-02 on SPI1 |
| ESP32-CAM | DHCP (printed at boot) | MJPEG stream to the dashboard |

Wi-Fi for all: SSID `CTRL`.

## What changed in Stage 2c (why "each part works but the system doesn't")

1. **WebSocket dropped every ~40 s.** The backend echoes each frame to its sender; the Pi
   gateway and `bridge.py` never read, so their queues filled, keepalive pongs stopped and
   the socket died (reproduced: 40.5 s). Both clients now drain the socket; the backend
   bounds each send to 0.5 s. Verified: 46 s+ continuous, no drop.
2. **Motor pins did not exist.** GPIO10/11 are not on the ESP32-C6-MINI-1. Motor moved to
   EN=GPIO3, LPWM=GPIO16, RPWM=GPIO17 (see PinoutAndWiring.md).
3. **Dashboard LIVE mode stuck on "connecting" when opened by IP.** Backend CORS now accepts
   localhost and private-LAN origins; the dashboard finds the backend from `.env.local` or
   the page's own host; Next dev allows LAN origins.
4. **Bench build.** `VCU_HAVE_INA260 1` (12 V adapter through the INA260 stands in for the 3S
   pack; 12 V is inside the 9.9-14 V window) and `VCU_FB_WIRED_MASK 0x06` (LF and RF servos have
   feedback wires). Set `VCU_HAVE_INA260 0` if the INA260 is unplugged — the VCU then refuses DRIVE.
5. **LoRa keep-alive.** `bridge.py` sends a HEARTBEAT downlink when it has nothing else to
   send, so the VCU's LoRa-timeout warning reflects the real link.

## Order of operations

### Laptop 1 (10.118.112.128)
```bash
# firewall (admin PowerShell, once):
#   New-NetFirewallRule -DisplayName "vcu-backend" -Direction Inbound -Protocol TCP -LocalPort 8000,3000 -Action Allow
cd SOFTWARE/backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

cd SOFTWARE/dashboard          # second terminal; .env.local is already set
npm install
npm run dev                    # open http://10.118.112.128:3000 -> switch to Live Mode

cd "HARDWARE/Ground Station"   # third terminal, ground ESP32 plugged in, Serial Monitor CLOSED
pip install pyserial websockets
python bridge.py --radio serial --serial-port COM5 -v   # use your COM port
```

### Laptop 2 (10.118.112.175)
1. Flash the VCU (`ESP32C6 Dev Module`, **USB CDC On Boot: Enabled**). Open its Serial
   Monitor at 115200 and type `status` → expect `state=IDLE`, `selftest=0x00`.
   Then `ds` → `tempmap <battery_idx> <motor_idx>` → `cal save`.
2. Flash the ESP32-CAM, note the `Dashboard URL` it prints, paste it into the dashboard's
   Live Camera page on laptop 1.
3. SSH to the Pi:
```bash
ssh pi@10.118.112.178
sudo ~/MP/HARDWARE/raspberry_pi/setup/can_up.sh        # note "VCU found on canX"
cd ~/MP/HARDWARE/raspberry_pi
python3 main.py --ground-link lora --lora-backend sx1278 --can-channel canX --aero-mode baseline -v
```

Expected: Pi logs `SX1278 up`, `link=fresh`; `bridge.py` logs `uplink seq=… rssi=…` about
5 times per second; the dashboard shows live IMU, wheel speeds and temperatures.
Battery voltage reads ~12 V and current the adapter draw. The SoC % is a 3S-LiPo estimate from
voltage, so on the adapter it is meaningless (~78 %).

## Bench power (12 V / 2.2 A adapter)

```
12 V adapter + ── INA260 IN+ ── INA260 IN- ──┬── Buck A (set 6.0 V, >= 5 A) ── both MG996R red
                                              └── Buck B (5 V) ── ESP32-C6 5V/VSYS pin  (optional; USB also works)
12 V adapter − ── common GND (bucks, INA260 GND, ESP32, servo brown, CAN G, Pi GND)
```
* **Never** put 12 V on the servo. MG996R: 4.8-6.6 V.
* Power the **Pi from its own USB-C supply**, not this adapter. Two MG996R stalling together draw
  ~5 A @ 6 V ≈ 2.8 A at 12 V — already more than the 2.2 A adapter. Moving freely they take
  ~0.3-1 A each, which is fine; keep linkages free and never hold both horns against a stop.
* 470-1000 uF across the 6 V servo rail, close to the servos.
* INA260 VBUS must be connected (jumper/wire to IN-), I2C on GPIO6/7 with the MPU6050, address 0x40.
* Servos **powered at boot**, or the feedback self-test (bit 0x40) fails: their wipers then read ~0 V.

## Servo calibration (once, then `cal save`)
Console on laptop 2, VCU in IDLE: `servo 1 0` → `cal 1 min`; `servo 1 45` → `cal 1 max`;
`servo 2 0` → `cal 2 min`; `servo 2 45` → `cal 2 max`; `cal show`; `cal save`; `bench off`.
Servo 1 = LF (PWM GPIO19, wiper GPIO0), servo 2 = RF (PWM GPIO20, wiper GPIO1).
Start with `pulse <id> 1000 1500` if a horn binds.

### Quick fallback (no LoRa): MODE A
If the radios misbehave, prove the rest of the chain over Wi-Fi first:
`python3 main.py --laptop 10.118.112.128 --can-channel canX --aero-mode baseline -v`
(and do not run `bridge.py`).
