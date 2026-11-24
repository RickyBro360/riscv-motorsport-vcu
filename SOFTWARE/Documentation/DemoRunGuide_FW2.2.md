# FW 2.2 — Run guide: DYNO mode and TRACK mode

FW 2.2 gives the car two drive modes. The VCU stays the only actuator authority in both. The
dashboard, the AI engineer and the console only **request**; the VCU accepts or refuses each
request against its live state and answers with a CONFIG_ACK, which the dashboard shows.

| | **DYNO** — car on the rollers | **TRACK** — car on the floor |
|---|---|---|
| Who moves the motor | a **circuit run** (dashboard Start run, or console `lap run`), `dyno cal`, console `motor` | only a **driver holding ARM** (`drive_client.py`, SPACE / gamepad A) |
| Steering, duty, flaps | from the stored circuit map at the car's odometry position | steering and throttle from the driver; flaps straight/corner from IMU + steering |
| Deadman | the **ground link**: no heartbeat or no LoRa for 1 s → run stops (COMM_LOSS warning) | ARM: release it and the car stops (250 ms command timeout) |
| Traction control | off (rollers) | on: duty is cut when the rears spin faster than the fronts by more than key 22 (25 % default) |
| AI engineer | look-ahead aero along the circuit, setup proposals, dyno-only proposals (lap length, duty floor, …) | live straight/corner advice from yaw, steering and g; TC and cornering-g proposals |

**The VCU boots in TRACK mode** (the mode in which nothing moves without a deadman). The mode
changes only when the car is IDLE with no run active, and it is remembered in NVS.

Which firmware folder you flash decides the hardware (`vcu_hw_profile.h`):
**Prototype** = hobby modules (A3144 halls, L298N, no kill switch). **PCB rev B** = TLE4941
wheel sensors, BTS7004 kill switch, 2× BTN8982. Everything below applies to both; differences are marked.

> **FW 2.2.1/2.2.2 (2026-11-12): classic ESP32 stand-in.** While the prototype runs on a classic ESP32
> DevKit in place of the dead C6, flash the same sketch with Arduino IDE board **ESP32 Dev
> Module** (no USB CDC option). `status` then shows `hw=prototype (hobby modules) on classic
> ESP32 stand-in`; the dashboard still shows "Prototype". Recalibrate first:
> `VCU_classic_ESP32_stopgap.md` §6.

## 1. Start the system

### Laptop 1 — engineer station
```bash
cd SOFTWARE/backend && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000        # --host 0.0.0.0: the Pi and the ESP32-CAM must reach it

cd SOFTWARE/dashboard && npm install && npm run dev     # http://<laptop-ip>:3000 → Live Mode

cd "HARDWARE/Ground Station"                             # LoRa (MODE B) only
python bridge.py --radio serial --serial-port COM5 -v
```

### The car
1. **VCU.** Flash the firmware folder for your hardware (board: ESP32C6 Dev Module, or ESP32 Dev
   Module for the classic ESP32 stand-in). Console at 115200: `status` must show
   `hw=…`, `MODE=TRACK`, `state=IDLE`, `selftest=0x00`.
2. **Pi.**
   ```bash
   sudo ~/MP/HARDWARE/raspberry_pi/setup/can_up.sh
   cd ~/MP/HARDWARE/raspberry_pi
   python3 main.py --ground-link lora --lora-backend sx1278 --can-channel canX --aero-mode ai -v   # MODE B
   python3 main.py --ground-link direct --laptop <laptop-ip> --aero-mode ai -v                     # MODE A (Wi-Fi)
   ```
3. **Camera (ESP32-CAM).** Before flashing `esp32_cam_stream.ino`, set `WIFI_SSID`, `WIFI_PASSWORD`
   and **`BACKEND_HOST` = the laptop's IP**. After boot it announces itself to the backend every
   10 s; the Camera page connects on its own and shows "announced by the ESP32-CAM". If the
   laptop IP changed, paste the printed `http://<cam-ip>:81/stream` on the Camera page instead.
   Do not open the stream in a browser tab: the camera serves one client, the backend.

The top bar now shows the drive mode chip and a red **E-STOP** button on every page.

## 2. DYNO mode — the presentation

1. Put the car **on the rollers** (or rear wheels off the ground).
2. **Car control** panel (Overview, Track Map or Vehicle Setup) → **Dyno — on the rollers** →
   confirm. The VCU ACKs; the chip turns DYNO. (Console: `mode dyno`.)
3. **First time on this hardware** — and always after moving to PCB rev B, whose half-bridges
   brake in the PWM off-time: **Dyno cal**. It ramps until the wheels turn, holds the duty cap
   for 3 s and stores the start duty and top speed. Then set the duty floor 5 % above the start
   duty (Vehicle Setup → tunables, or `set 18 <start‰ + 50>`).
4. **Track Map** → pick a circuit → **Send to car** (accepted only while no run is active).
5. **Laps** (0 = until stopped) → **Start run**. The VCU starts only if: DYNO, IDLE, no fault,
   ground link seen. If a condition is missing the panel lists it, and if the VCU refuses anyway
   it shows **REFUSED by the VCU**.
