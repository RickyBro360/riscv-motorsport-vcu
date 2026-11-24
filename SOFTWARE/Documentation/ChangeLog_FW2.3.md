# FW 2.3.10 (2.3.10-stage2e) — 2026-11-15

- **Demo 2 on every circuit.** Key 26 = 100 + track id (2 = the circuit selected on the car). Dashboard: circuit
  picker next to "Demo 2: lap of"; console `demo 2 <id>` (`track list` shows ids); POST /api/vehicle/demo
  {"action":"lap","trackId":n}. Each circuit is scaled to ~34 s (72-89 m). Full lock = 35 % of that circuit's
  tightest map corner (min 3 deg), growing from half lock in between. Host test runs all 24 circuits.
- **`imu flip`**: turns the IMU frame 180 deg about vertical (pitch AND roll both change sign), saved.
- **Dashboard lag on Live Telemetry.** Measured: in `npm run dev` the page kept the browser ~97 % busy
  (14 charts redrawn per frame). Charts and history statistics now refresh at 4 Hz, only while on screen;
  live numbers still update per frame. The big gain is running the production build (`npm run build`,
  `npm start`): the same page then uses ~2 % in long tasks. `npm start` now listens on 0.0.0.0.

# FW 2.3.9 (2.3.9-stage2e) — 2026-11-15 — VCU sketch only

- Demo 2 (Monza lap) steering is now visible. The map's real angles are tiny on a fast circuit (long corners
  0.5-2.4 deg, chicanes a few hundred ms), so the demo lap shows: tight corners (>= 3 deg in the map) = FULL lock,
  each side held 0.9 s; long corners (>= 0.3 deg) = half lock (`VCU_CAL_DEMO_LAP_HALF_LOCK_PCT`), smoothed.
- Demo 2 speed: top duty `VCU_CAL_DEMO_LAP_TOP_DUTY_PCT` 80 (13.5 km/h), squared speed profile so braking zones
  drop to about 5.5 km/h (26 % duty). Lap still ~34 s (89 m). Normal circuit runs are unchanged.
- IMU: `VCU_CAL_IMU_AUTO_LEVEL 1` levels automatically ~3.5 s after power-up when nothing is saved and the board
  is more than ~30 deg from flat. `imu level` / `imu nose` / `imu reset` now also restart the attitude filter
  and zero yaw.

# FW 2.3.8 (2.3.8-stage2e) — 2026-11-15

- **IMU mounting correction.** The MPU6050 is now mounted vertically, so roll/pitch read wrong. Console:
  `imu level` (car flat and still) rotates the readings so gravity is +z; then lift the nose ~10 deg or more and
  `imu nose` fixes the forward direction. Stored in NVS key "imurot"; `imu reset` removes it; `imu` shows the
  current reading. Car frame afterwards: x forward, y left, z up (nose up = negative pitch, left side up =
  positive roll, left turn = positive lateral g). The digital twin's roll/pitch signs follow this frame.
- **Demo 2: one auto-driven lap of Monza** (key 26 = 2, console `demo 2`, dashboard "Demo 2: Monza lap",
  POST /api/vehicle/demo {"action":"lap"}). The VCU selects Monza, sets the lap length so the lap takes about
  `VCU_CAL_DEMO_LAP_SECONDS` (34 s; 133 m with the default tunables), switches to DYNO for the run and back
  afterwards, and runs 1 lap with the existing circuit-run logic (map steering, map speed, map aero). The
  track dot on the dashboard follows it. Track id and lap length stay at the demo values afterwards.
- `VCU_CAL_DEMO_TOP_DUTY_PCT` 100. Knob button on J12 pin 3 (front-right wheel input). vcu_calibration.h
  carries the car's calibration of 2026-11-15.

# FW 2.3.7 (2.3.7-stage2e) — 2026-11-15

- Motor duty cap default 100 % (was 85 %): firmware, backend, dashboard. A car that saved the old 85 % default
  (any `cal save` writes the tunables too) is lifted to 100 % once at boot ("[CFG] motor duty cap ..." line).
  Any other value you set on purpose is kept. The 2 A current derating (key 21) still applies on the adapter.
