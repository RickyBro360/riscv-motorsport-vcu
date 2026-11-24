# ChangeLog — FW 2.2 (`2.2.0-stage2e`)

FW 2.2 starts from the Stage 2d / FW 2.1.1 archive. The architecture is unchanged: the VCU is the
only actuator authority, the AI only recommends or proposes, and the operator approves.
The 13 bugs listed in the handoff stay fixed, and their regression tests still pass.

**Status: compiled and tested in software only. Nothing in FW 2.2 has run on the car yet.**

## 1. One firmware, two hardware builds

`HARDWARE/esp32/main_updated_corrected__1_/vcu_hw_profile.h` holds every pin and driver detail.
Only one line differs between the two project folders:

| | `VCU_HW_PROFILE_PROTOTYPE` (folder *Prototype_hobby_sensors*) | `VCU_HW_PROFILE_PCB_REV_B` (folder *PCB_revB*) |
|---|---|---|
| Wheel sensors | A3144 hall modules, `INPUT_PULLUP`, falling edge, 2 ms debounce, 1 magnet | TLE4941 → LM393, `INPUT`, rising edge, 200 µs debounce, PPR = `VCU_REVB_WHEEL_PPR` (**placeholder 1**) |
| Motor driver | L298N ENA+ENB on GPIO17, 1 kHz, off-time coasts | 2× BTN8982 IN on GPIO17, 20 kHz, off-time **brakes**; L298N on J5 with `VCU_REVB_L298N_FALLBACK 1` |
| Kill switch | none | BTS7004 on **GPIO19** (MOTOR_EN) |
| Front-left flap servo | GPIO19 | **GPIO16** |
| CAN transceiver | SN65HVD230 module | SN65HVD267 |
| Wiring reference | `VCU_final_connections.txt` | `PCB_revB_connections.md` |

The control logic, the protocol, the backend and the dashboard are identical in both folders.
The VCU reports which build it is: key 104, CAN 0x148 and the console `status` line. The
dashboard shows it too.

## 2. Firmware

* **`VcuController`** (vcu_logic.h). The whole decision layer now sits in one hardware-free class:
  the state machine, the run manager, track runner, track-mode aero, traction control, motor,
  stall watchdog, kill switch, knob and config store. The `.ino` reads sensors into `VcuInputs`,
  calls `step()` and writes `VcuOutputs` to the pins. The same class is compiled for the laptop
  for the host tests and the virtual car, so the tests exercise the logic the ESP32 runs.
* **Drive modes**, key 5. **DYNO**: circuit runs, `dyno cal` and `motor` allowed. **TRACK**:
  only the driver with ARM; this is the default. The mode can change only in IDLE with no run
  active, and it is kept in NVS (`dmode`).
* **Dashboard run control.**
  * Key 6 (command): 0 stop, 1 circuit run, 2 dyno cal.
  * Key 7: laps per run (NVS `rlaps`).
  * A run is accepted only when the car is in DYNO, IDLE, has no fault, and has the ground link.
  * A remote run uses the ground link as its deadman. If the heartbeat is older than 1 s or the
    `lora_ok` bit is clear, the run stops and COMM_LOSS shows for 3 s.
  * A start that is resent is idempotent: it does not restart a run that is already going.
* **E-stop from the dashboard**, key 8 (command): 1 triggers the e-stop (always accepted);
  2 resets it, only from EMERGENCY_STOP, and re-runs the self-test.
* **Traction control** (TRACK, driver in DRIVE).
  * Key 22 sets the slip target: 0 = off, otherwise 50–500 ‰ (NVS `tcslip`).
  * Duty is cut at a gain of 2 × the slip above the target, up to 70 %, held for 150 ms, then
    released at 1.5/s.
  * It is trusted only above 3 km/h at the front.
* **Track-mode aero.** The flaps switch between straight and corner mode from the IMU, the steering
  and the throttle, with hysteresis and a 300 ms hold. In DYNO the map still drives them.
* **Kill switch (rev B).** It is on only while the motor may run: DRIVE, ARMED or an accepted run.
  It is off in IDLE, FAULT, E-stop and after a stall, and R7 holds it off through reset.