6. During the run: the map, lap times, flap angles, wheel speeds, power, temperatures and the AI
   engineer update live. The run ends by itself after the laps target.
7. **Stop** ends the run (motor to 0, flaps and steering to neutral). **E-STOP** latches the VCU
   in EMERGENCY_STOP: motor off, kill switch off (rev B), flaps and steering to failsafe.
   **Reset** re-runs the self-test and returns to IDLE.
8. **AI engineer page → proposals → Approve.** Each moves approved → sent → staged → on car; the
   VCU applies staged values at the next start/finish crossing.
9. **Encoder knob** (KY-040): each detent changes the motor duty cap by 1 % through the same
   ConfigStore (range checked, ACKed, visible on the dashboard). `knob` on the console shows the count.

**To show the safety chain in the viva:** start a run, then stop the bridge (or unplug the
ground radio). Within 1 s the VCU stops the run and raises COMM_LOSS for 3 s; the run does not
restart by itself when the link returns.

## 3. TRACK mode — driving on the floor

1. Car stopped, IDLE. **Track — on the floor** (no confirmation needed). Console: `mode track`.
2. `python drive_client.py --pi <pi-ip>` (keyboard) or with a gamepad. Hold **SPACE** (A) ≈ 0.3 s
   with neutral throttle to enter DRIVE, then **W** / right trigger for throttle.
3. The VCU: steering and throttle from the driver; **traction control** cuts duty when the rears
   spin up (dashboard: "Traction ctrl cutting N %"); flaps go to straight mode only when speed
   > 2 km/h, throttle > 30 %, lateral g < 0.12, yaw < 12 °/s and steering < 3° for 300 ms, and
   back to corner mode at once on any of lateral g > 0.25, yaw > 25 °/s or steering > 6°.
4. The AI engineer sends track-mode recommendations (source `engineer_station_track_mode_v1`):
   straight mode on a powered straight, corner mode at turn-in with a left/right split from yaw × speed.
5. Stop: release SPACE. **E** (gamepad B) or the dashboard E-STOP latch the e-stop; **R** resets.
6. TC slip target: Vehicle Setup → "Traction control slip target" or `tc <permille>` (0 = off).

Circuit runs, `dyno cal` and `motor` are **refused** in TRACK mode.

## 4. Faults — what the car does by itself

All of these are decided on the VCU; nothing off the car can disable them.

| Condition | Reaction |
|---|---|
| Driver command older than 250 ms (TRACK) | motor 0, the VCU disarms (ARM must be held again) |
| Ground link lost 1 s during a dashboard run (DYNO) | run stops, COMM_LOSS warning 3 s |
| Duty applied but rear wheels not turning (stall) | run aborted, motor off and kill switch off until the run is restarted or the driver releases the throttle (both modes) |
| Supply current above key 21 | duty derated |
| Over-temperature, under-voltage, sensor faults, CAN timeout | as `SafetyArchitecture.md` §3 (FAULT or EMERGENCY_STOP) |
| E-STOP (dashboard, console `estop`, driver E / B) | EMERGENCY_STOP latched; motor and kill switch off; flaps/steering failsafe |

## 5. Rehearsing without the car

* **Dashboard only:** Simulation Mode. The Car control panel drives the offline simulator
  (mode, start run, laps, e-stop) with the same rules.
* **Backend + dashboard, fake car:** `python tools/demo_car.py --camera 8081` (add `--hw prototype`
  to pretend to be the prototype). It boots in TRACK, refuses runs until DYNO, and answers
  every button like the VCU. The dashboard shows "DEMO FEED".
* **The real VCU logic, no hardware:** `HARDWARE/tests/virtual_car.py` compiles the firmware's
  own `vcu_logic.h` for the laptop and runs it behind the real Pi gateway on a virtual CAN bus:
  ```bash
  python HARDWARE/tests/virtual_car.py --ground-link direct --laptop 127.0.0.1           # MODE A, car on the rollers
  python HARDWARE/tests/virtual_car.py --floor --ground-link direct --laptop 127.0.0.1   # on the floor (TRACK)
  ```
  Needs `g++` and `pip install python-can`.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Start run greyed out | the panel lists why: not DYNO, not IDLE, a run already active, link not seen by the VCU, car not reporting |
| "REFUSED by the VCU" | the VCU's state disagreed with the dashboard's (e.g. a fault arrived). Check `status` |
| "no answer from the car" | the command was resent for 10 s without an ACK: the car, the Pi or the radio is down |
| Mode switch refused | the car is not IDLE or a run is active. Stop first |
| Motor never turns on rev B | motor power stays off outside DRIVE/run; check `status` "kill switch", J3/J4 voltage, GPIO19 |
| Camera page never connects | `BACKEND_HOST` wrong or uvicorn not on 0.0.0.0; the camera's `/status` page shows announce failures |
| Wheel speeds wrong by a whole factor (rev B) | set `VCU_REVB_WHEEL_PPR` to the teeth / pole pairs of your target wheel |