- Dashboard Simulation Mode and backend tools/demo_car.py now use the same speed model as the VCU:
  1000 rpm x 90 mm = 17.0 km/h at 100 % duty, 0 below 20 % (was a placeholder 4.4 km/h at 100 %).

# FW 2.3.6 (2.3.6-stage2e) — 2026-11-15

VCU sketch only. Simulated spikes now last `VCU_CAL_SPEED_SPIKE_MS` (200 ms) and also appear on the reported
VEHICLE speed (mean of the two rear values). Display only: motor PWM, odometer and VCU logic are unaffected.
Over LoRa (5 Hz telemetry) a 200 ms spike lands in about one frame and can occasionally be missed.

# FW 2.3.5 (2.3.5-stage2e) — 2026-11-15 — simulated wheel-speed noise (exploration only)

Only the VCU sketch changed (vcu_calibration.h, vcu_logic.h, the .ino). Pi, bridge, backend, dashboard = 2.3.4.
- `VCU_CAL_SPEED_SIM_NOISE 1`: on top of the duty-based speed, each rear wheel gets its own slow jitter of
  +/- `VCU_CAL_SPEED_JITTER_PCT` (10 %) and, every 6-14 s, a 0.3-0.5 s spike of 1.5x-3.5x. Vehicle speed
  carries the jitter only. Odometer, lap position and the VCU's own logic use the clean estimate.
- `VCU_CAL_SPEED_REPORT_AS_SENSOR 1`: RO key 106 reports 0, so the dashboard shows its original "measured"
  labels. THESE NUMBERS ARE SIMULATED, NOT MEASURED — do not present them as hall-sensor data.
- Back to 2.3.4 behaviour: both to 0. Back to real halls (2.3.3 behaviour): `VCU_CAL_SPEED_FROM_DUTY 0`.

# FW 2.3.4 (2.3.4-stage2e) — 2026-11-14/15

Servo calibration code, the flap law and DRS timing are UNCHANGED from 2.3.3 (saved values on the car still win).

1. **Wheel speed estimated from motor duty** (`VCU_CAL_SPEED_FROM_DUTY 1` in vcu_calibration.h). The hobby
   hall modules read too badly to show. Rear speed = duty x free speed (1000 rpm, 90 mm wheel = 17.0 km/h at
   100 %), 0 below 20 % duty (motor dead band), 0.4 s first-order lag. Front wheels report 0. The VCU reports
   RO key 106 `speedSource` = 1 and the dashboard labels speed as DERIVED / "estimated from motor duty".
   It is an estimate, not a measurement: no artificial noise or spikes are added. Odometer and lap position
   use the same estimate. The pulse-based stall watchdog is OFF in this mode (it needs trustworthy pulses).
   Set `VCU_CAL_WHEEL_DIAMETER_MM` to the measured tyre. `VCU_CAL_SPEED_FROM_DUTY 0` = halls, as before.
2. **Knob press = DRS** (`VCU_CAL_KNOB_BUTTON_DRS 1`). Wire KY-040 **SW -> J13 pin 3** (front-left wheel-sensor
   signal, GPIO14; classic stand-in D25) and UNPLUG the front-left hall module. Internal pull-up, 30 ms debounce.
   Console prints `[KNOB] pressed -> DRS`. The front-left wheel input is no longer read.
3. **Demo corner sequence** (CONFIG key 26 `demoCmd`, dashboard Car control -> "Play demo sequence", console
   `demo` / `demo stop`, POST /api/vehicle/demo). Run BY THE VCU, 21 s: 0-2 s accelerate, 2-4 hold, 4-5.5 brake,
   5.5-7.5 steer left, 7.5-9.5 right, 9.5-10.5 straight, 10.5-13 accelerate to top, 13 s DRS (5 s flat + 1 s
   max), 19-21 slow down, stop. Duty never drops below 22 % while running; top = min(duty cap,
   `VCU_CAL_DEMO_TOP_DUTY_PCT` 60). Flaps are not scripted: they follow the steering law and DRS. Same
   conditions as the dashboard motor (IDLE, no fault, ground link); Stop motor / E-STOP / `demo stop` end it.
   New run state 5 `demo`.
