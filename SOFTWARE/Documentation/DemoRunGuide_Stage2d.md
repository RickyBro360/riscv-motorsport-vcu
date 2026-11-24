# Stage 2d — Dyno demo run guide

The car sits on the rollers, powered from the 12 V / 2.2 A adapter. You pick a 2026-calendar
circuit; the car then "drives" it:
- its rear wheels follow the circuit's speed profile;
- the steering follows the corners;
- the four flaps switch between **straight mode** (low drag) and **corner mode** (high downforce) at the right places.

The dashboard shows the car moving round the real circuit outline, every sensor, and the
AI engineer. The AI explains what it sees and proposes changes that you approve before
they reach the car.

## 1. What is real and what is modelled — say this in the viva

| Thing | Where it comes from |
|---|---|
| Position on the circuit | **VCU odometry**: rear hall pulses × wheel circumference, one lap = `lap len` metres of wheel travel (default 60 m). No GPS. The VCU moves the flaps and steering from this same number, so the map and the physical car cannot drift apart. |
| Steering angle | The circuit's real curvature: `atan(wheelbase × curvature)`. The angle is scale-invariant, so the 1:10 car steers exactly what a full-size car would. The VCU drives it; the servo is open loop. |
| Motor duty | The modelled full-scale speed profile, mapped between the duty floor (slowest corner) and the duty cap (top speed). |
| Flap angles | Straight/corner mode from the map's zones, plus a left/right split from the modelled lateral g in corners. |
| Wheel speeds, current, voltage, temperatures, IMU, flap feedback (rear LR/RR) | **Measured** by the sensors. |
| Downforce, drag, full-size speed, cornering g, twin lap time | **Modelled**: digital-twin point-mass lap, illustrative 2026-style aero. The rollers cannot feel downforce. |
| Circuit outlines | bacinger/f1-circuits GeoJSON (MIT licence). Speeds, zones and sectors are modelled; the sectors are equal thirds. |
| AI engineer | Deterministic rules plus a twin optimiser, not a trained model. It only proposes; the VCU validates everything. |

**Why the VCU drives the lap and not the laptop or the AI:**
- The architecture is locked: the VCU is the only actuator authority.
- LoRa runs at about 5 Hz and loses packets. The VCU drops a driver command after 250 ms, so the radio could never steer the car.

**What the AI decides from the car's position:**
- **Live look-ahead aero.** It predicts where the car will be once its message has crossed the radio (0.35 s ahead) and asks for the flaps that suit that point.
- **Setup proposals.** It proposes changes and explains each one.

## 2. First power-up of the Stage 2d firmware (bench, before the demo)

Stage 2d moves three things you tested in 2c and adds new outputs. Work through them in this order.

1. **Wiring.** Apply `VCU_wiring_changes.txt`:
   - Hall RL goes to GPIO3.
   - Flap RR goes to GPIO8, with **R9, a 10k resistor from GPIO8 to 3V3**.
   - L298N ENA+ENB go to GPIO17, with **R2, a 10k resistor from GPIO17 to GND**.
   - Remove the L298N's ENA/ENB jumpers.
   - Strap IN1+IN3 to +5V and IN2+IN4 to GND.
   Then run the pre-power checklist in `VCU_final_connections.txt` §9.
2. **Flash.** Use board "ESP32C6 Dev Module" with **USB CDC On Boot: Enabled**. Your 2c calibration (`cal`) and `tempmap` survive in NVS.
3. **Check the basics.** Send `status`: expect `state=IDLE` and `selftest=0x00`. Send `i2cscan`: expect 0x40 and 0x68.
4. **Hall RL on GPIO3.** Spin the rear-left wheel by hand and check `status` shows RL speed. Power-cycle with a magnet parked at each sensor; the board must boot every time.
5. **Servos.** Leave the linkages disconnected first.
   - `servo 3 20` moves LR.
   - `servo 4 20` moves RR (GPIO8; the board LED may flicker).
   - `servo 0 15` moves steering.
   - Then `sweep on 2` and watch all five, then `sweep off` and `bench off`.
6. **Motors, wheels free.** Send `motor 30`.
   - Both rear wheels must turn forward. If one turns backwards, swap **that motor's two output wires**, never the IN straps.
   - `status` shows the current, which must stay well under 2 A.
   - Send `motor stop`.
7. **Dyno calibration, car on the rollers.** Send `dyno cal`. It ramps the duty until the wheels turn, then holds the duty cap for 3 s, and prints the start duty and the top speed. This replaces the missing RPM label, and the dashboard scales its speed charts from it.
   - Set the floor 5 % above the start duty: `set 18 <start‰ + 50>`, then `cal save`.
8. **A first lap from the console.** Send `track list`, then `track 12` (Monza), then `lap run 1`. Watch the console print `[TRACK] lap 1: … s`: the steering, flaps and motor follow Monza. Send `lap stop` at any time; `estop` also stops it.

## 3. The demo