* **Stall watchdog** now also runs in DRIVE. It is still pulse-based (bug #9).
* **KY-040 knob** on GPIO22/23. The pins are plain inputs and I2C is never started there (bug #2).
  * A quadrature table in an ISR counts the steps.
  * Each detent moves the duty cap (key 17) by ±1 %, through the ConfigStore, so the change is
    range-checked and ACKed.
* **CAN 0x148 VCU_MODE** at 100 ms carries the mode, the hardware profile, the flags, the laps,
  the TC cut and the knob count (see `CANProtocol.md`).
* **Read-only keys:** 104 hardware profile, 105 wheel-sensor pulses per rev.
* **Command plumbing.** CAN writes go through a queue to the control task, and ACKs go through
  a queue back to the CAN task. Nothing writes the controller from two tasks.
* **Console.**
  * New commands: `mode dyno|track`, `tc <permille>`, `knob`.
  * `status` now shows the hardware, the mode, the kill switch, TC and why dashboard runs are
    allowed or refused.
* The 1-Wire pin is set to `OUTPUT_OPEN_DRAIN` once (bug #5).

## 3. Raspberry Pi gateway and ground bridge

* The Pi decodes 0x148 into the dashboard's `vcu` object.
* LoRa run/cal byte: bit 6 = drive mode, bit 7 = motor power. Same packet, LoRa v3.
* Both the Pi and the bridge poll for commands every **0.25 s** (was 1 s).
* The bridge sends an e-stop and a run stop ahead of anything else in its queue.
* The key tables in the firmware, the Pi, the bridge and the backend are checked equal by a test.

## 4. Backend

* **New endpoints:**
  * `GET /api/vehicle/status` reports the mode, the hardware, whether a run can start, and why not.
  * `POST /api/vehicle/mode`
  * `POST /api/vehicle/run` takes start, stop or dyno_cal, plus laps.
  * `POST /api/vehicle/estop` takes trigger or reset.
* **Outbox.**
  * An e-stop and a run stop go first.
  * Commands are resent every 1 s, up to 10 times, until the VCU ACKs.
  * An e-stop supersedes a run start that is still pending.
* **AI engineer, mode-aware:**
  * Insights cover the mode and the hardware, the kill-switch anomaly (rev B), and in TRACK mode
    the position, traction and cornering g.
  * New proposals: TC power, enable TC, and an asymmetric gain from the measured g.
  * The dyno-only proposals (lap length, aero optimise, duty floor, …) are not offered in TRACK.
  * Live track-mode recommendations come from the source `engineer_station_track_mode_v1`.
* **Camera.**
  * `POST /api/camera/announce`: the ESP32-CAM announces itself and the relay connects by itself.
  * An operator-typed URL that is delivering frames is never replaced.
  * `/api/camera/status` reports the ESP32-CAM relay.
* **Fix:** the latest frame is now chosen by row id, not the vehicle timestamp, so stale frames
  no longer show after a gateway restart. Frames recorded by the simulator are skipped.
* `tools/demo_car.py` plays the FW 2.2 rules: it boots in TRACK, refuses runs until DYNO, and
  handles stop, e-stop/reset and the laps target. `--hw prototype|pcb_rev_b`, and `--running`
  gives the old behaviour.

## 5. Dashboard

* **Car control panel** on Overview, Track Map and Vehicle Setup:
  * the mode switch, which asks you to confirm the car is on the rollers before DYNO;
  * VCU state, run, motor power, and TC or ground link;
  * laps, Start run, Stop, Dyno cal;
  * E-STOP and Reset;
  * the VCU's own verdict on each command.
* The top bar has a **drive-mode chip and a one-click E-STOP** on every page.
* Live Telemetry has a new row of tiles: drive mode, hardware, motor power, TC cut, ground link
  (as the VCU sees it) and flap mode.
* The tunables table hides the command keys. The Camera page shows the announce info. Simulation
  Mode understands the new commands.

## 6. ESP32-CAM

* It POSTs `/api/camera/announce` every 10 s. Set `BACKEND_HOST` to the laptop's IP.
* `/status` shows the announce counters.
* Initialiser warnings are fixed.
* Compiles at 1,161,255 bytes (`huge_app`).

## 7. Tests (all passing)

| Suite | Count | New in FW 2.2 |
|---|---|---|
| Hardware (`HARDWARE/tests`) | 61 | 0x148 parity; key tables equal (firmware = Pi = bridge); LoRa bits; e-stop queue priority; **two end-to-end chain tests with the real VCU logic** (`test_fw22_chain.py`) |
| Firmware host self-test | pass | `selftest_fw22`: keys, 0x148, e-stop/reset, TRACK refusal, link-loss deadman, laps target, TC, track aero, stall lockout, knob |
| Backend | 63 | `tests/test_fw22.py` (8) |
| Analysis | 25 | — |
| Dashboard | — | `tsc`, `eslint` (0 errors, 3 old warnings) and `next build` clean |
| ESP32-C6 compile | clean | prototype 447,758 B · rev B 447,844 B · rev B + L298N fallback 447,822 B (34 %) |

The chain tests run the actual backend, bridge (LoRa over UDP) and Pi gateway processes against
`vcu_logic.h` compiled for the host. They measured:

* Dashboard e-stop to EMERGENCY_STOP: **< 2.5 s over LoRa**, **< 1.5 s in Mode A**.
* A run refused in TRACK; accepted after DYNO; motor power on; stopped by the e-stop; reset back
  to IDLE.
* In TRACK, the driver drives with traction control, straight/corner aero follows, and the AI
  uses the track-mode source.

## 8. Hardware re-test list

### Both builds
1. `status`: the right `hw=`, `MODE=TRACK` after the first boot, `selftest=0x00`.
2. `mode dyno` and `mode track`, from the console and from the dashboard. Each is ACKed and
   survives a power cycle.
3. Dashboard **Start run** in DYNO (1 lap), **Stop**, **E-STOP**, **Reset**. Each shows "done
   (VCU ACK)".
4. Ground-link deadman: during a dashboard run, stop `bridge.py`. The run must stop within about
   1 s.
5. Knob: one detent changes the duty cap by 1 %, and the dashboard shows the new value.
6. TRACK on the floor at low duty: TC cuts when a rear wheel is spun by hand or on a slippery
   surface; the flaps go to straight mode on a straight.
7. Camera announces itself, and the Camera page connects without pasting a URL.

### Prototype only
8. Front-left flap on **GPIO19** (unchanged from 2d), plus the whole 2d re-test list
   (`ChangeLog_Stage2d.md` §7).

### PCB rev B only
9. The pre-power checklist in `PCB_revB_connections.md` §4.
10. Kill switch: J3/J4 sit at 0 V in IDLE and come up only for a run or in DRIVE. An e-stop drops
    them at once.
11. Count the target wheel and set `VCU_REVB_WHEEL_PPR`. Hand-turn each wheel and check the
    speed.
12. **Redo `dyno cal` and the duty floor.** BTN8982 braking changes the duty → speed curve.
13. Front-left flap on **GPIO16**. Expect a possible twitch at power-up from the boot ROM's UART
    text; this is harmless.
14. CAN through the SN65HVD267, and JP2 termination as your bus needs.

## 9. Known limits

* Steering sign convention is not yet confirmed on the car. The driver packet has + = RIGHT
  (`steering_permille`), while the map steering, the IMU lateral g and the yaw use + = LEFT.
  On the car, check that a left turn gives positive `gForceLat` and yaw and that the servo
  moves the wheels left. The asymmetric flap split and the track-mode corner detection
  depend on it.
* TC with one magnet per wheel (prototype) is coarse, so the default target is a generous 25 %.
  It works better on rev B with a multi-tooth target wheel.
* Over LoRa only the drive mode and the motor power travel. TC cut, ground link and knob are
  `null` on the dashboard in Mode B. They are logged on the Pi.
* The e-stop from the dashboard is a request carried over the radio. The driver's E key and the
  console `estop` are faster. Keep a hand near the 12 V plug during the demo.