4. **Digital twin rebuilt from the chassis model** (the "F1 VCU Car Model" template geometry, 58 sheet parts):
   dashboard lib/car-model.ts + car-model-data.json. Front wheels steer about their kingpins, all four flaps
   rotate about their hinge lines, tie rods and pushrods follow, rear wheels spin with the speed.
5. **Charts no longer clip**: the fixed Y range is a minimum; the axis grows when the data goes past it.
6. ESP32-CAM sketch (2026-11-14): TCP_NODELAY on the stream, fps / reset reason / RSSI diagnostics.

Tests: hardware 63, backend 74, dashboard tsc + eslint (0 errors) + next build. Compile C6 and classic ESP32.
Not yet run on the car.

# FW 2.3.3 (2.3.3-stage2e) — asymmetric aero + DRS, 2026-11-13

1. **Flap law default = "straight: all MAX; turning: the INSIDE pair drops to FLAT".**
   `VCU_CAL_FLAP_NEUTRAL_DEG 45`, `VCU_CAL_FLAP_SWING_DEG 45` (swing may now go to 90 = inside
   pair flat at half lock). Knob LEFT → left front + left rear go FLAT, right pair stays MAX;
   knob RIGHT → the mirror image; in between it is proportional.
2. **DRS** (config key 25, dashboard button in Car control, console `drs` / `drs off`):
   all four flaps FLAT for 5 s whatever the steering, then all MAX for 1 s, then back to the
   steering law. Pressing again restarts the 5 s. The VCU runs the timer
   (`VCU_CAL_DRS_OPEN_MS` / `VCU_CAL_DRS_HOLD_MS`). Works in IDLE/ARMED/DRIVE/FAULT; refused in
   E-STOP; an E-STOP cancels it. Console prints `[DRS] OPEN / closed / off`.
3. **A re-flash can no longer make a servo jump.** Boot rule: servo END POINTS (all `*_US`)
   saved on the car ALWAYS win over vcu_calibration.h; the boot log notes when the file differs.
   The flap law + knob (lock, neutral, swing, click, reverse) follow the file whenever it is
   edited (new NVS key `trimlaw`), so the new 45/45 applies on the first boot while your saved
   FLAT/MAX pulses stay. `cal reset` is the only way to load the file pulses (servos move).
4. **`cal flap <fl|fr|rl|rr> swap`** swaps FLAT and MAX of one flap — for a mirror-mounted
   servo that moves the wrong way (symmetric instead of asymmetric motion). Then `cal save`.
5. `VCU_CAL_STEER_SERVO_ENABLED 1` (180° MG996R fitted on the steering).
6. Backend `POST /api/vehicle/drs {"action":"open"|"cancel"}`; key 25 `drsCmd` in the Pi
   gateway, ground bridge and backend; dashboard DRS button with a 5 s / 1 s countdown and the
   VCU-reported phase. Simulation Mode accepts key 25.

Compile: classic ESP32 469,483 B, ESP32-C6 467,574 B, 0 warnings. Hardware tests 63, backend 74,
dashboard tsc + eslint + next build clean.

# FW 2.3.2 (2.3.2-stage2e) — bench findings, 2026-11-13 morning

Found on the car:
1. **The steering servo is a CONTINUOUS-ROTATION (360°) MG995.** 1410–1590 µs = stop, below
   1400 µs = spins one way, above 1600 µs = the other way, faster the further from 1500. The knob
   therefore set a SPEED, so the steering kept turning and drove the linkage into its stops.
   No firmware can make it hold an angle (no pot inside). **Fit a 180° positional servo**
   (MG995/MG996R "180°", or MG90S). New `VCU_CAL_STEER_SERVO_ENABLED` in vcu_calibration.h,
   **default 0**: no pulses to the steering servo; the knob still sets the steering ANGLE that the
   flaps follow and the dashboard shows. Set 1 once a positional servo is fitted.
2. **`pulse <id> <us>` held every servo until `bench off`**, so the knob seemed dead after any
   calibration, and a reset "lost" the values (a `pulse` jog is never stored; only `cal ...` +
   `cal save` is). Now a knob turn, or 60 s without a new jog, hands the servos back to the knob,
   and the console says so. The dashboard's Servo calibration panel never holds the servos.