### Laptop 1 — engineer station (10.118.112.128)
```bash
cd SOFTWARE/backend && pip install -r requirements.txt       # includes imageio-ffmpeg for .mp4 downloads
uvicorn app.main:app --host 0.0.0.0 --port 8000

cd SOFTWARE/dashboard && npm install && npm run dev           # http://10.118.112.128:3000 → Live Mode

cd "HARDWARE/Ground Station"                                   # ground ESP32 + Ra-02 on USB, Serial Monitor CLOSED
python bridge.py --radio serial --serial-port COM5 -v          # re-flash ground_radio_bridge.ino (status-line fix)
```

### Laptop 2 — car side (10.118.112.175)
1. VCU console open at 115200.
2. **Camera.**
   - Flash the ESP32-CAM and note the `http://<ip>:81/stream` it prints.
   - Paste it into the dashboard's **Live Camera** page on laptop 1 and click **Connect**.
   - Do **not** open that URL in a browser: the camera serves one client, and that client must be the backend.
3. **The Pi** (the gateway's LoRa protocol is v3, so update the Pi and `bridge.py` together):
```bash
ssh pi@10.118.112.178
sudo ~/MP/HARDWARE/raspberry_pi/setup/can_up.sh                # note canX
cd ~/MP/HARDWARE/raspberry_pi
python3 main.py --ground-link lora --lora-backend sx1278 --can-channel canX --aero-mode ai -v
```
   The Pi now stops with a wiring message if the Ra-02 doesn't answer, because it reads RegVersion.

### Running the car
- **Bench mode (easiest to present).**
  - On the VCU console: `track 12`, then `lap run`. It runs until `lap stop`.
  - No gamepad is needed. The console operator is the safety person: `estop` stops everything.
- **Driver mode.**
  - `trackrun on` on the console.
  - Then `python drive_client.py --pi 10.118.112.178` and hold **SPACE** (ARM deadman).
  - After ~0.3 s hold **W**. Your throttle is a ceiling on the map's duty, and releasing W coasts.
- **Choosing a circuit.** Use the dashboard's **Track Map** selector → **Send to car**. The VCU accepts a circuit change only while no run is active.

### The AI engineer, live
1. **AI Engineer** page, left panel: what it is sending right now and why. For example:
   > "straight mode ends in 180 m — calling corner mode now so the flaps are up before the braking point"

   It also shows the VCU's verdict on each message (`accepted`, or `ai mode disabled` if aero mode is baseline).
2. **What the data shows:** supply headroom against 2.2 A, motor warming rate, flap tracking error and response time, rear motor balance, vibration, link quality.
3. **Proposals.** Approve one, for example "Close straight mode earlier to beat servo travel time" or "Retune the flap angles for Monaco".
   - Watch it move from **approved** to **sent**, then **staged → next lap**, then **on car**.
   - The VCU console prints `[CFG] key 16 = 25 -> staged_next_lap`, then `lap N … (staged settings applied)` at the line.
   - The **after** line compares the metrics before and after the change.
4. Rejected or out-of-range values stay visible as **rejected** or **failed**. They are never hidden.

### Recording
Go to **Live Camera → Start recording**, then **Stop**. The recording appears with **.avi**, **.mp4** (once converted) and **telemetry.csv** download buttons. The CSV covers exactly the video's time window.

## 4. On the 12 V / 2.2 A adapter
- The VCU derates motor duty above **2.0 A** (key 21, shown on the Power charts). Raise it only on a LiPo.
- The rear flaps switch 150 ms after the front pair, so four servos never start together.
- If the flaps sag the voltage, the AI proposes a lower slew rate. If the current sits near the limit, it proposes a lower duty cap.
- The Pi needs its own supply.

## 5. Rehearsing without the car
```bash
cd SOFTWARE/backend
uvicorn app.main:app --port 8000 &
python tools/demo_car.py --camera 8081     # dashboard shows "DEMO FEED — not the car"
# Camera page: connect to http://127.0.0.1:8081/stream
```
`demo_car.py` runs the same map logic and answers approved changes the way the VCU does (range checks, staging at the line, ACKs), so you can rehearse the whole approve-to-applied flow.

The dashboard's **Simulation Mode** (scenario "Dyno track run") needs no backend at all.

## 6. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Board doesn't boot after wiring | GPIO8 strap LOW: R9 missing, or a servo signal wire is backfeeding. Check with the ESP32 held in reset: GPIO8 = 3.3 V, GPIO17 = 0 V. |
| `lap run` refused | Not IDLE, or a fault is active (`status`). A power sensor is required. |
| `[MOTOR] STALL` | The wheels didn't turn with duty > 25 % for 2.5 s. Raise the duty floor (`set 18 …`) or check the roller drag. |
| Camera page "HTTP 503 … camera busy" | A browser tab or VLC holds the ESP32-CAM stream. Close it; the backend reconnects by itself. |
| Map position jumps in 0.2 m steps | One magnet per wheel. Add 2–4 magnets per rear wheel and set `CFG_WHEEL_PULSES_PER_REV`. |
| Bridge logs nothing | Pi and `bridge.py` must both be Stage 2d (LoRa v3). A v2 end rejects every packet. |
