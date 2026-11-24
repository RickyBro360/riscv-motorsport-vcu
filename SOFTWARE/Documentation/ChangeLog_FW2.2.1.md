# ChangeLog — FW 2.2.1 (`2.2.1-stage2e`), 2026-11-12

**One reason:** the prototype PCB arrived, was soldered and works, but the SmartElex ESP32-C6
Pico was killed by about 1 s of 12 V. A classic ESP32 DevKit is jumpered into the empty U1
socket as a **temporary stand-in** until a replacement C6 is fitted.

**No control-logic change.** `vcu_logic.h` differs from 2.2.0 only in the version string. The
CAN protocol (v2, including 0x148), the LoRa v3 frames, the config keys, the Pi gateway,
`bridge.py`, the backend and the dashboard are all unchanged. Nothing on the Pi or the laptop
needs to be re-deployed.

## 1. Firmware

| File | Change |
|---|---|
| `vcu_hw_profile.h` | The microcontroller is now detected from the board selected in the IDE (`CONFIG_IDF_TARGET_ESP32C6` / `CONFIG_IDF_TARGET_ESP32`). The ESP32-C6 pin map is untouched. New classic ESP32 pin map (prototype only; PCB rev B on a classic ESP32 refuses to compile). New `VCU_MCU_NAME`. |
| `main_updated_corrected__1_.ino` | The "USB CDC On Boot" check applies to the C6 only (the classic ESP32's console is UART0 via the CP2102, on pins the VCU does not use). Compile-time checks for the classic map: no output or hall input on input-only GPIO34–39; no signal on a flash, console or strapping pin. Boot banner names the MCU and the knob pins, and reminds you that calibration starts empty on a new chip. The three RTOS tasks are pinned to the Arduino core (`xTaskCreatePinnedToCore(..., ARDUINO_RUNNING_CORE)`). |
| `vcu_logic.h` | `FW_VERSION = "2.2.1-stage2e"` only. |

**Why the tasks are pinned:** the sensor and control tasks copy the ISR counters (wheel pulses,
encoder) inside `noInterrupts()`. That only masks the core it runs on. On the single-core C6
it always worked; on the dual-core classic ESP32 a task on core 0 could read a half-updated
value while the ISR ran on core 1. Pinning everything to the Arduino core (where the ISRs are
attached) gives the classic ESP32 the same behaviour as the C6. On the C6 nothing changes.

**What the car reports:** hardware profile 0 (prototype) on CAN 0x148 and config key 104, so
the dashboard shows "Prototype". The car electronics really are the prototype's; only the MCU
differs, and the console says so (`hw=prototype (hobby modules) on classic ESP32 stand-in`).

## 2. Classic ESP32 pin map

See `VCU_classic_ESP32_stopgap.md` for the jumper table, power notes, flashing, recalibration
and bring-up order.

| Signal | ESP32-C6 | Classic ESP32 |
|---|---|---|
| Steering servo | 18 | 18 |
| FL / FR flap | 19 / 20 | 19 / 23 |
| RL / RR flap | 21 / 8 | 27 / 14 |
| RL / RR feedback (ADC1) | 0 / 1 | 36 / 39 |
| Motor PWM | 17 | 4 |
| CAN TX / RX | 5 / 15 | 17 / 16 |
| I2C SDA / SCL | 6 / 7 | 21 / 22 |
| 1-Wire | 9 | 13 |
| Hall FL / FR / RL / RR | 14 / 2 / 3 / 4 | 25 / 26 / 32 / 33 |
| KY-040 CLK / DT | 22 / 23 | 34 / 35 |

## 3. Tests (all passing, 2026-11-12)

| Check | Result |
|---|---|
| Compile, ESP32-C6 prototype (`esp32c6:CDCOnBoot=cdc`) | clean, 447,888 B (34 %) |
| Compile, ESP32-C6 PCB rev B (`-DVCU_HW_PROFILE=1`) | clean, 447,974 B (34 %) |
| Compile, classic ESP32 prototype (`esp32:esp32:esp32`) | clean, 451,247 B (34 %), no sketch warnings with `--warnings all` |
| Classic ESP32 + PCB rev B | refuses to compile, as intended |
| Classic ESP32 with a servo moved to GPIO34 (negative test) | refuses to compile, as intended |
| `HARDWARE/tests` | 61 passed |
| `SOFTWARE/backend` tests | 63 passed |
| `SOFTWARE/analysis` tests | 25 passed |

Compiled with arduino-esp32 core 3.3.2 and Arduino's own ctags, which is what the Arduino IDE
uses to generate prototypes. The dashboard was not rebuilt: no dashboard file changed.

## 4. Hardware re-test list (classic ESP32 stand-in)

1. Banner: `FW 2.2.1-stage2e`, `MCU = classic ESP32 (Xtensa) ...`.
2. `i2cscan` → 0x40 and 0x68. `ds` → 2 sensors, then `tempmap`.
3. Each hall: turn the wheel by hand, `status` shows its speed.
4. CAN: `status` shows rx/tx counting and the Pi heartbeat; the dashboard shows live data.
5. Servos (linkages off): `servo 0..4`, then `pulse 0 ...`, `cal 3/4 min/max`, `cal save`.
6. Feedback: `status` shows a measured angle for LR and RR that follows the command.
7. `knob`: one detent = 1 % duty cap.
8. Motor on the rollers in DYNO: `motor 20`, `motor stop`, then `dyno cal`.
9. Then the FW 2.2 re-test list, `ChangeLog_FW2.2.md` §8 ("Both builds" and "Prototype only").

## 5. Still open
- Replace the C6 (the project title says RISC-V). Then select ESP32C6 Dev Module, flash the
  same sketch, and recalibrate.
- Everything in `NEW_CHAT_HANDOFF.txt` §7 is still open (FW 2.2 has not yet run on the car).