3. Front flaps moved together while the rears moved opposite: one front SG90 is mounted
   mirror-image. Fix by calibration: that servo gets FLAT > MAX (`cal flap fr flat 1800`,
   `cal flap fr max 1300` style). No firmware change.

Compile: classic ESP32 468,355 B, ESP32-C6 466,362 B, 0 warnings. Hardware tests 63, backend 73.

---

# FW 2.3.1 (2.3.1-stage2e) — first bench result on FW 2.3.0, 2026-11-13 03:13

On the car, FW 2.3.0 sat in **FAULT** with `faults=0x0007` / `0x0006` (WATCHDOG + SENSOR_TIMEOUT +
CAN_TIMEOUT). Knob steering and the flap law worked (1 click → −3°, FL 22° / FR 18°).
- **CAN_TIMEOUT, `TEC=128`, `rx=0`:** expected while the Pi gateway / `can_up.sh` is not running —
  nobody ACKs the frames. A warning only.
- **FAULT:** the 1 s *software* watchdog kept seeing the sensor task silent (the same stall FW 2.2.10
  was chasing), so the FAULT never got its clean second to clear.

FW 2.3.1:
1. The software watchdog is a **WARNING**. A task that really hangs is still caught by the ESP32's
   own task watchdog (3 s → reboot; the motor pin's pull-down stops the motor) and a CAN-side stall
   still trips the dashboard-motor deadman.
2. Diagnostics to find the stall: console **`diag`** (sensor ages, longest sensor / control / CAN
   loop gaps, per-section maxima IMU / INA260 / ADC / DS18B20, read-failure counts, I2C restarts,
   watchdog trips) and `diag reset`. A **`[WDOG]`** line prints when a task goes silent, a
   **`[STATE] A -> B faults: ...`** line on every state change, and `status` names the fault bits.

Only the VCU firmware changed from 2.3.0 (and its tests). Compile: classic ESP32 467,631 B,
ESP32-C6 465,656 B, 0 warnings. Hardware tests 63, backend 73.

**What to send back:** after a few minutes powered (motor off is fine), the output of `diag`.
`longest gap between loops` far above 10 ms on the sensor task, and which section holds the
longest loop, tells us whether it is I2C, the DS18B20 1-Wire or something starving the task.

---

# FW 2.3.0 (2.3.0-stage2e) — knob steering, steering-driven flaps, dashboard motor, live servo calibration

Date: 2026-11-13. Prototype build (hobby sensors), classic ESP32 stand-in or ESP32-C6.

**Status: compiled and tested in software only. NOT yet run on the car.** Run the re-test list
in §7 before trusting it on the bench.

## 0. Which firmware this is built on

The zip this was made from contained **FW 2.2.4**. The project notes say the car had already
been through FW 2.2.5–2.2.10 in another session, but none of those changes were in the zip.
FW 2.3 therefore re-implements the ones that matter, from their descriptions (§4). If you still
have the 2.2.10 sketch, compare `controlTask()` / `sensorTask()` with it — anything in 2.2.10
that is not listed in §4 is not in FW 2.3.

## 1. What you asked for, and what the car now does

| Request | FW 2.3 |
|---|---|
| The rotary encoder steers the steering servo | The KY-040 is the steering wheel: 3° per click, ±30° (10 clicks each way), clamped at full lock with no wind-up. Counted by the PCNT hardware counter on both chips (no missed clicks, no interrupt storm on a floating pin). |
| Flaps follow the steering: right turn → left flaps more downforce, right flaps less (and vice versa); angle depends on how much it is steered | Wheels straight: all four flaps at NEUTRAL (20°). Turning, the OUTSIDE pair rises toward NEUTRAL + SWING and the INSIDE pair drops toward NEUTRAL − SWING, in proportion to steering / lock. Defaults 20° ± 20° → full right lock = left flaps 40°, right flaps 0°. Front and rear pairs do the same. |
| Motors controlled from the dashboard | Car control panel: a duty slider, **Run motor**, **Stop motor**. Works in either drive mode (no need for DYNO). Drag the slider while it runs and release: the duty changes without stopping. Console `motor <percent>` / `motor stop` does the same. |
| Remove unnecessary stop / fault modes | See §3. Sensor glitches, failed self-test items and servo mismatches are warnings now; only things that can hurt the car stop the motor, and those clear by themselves. The only latching stop left is the E-STOP button. |
| Dashboard is laggy | See §5: database writes moved off the server's event loop, broadcast to all viewers at once, latest frame served from memory, dashboard redraws coalesced to ≤10 Hz. |
| Servo min/max easy to calibrate and edit | `vcu_calibration.h` (one short file, every number explained) **and** live editing from the dashboard (Vehicle Setup → Servo calibration) or the console, with each change moving that servo to the new end point so you can see it. §2. |

Where the steering comes from, in order:
1. a **circuit run** (dyno map) — the map steers, and the map's own straight/corner aero is kept;
2. a **driver holding ARM** in `drive_client.py` — their wheel steers while armed;
3. otherwise (IDLE, FAULT, dashboard motor, no driver) — **the knob**.

E-STOP and boot put steering straight and the flaps flat (failsafe), as before.

AI recommendations are still validated by the VCU, but are blended into the flaps **only during
circuit runs**. Outside a run the flaps are exactly the steering law above. The AI never acts
during a FAULT.

## 2. Servo calibration — how to do it

Linkages connected, car on the stand, wheels off the ground.

### Option A — from the dashboard (Vehicle Setup → Servo calibration)
1. Live mode, car reporting.
2. Steering: use −10/−1/+1/+10 on **Full LEFT lock** until the wheels are at full left without
   the servo buzzing. Each change moves the steering servo there for 2.5 s. **Go** re-sends the
   value to look again. Same for **Wheels straight** and **Full RIGHT lock**.
3. Flaps: for each flap set **Flat (0°)** (least downforce) and **Max (45°)** (most downforce).
   A mirror-mounted servo just has Flat > Max — that is how left and right end up moving the right
   way. For the rear MG996R the pot reading at each end is captured automatically after 1.5 s.
4. Steering → flaps: Neutral, Swing, Steering lock, Steering per knob click, Knob direction.
5. **Save on car** keeps it through power cycles. **Copy vcu_calibration.h** copies the #define
   block — paste it into the file to make these the firmware defaults.

### Option B — console (115200 baud)
```
pulse 0 1500            raw jog: steering servo to 1500 us (find the end points; `bench off` releases)
cal steer left 1150     store an end point (the servo moves there for 2.5 s)
cal steer center 1500
cal steer right 1850
cal flap rl flat 1000   (fl | fr | rl | rr) (flat | max) <us>
cal flap rl max 1520
cal neutral 20          degrees
cal swing 20
cal lock 30
cal click 3             degrees per knob click
cal reverse 1           if clockwise steers left
cal                     show everything + a copy of the #define block to paste
cal save                keep it on the car
cal reset               back to vcu_calibration.h
```

### Option C — edit `HARDWARE/esp32/main_updated_corrected__1_/vcu_calibration.h` and upload.

**Which wins at boot:** values saved on the car win, **unless the file was edited after that
save** — the firmware stores a fingerprint of the file's defaults with every save. So an edited
file always takes effect. The boot log and `cal` say where the values came from.

Old FW 2.2 servo calibration (`pulse <id> <min> <max>`, NVS keys `sv0..4` pulses) is no longer
used for pulses. The rear-flap pot feedback calibration in `sv3`/`sv4` is still used.

## 3. Faults: what stops the car now

| Condition | FW 2.2 | FW 2.3 |
|---|---|---|
| Sensor / control task stalled (software watchdog) | CRITICAL → latched E-STOP | FAULT in 2.3.0; **WARNING in 2.3.1** (logged `[WDOG]`, `diag`) |
| IMU / INA260 / DS18B20 read timeout | FAULT (everything to failsafe) | WARNING |
| Implausible supply voltage reading | FAULT | WARNING |
| Any self-test item failed (missing DS18B20, gyro calibration disturbed, CAN, feedback wire) | FAULT **for ever** (the flag never cleared) | WARNING; the car goes to IDLE and works |
| Steering / flap position mismatch | FAULT / warning + flaps to failsafe | WARNING, nothing moves to failsafe |
| Supply under-voltage, over-current | CRITICAL → latched E-STOP | FAULT: motor off, auto-clears |
| Motor / battery over-temperature | FAULT | FAULT (unchanged) |
| Driver link lost while DRIVING (drive_client) | FAULT | FAULT (unchanged) |
| Dashboard motor / run: ground link lost 1 s (LoRa: 3 s) | run stops | motor stops and **stays** stopped (unchanged — this is the deadman) |
| Motor stall (duty > 25 %, no rear pulse for 2.5 s) | run stops | unchanged |
| E-STOP button (dashboard, console, driver) | latches | latches (unchanged) |

In FAULT the steering and flaps keep working; only the motor is off.

## 4. FW 2.2.5–2.2.10 fixes folded in (they were not in the 2.2.4 zip)

| From | Fix |
|---|---|
| 2.2.5 | 10 ms task loops no longer "catch up" after a long step (self-test): `loopWait()` blocks one tick and resyncs. This was the task-watchdog reboot loop. |
| 2.2.7 | KY-040 = 2 counts per click (`VCU_CAL_KNOB_COUNTS_PER_CLICK`); knob levels printed at boot. |
| 2.2.8 | Knob counted by the PCNT peripheral (both chips), not polled. |
| 2.2.9 | Every age of a timestamp written by another task is clamped (`ageMs()`): no false WATCHDOG. |
| 2.2.10 | Classic ESP32 I2C at 100 kHz. Plus (new): I2C bus restarted after 0.5 s of failed IMU reads, and an I2C mutex and 1-Wire mutex (the e-stop-reset self-test used to run I2C/1-Wire at the same time as the sensor task). |

## 5. Dashboard lag — causes found and fixed

1. **Backend wrote every telemetry frame to SQLite on the event loop** (3 connections and
   commits per frame at 20 Hz, rollback journal = an fsync each). The WebSocket fan-out and every
   REST call queued behind it. Now: write in a worker thread, WAL journal, `synchronous=NORMAL`.
2. **Broadcast went to viewers one after another**, each allowed 0.5 s. One slow tab or the
   second laptop on weak Wi-Fi delayed every frame for everyone. Now: serialised once, sent
   concurrently.
3. **"Latest frame" read and parsed 50 frames from SQLite on every call** — and the vehicle
   status, twin, AI recommendation (5 Hz from the gateway) and calibration endpoints all call it.
   Now served from memory (same row; DB fallback unchanged).
4. **The dashboard re-rendered every page on every frame.** Now frames are collected as they
   arrive and the screen updates at most every 100 ms; charts still get every frame.
5. LoRa: a newer value for a key replaces older copies waiting in the ground radio's queue (a
   dragged slider no longer queues seconds of stale values); motor STOP jumps the queue like E-STOP.

