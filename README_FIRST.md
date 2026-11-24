# F1 VCU — FW 2.3.2 — PROTOTYPE build (hobby sensors)

> **FW 2.3.0 (2026-11-13) — read `SOFTWARE/Documentation/ChangeLog_FW2.3.md` first.**
> The KY-040 knob is the steering wheel; the four flaps follow the steering (turning right: left
> flaps up, right flaps down, in proportion); the motor runs from the dashboard (Car control →
> Run motor, any drive mode); only real faults stop the motor; the dashboard lag is fixed.
> **Servo end points:** `HARDWARE/esp32/main_updated_corrected__1_/vcu_calibration.h`, or live on
> the dashboard (Vehicle Setup → Servo calibration), or the console (`cal`).
> Re-deploy everything (firmware, Pi, bridge, backend, dashboard). Not yet run on the car.

This folder is the project for the car **as built for the prototype**: hobby modules on the
carrier board.

> **2026-11-12 — current state.** The prototype PCB was received, soldered and works. The
> SmartElex ESP32-C6 Pico was then killed by about 1 s of 12 V. Until a replacement C6 is fitted,
> a **classic ESP32 DevKit** (ESP-WROOM-32) is jumpered into the empty U1 socket.
> **FW 2.2.2** builds the same sketch for either chip and swaps the CAN TX/RX pins to match how
> the SN65HVD230 module plugs into the PCB; nothing else in the system changed.
> The classic ESP32 is Xtensa, **not RISC-V**: present it as a temporary stand-in.

| Part | In this build |
|---|---|
| VCU | SmartElex ESP32-C6 Pico (RISC-V) — **currently replaced by a classic ESP32 DevKit stand-in** |
| Wheel speed | 4× LM393/A3144 hall modules, one magnet per wheel |
| Motor | L298N (ENA+ENB on the motor PWM pin, 1 kHz, forward only), no kill switch |
| CAN | SN65HVD230 module → Raspberry Pi 4B + Waveshare 2-CH CAN HAT |
| Front-left flap servo | GPIO19 (both chips) |
| Everything else | MPU6050, INA260, 2× DS18B20, MG996R rear flaps with feedback, SG90 front flaps, MG995 steering, KY-040 knob |

## Flashing — pick the board in the Arduino IDE; the pin map follows it

| Arduino IDE board | When | `status` shows |
|---|---|---|
| **ESP32 Dev Module** | now: the classic ESP32 stand-in | `hw=prototype (hobby modules) on classic ESP32 stand-in  MODE=TRACK` |
| **ESP32C6 Dev Module**, USB CDC On Boot: **Enabled** | once the C6 is replaced | `hw=prototype (hobby modules)  MODE=TRACK` |

Sketch: `HARDWARE/esp32/main_updated_corrected__1_/`. Arduino-ESP32 core 3.x, no external
libraries. The car electronics are chosen by one line in `vcu_hw_profile.h`
(`#define VCU_HW_PROFILE VCU_HW_PROFILE_PROTOTYPE`). The PCB rev B folder
(`F1_VCU_FW2.2_PCB_revB`) can take these same three firmware files with that line set to
`VCU_HW_PROFILE_PCB_REV_B`; rev B is C6-only.

**After flashing a new chip, recalibrate:** calibration is stored in the chip's own flash.
See `SOFTWARE/Documentation/VCU_classic_ESP32_stopgap.md` §6.

## Start here
0. `HOW_TO_RUN.md`: the exact commands for the laptop, the Pi and the ground station, the
   current IP addresses (10.49.218.x) and the file tree on the Pi.
1. `SOFTWARE/Documentation/VCU_classic_ESP32_stopgap.md`: the jumper map, power checks,
   recalibration and bring-up for the stand-in.
2. `SOFTWARE/Documentation/DemoRunGuide_FW2.2.md`: how to run the DYNO presentation and TRACK driving.
3. `SOFTWARE/Documentation/VCU_final_connections.txt`: the PCB wiring for this build.
4. `SOFTWARE/Documentation/ChangeLog_FW2.2.2.md`, `ChangeLog_FW2.2.1.md` and `ChangeLog_FW2.2.md`: what changed, the
   test totals and the re-test lists.
5. `SOFTWARE/Documentation/NEW_CHAT_HANDOFF.txt`: full project context to paste into a new chat.

FW 2.3: the Raspberry Pi gateway (`main.py`), the ground `bridge.py`, the backend and the
dashboard all changed — copy all of them. The ground radio sketch and the ESP32-CAM are unchanged.