Over LoRa the dashboard can only update as fast as the uplink (5 Hz by default) — that is the
radio, not the software.

## 6. Files changed

Firmware: `vcu_calibration.h` (new), `vcu_logic.h`, `main_updated_corrected__1_.ino`,
`vcu_hw_profile.h` (encoder step count moved). Gateway: `raspberry_pi/main.py`,
`Ground Station/bridge.py` (key names; queue fix). Backend: `app/db.py`, `app/websocket.py`,
`app/services/recording.py`, `app/services/vehicle_commands.py`, `app/routers/telemetry.py`,
`app/routers/vehicle.py`, `app/vcu_config.py`. Dashboard: `components/features/car-control.tsx`,
`components/features/servo-calibration.tsx` (new), `components/features/vcu-tunables.tsx`,
`contexts/telemetry-context.tsx`, `services/api.ts`, `app/vehicle-setup/page.tsx`,
`app/live-telemetry/page.tsx`. Tests: see §8.

**Re-deploy everything**: VCU firmware, Pi `main.py`, ground `bridge.py`, backend, dashboard.
The CAN protocol version stays 2 (new keys are additive), but the Pi and bridge need the new key
names for the dashboard to show the calibration values.

## 7. Re-test on the car (in this order)

1. Flash. Boot log: `servo calibration: vcu_calibration.h (nothing saved on the car yet)`,
   `knob levels CLK=1 DT=1`, `knob = STEERING WHEEL ... (PCNT hardware counter)`.
2. `status`: state IDLE even if a self-test item warns. `knob`: turn one click → position ±1.
3. Steering: turn the knob both ways. If clockwise steers LEFT → `cal reverse 1`. If one click
   moves two steps → set `VCU_CAL_KNOB_COUNTS_PER_CLICK 4`.
4. Calibrate the steering end points and centre (§2). Check full lock both ways does not buzz.
5. Calibrate each flap FLAT / MAX. Then turn the knob right: **left flaps rise, right flaps
   drop**; left: the opposite. Wheels straight: all four equal.
6. `cal save`, power-cycle, `cal` → "saved on the car".
7. Watch the 5 V rail with the knob swung lock to lock quickly (all five servos move together).
   If the VCU resets (boot log "BROWN-OUT"), lower the flap slew rate (key 19, Vehicle Setup).
8. Dashboard: steering and flap angles follow the knob; Car control shows the knob clicks.
9. Wheels off the ground / on the rollers: **Run motor** at 30 %, drag to 60 % and release,
   **Stop motor**. Then run it and kill the bridge / Pi: the motor must stop within ~1 s (Mode A)
   or ~3 s (LoRa) and stay stopped.
10. E-STOP from the dashboard while the motor runs: motor off, steering straight, flaps flat.
    Reset: back to IDLE, motor stays off.
11. Unplug a DS18B20 and reboot: a warning, but the knob and the motor still work.

## 8. Test totals (software)

- Firmware compile (arduino-cli, esp32 core 3.3.2, `--warnings all`, 0 warnings):
  classic ESP32 465,431 B · ESP32-C6 prototype 463,334 B · ESP32-C6 rev B 463,400 B.
- Hardware tests 63 (was 61): host self-test rewritten for FW 2.3 (knob steering, flap law,
  pulse maths, calibration keys + preview, dashboard motor incl. deadman/timeout/fault/e-stop,
  severity changes) and `test_fw23_chain.py` — two end-to-end runs through the real backend,
  Pi gateway (Mode A and LoRa) and the real `vcu_logic.h` as a virtual car.
- Backend 73 (was 63): `tests/test_fw23.py`.
- Analysis 25. Dashboard: `tsc` clean, `eslint` 0 errors, `next build` clean.


## FW 2.3.11 — IMU mounting really saved
- `imu level` / `imu nose` / `imu flip` were written to flash while the storage was closed, so they were lost at the
  next reset (and the boot auto-level then redid only the "level" half). Now saved properly; `imu reset` too.
- The one-time 85 % -> 100 % duty-cap migration had the same fault and is now saved.

## FW 2.3.12 — AI steering, AI Model page (Monza)
- **CONFIG key 27 `aiSteerCmd`** (command): steering angle in tenths of a degree, + = left, clamped to the steering
  lock. It replaces the knob while new values keep arriving (`CFG_AI_STEER_TIMEOUT_MS` 1500 ms). Turning the knob
  takes steering back at once and refuses key 27 for 3 s (`CFG_AI_STEER_KNOB_HOLD_MS`). Refused in FAULT / E-STOP
  and, from the dashboard, when the ground link is down. Circuit runs, demos and a driver packet still outrank it.
  The flaps follow the steering as they do with the knob. Reported value: the angle in force, 0 when the knob steers.
  `steer_source` 4 = AI.
- Backend `/ws/ai-command` (still OFF unless `VCU_AI_COMMAND_LINK=1`): also forwards `steer_deg` to key 27 (change of
  >= 0.5 deg, at most every 0.2 s, repeated every 0.6 s) and centres the steering when the link closes or goes quiet.
- Dashboard: new **AI Model** tab (`/ai-model`). One model for Monza (`lib/monza-model.json`, `lib/track-model.ts`),
  two modes: "AI recommends" (nothing sent) and "AI drives" (ARM -> motor duty key 9, straight mode key 25, steering
  key 27, five times a second, sent for where the car will be 0.5 s later). The model was trained on the VCU's own
  Monza circuit run (`SOFTWARE/tools/monza_model/`), so it imitates the stored map.
- Pi `main.py` and ground `bridge.py`: key 27 name only. Virtual car: `--speed-model` (wheel speed from duty, as the car).
