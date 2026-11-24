# VCU stop-gap: classic ESP32 DevKit on the prototype PCB   (FW 2.2.2, 2026-11-12)

**Why:** the SmartElex ESP32-C6 Pico (U1) was killed by about 1 s of 12 V on 2026-11-12. The
prototype PCB itself works. Until a replacement C6 is fitted, a classic ESP32 DevKit (30-pin,
ESP-WROOM-32, CP2102 USB) is jumpered into the empty U1 socket.

**Be upfront at the review:** the classic ESP32 is an Xtensa LX6, **not RISC-V**. It is a
temporary stand-in. The control logic, CAN protocol, Pi gateway, ground station, backend and
dashboard are unchanged; only the pin map differs.

**Firmware: done in FW 2.2.1 (pin map) and FW 2.2.2 (CAN TX/RX swap, §9).** The same sketch builds for both chips. The pin map follows
the board you select in the Arduino IDE (`vcu_hw_profile.h`):

| Arduino IDE board | Builds | Console |
|---|---|---|
| **ESP32 Dev Module** | the classic ESP32 stand-in (this document) | the DevKit's USB (CP2102, UART0), 115200 |
| ESP32C6 Dev Module, USB CDC On Boot: Enabled | the real C6 VCU, unchanged | native USB, 115200 |

The car still reports hardware profile 0 (prototype) on CAN 0x148, so the dashboard shows
"Prototype". The boot banner and `status` show `hw=prototype (hobby modules) on classic ESP32
stand-in` and `MCU = classic ESP32 (Xtensa)`.

"Socket pin" below = the U1 header pin number on the PCB (the "Hdr pin" column of
`VCU_final_connections.txt` §1).

---

## 1. Jumper map, in the DevKit's physical pin order (antenna at the top)

### DevKit LEFT side, top to bottom
| DevKit pin | GPIO | Signal | PCB socket pin | Was on the C6 |
|---|---|---|---|---|
| EN | — | leave free | — | — |
| VP | 36 | RL_FB (ADC, rear-left flap feedback tap) | 35 | GPIO0 |
| VN | 39 | RR_FB (ADC, rear-right flap feedback tap) | 34 | GPIO1 |
| D34 | 34 | ENC_CLK (KY-040) | 26 | GPIO22 |
| D35 | 35 | ENC_DT (KY-040) | 27 | GPIO23 |
| D32 | 32 | HALL_RL | 31 | GPIO3 |
| D33 | 33 | HALL_RR | 4 | GPIO4 |
| D25 | 25 | HALL_FL | 9 | GPIO14 |
| D26 | 26 | HALL_FR | 32 | GPIO2 |
| D27 | 27 | SERVO RL (rear-left flap PWM) | 17 | GPIO21 |
| D14 | 14 | SERVO RR (rear-right flap PWM) | 11 | GPIO8 |
| D12 | 12 | **do not use** (boot strap) | — | — |
| D13 | 13 | 1-Wire (DS18B20 ×2) | 12 | GPIO9 |
| GND | — | GND | 38 | — |
| VIN | — | 5 V in (from VSYS) | 39 | — |

### DevKit RIGHT side, top to bottom
| DevKit pin | GPIO | Signal | PCB socket pin | Was on the C6 |
|---|---|---|---|---|
| D23 | 23 | SERVO FR (front-right flap PWM) | 16 | GPIO20 |
| D22 | 22 | I2C SCL | 7 | GPIO7 |
| TX0 / RX0 | 1 / 3 | leave free (USB console) | — | — |
| D21 | 21 | I2C SDA | 6 | GPIO6 |
| D19 | 19 | SERVO FL (front-left flap PWM) | 15 | GPIO19 |
| D18 | 18 | SERVO STEER | 14 | GPIO18 |
| D5 | 5 | leave free (strap) | — | — |
| TX2 | 17 | **CAN RX** (from module CRX: J1 pin 3) — see §9 | 5 | GPIO5 |
| RX2 | 16 | **CAN TX** (to module CTX: J1 pin 4) — see §9 | 10 | GPIO15 |
| D4 | 4 | MOTOR_PWM (L298N ENA+ENB) | 2 | GPIO17 |
| D2 | 2 | leave free (on-board LED, strap) | — | — |
| D15 | 15 | leave free (strap) | — | — |
| GND | — | GND (second wire) | 33 | — |
| 3V3 | — | feeds the PCB +3.3V rail | 36 | — |

Total: 19 signals + VIN + 3V3 + 2× GND = **23 jumpers**.

Check that the DevKit is a **WROOM** board. On a WROVER board, GPIO16/17 belong to the PSRAM
and CAN would not work.

## 2. The same map, by signal (for checking against the firmware)

| Signal | Classic ESP32 GPIO | `vcu_hw_profile.h` |
|---|---|---|
| Steering servo (MG995) | 18 | `VCU_PIN_SERVO_STEER` |
| FL flap (SG90) | 19 | `VCU_PIN_SERVO_LF` |
| FR flap (SG90) | 23 | `VCU_PIN_SERVO_RF` |
| RL flap (MG996R) | 27 | `VCU_PIN_SERVO_LR` |
| RR flap (MG996R) | 14 | `VCU_PIN_SERVO_RR` |
| RL feedback tap | 36 (VP) | `VCU_PIN_FB_LR` |
| RR feedback tap | 39 (VN) | `VCU_PIN_FB_RR` |
| Motor PWM (L298N ENA+ENB) | 4 | `VCU_PIN_MOTOR_PWM` |
| CAN TX / RX | **16 / 17** (FW 2.2.2; the DevKit's TX2/RX2 labels are UART2 names and do not apply) | `VCU_PIN_CAN_TX` / `VCU_PIN_CAN_RX` |
| I2C SDA / SCL (MPU6050 0x68, INA260 0x40) | 21 / 22 | `VCU_PIN_I2C_SDA` / `VCU_PIN_I2C_SCL` |
| 1-Wire (DS18B20 ×2) | 13 | `VCU_PIN_ONEWIRE` |
| Hall FL / FR / RL / RR | 25 / 26 / 32 / 33 | `VCU_PIN_WHEEL_*` |
| KY-040 CLK / DT | 34 / 35 | `VCU_PIN_ENC_CLK` / `VCU_PIN_ENC_DT` |

The firmware refuses to compile if a later edit puts an output on an input-only pin (34–39),
a hall input on 34–39 (no pull-ups there), or any signal on a flash, console or strapping pin.

## 3. Power — read before connecting
- The DevKit's AMS1117 regulator now supplies the whole +3.3V rail (MPU6050, INA260, halls,
  DS18B20s, CAN module, KY-040): about 100 mA plus the ESP32's own draw. Fine for a 1 A regulator.
- **Remove the dead Pico from the board first.** A dead chip can clamp the 3.3 V rail or a
  signal line. With the board unpowered, check socket 36 (3V3) to GND is not near 0 Ω.
- Pull J17 (VSYS jumper). Set the buck to 5.0–5.2 V with no load and measure it. Only then refit J17.
- USB and VIN together are OK on this DevKit: it has a diode on the USB 5 V side.
- **What killed the C6:** 12 V on a 5 V or 3.3 V pin. Before every power-up, check with a meter that
  nothing on the 12 V side (adapter, INA260 IN+/IN−, L298N 12 V terminal) touches VSYS, 3V3
  or any DevKit pin.

## 4. PCB pull-ups/downs — all still correct on the new pins
- R6 4.7k pull-down on MOTOR_PWM → GPIO4: holds the motor off through reset. GPIO4 is not a strap pin.
- R1 4.7k on 1-Wire → GPIO13. R9/R10 I2C pull-ups → GPIO21/22.
- R5 10k pull-up on the RR servo net → GPIO14: harmless. GPIO14 can pulse briefly at boot,
  so the RR flap may twitch at power-up.
- GPIO34/35/36/39 are input-only with no internal pull-ups. The encoder relies on the
  KY-040's own 10k pull-ups; the feedback lines are driven by the 10k/10k dividers.
- Halls on 25/26/32/33 use INPUT_PULLUP, as before.

## 5. Flashing
1. Arduino IDE → Board: **ESP32 Dev Module** (esp32 core 3.x, the same core as for the C6).
   Leave the other options at their defaults.
2. Flash with **nothing but USB** connected the first time.
3. If the upload stalls at `Connecting...`, hold **BOOT** until it starts writing.
4. Serial Monitor at **115200**, line ending **Newline**. The banner must show
   `FW 2.2.2-stage2e`, `prototype (hobby modules) on classic ESP32 stand-in`,
   `[CAN] TWAI 500 kbit/s TX=16 RX=17 : started`, and
   `MCU = classic ESP32 (Xtensa) - TEMPORARY stand-in for the ESP32-C6`.

## 6. Recalibrate — the calibration lived in the dead C6's flash
The new chip boots with **defaults**. Do this with the linkages unhooked, the VCU in IDLE:
1. `pulse 0 <min> <max>` for the MG995 steering, so ±30° does not hit a stop.
2. `servo 3 0` → `cal 3 min`, `servo 3 45` → `cal 3 max` (rear-left MG996R).
3. `servo 4 0` → `cal 4 min`, `servo 4 45` → `cal 4 max` (rear-right MG996R).
4. `ds`, then `tempmap <battery_idx> <motor_idx>`.
5. `cal show`, `cal save`, `bench off`.
6. On the rollers: `mode dyno`, then `dyno cal`.
7. `mode track` if you are going to drive (the default at boot).

## 7. Bring-up order
1. Flash over USB with nothing else connected; check the banner and `status`.
2. Beep every jumper with a meter, from the DevKit pin to its destination connector on the PCB.
3. Power wires only (VIN, 3V3, 2× GND), board powered: check 3.3 V and 5 V.
4. Add I2C (`i2cscan` must list 0x40 and 0x68), then 1-Wire (`ds` must list 2 sensors), halls
   (turn each wheel by hand and watch `status`), CAN (`status` shows CAN rx/tx counting and the
   Pi heartbeat), then the servos (linkages off), then the encoder (`knob`).
5. The motor last, on the rollers, in DYNO mode.
6. Then run the FW 2.2 re-test list (`ChangeLog_FW2.2.md` §8, "Both builds" + "Prototype only").

## 8. Going back to the C6
1. Fit the new SmartElex ESP32-C6 Pico into U1. Remove the jumpers first: a DevKit pin left
   on a socket pin would fight the C6.
2. Arduino IDE → Board: **ESP32C6 Dev Module**, USB CDC On Boot: **Enabled**. Flash the
   **same** sketch: it picks the C6 pin map by itself.
3. Recalibrate again (section 6): calibration is stored in each chip's own flash.

## 9. CAN module orientation (FW 2.2.2)
The PCB's J1 is drawn **3V3, GND, CAN_TX, CAN_RX**, but the SN65HVD230 module's header is
**VCC, GND, CRX, CTX**. Plugged straight in, the module's CRX (transceiver output) sits on the
PCB's CAN_TX net (socket 5) and its CTX (transceiver input) on CAN_RX (socket 10).

Nothing needs rewiring: TWAI can use any GPIO, so FW 2.2.2 swaps the two pins in firmware
(`VCU_PROTO_CAN_SWAPPED 1` in `vcu_hw_profile.h`, prototype only):

| | Classic ESP32 | ESP32-C6 (later) |
|---|---|---|
| CAN TX → socket 10 → J1 pin 4 → module CTX | GPIO16 (RX2 jumper) | GPIO15 |
| CAN RX ← socket 5 ← J1 pin 3 ← module CRX | GPIO17 (TX2 jumper) | GPIO5 |

- Keep the module plugged straight into J1 and keep the jumpers as in §1.
- **Do not run FW 2.2.1 or older with the module plugged in this way.** The MCU's TX would drive
  against the transceiver's output. Flash 2.2.2 first.
- **Check the module's labels once with a meter (power off):** CTX must beep to pin 1 (D) of the
  SN65HVD230 chip and CRX to pin 4 (R). If it is the other way round, the labels mean "connect to
  MCU TX/RX"; then set `VCU_PROTO_CAN_SWAPPED 0`.
- If you ever cross the two wires by hand instead (socket 5 → CTX, socket 10 → CRX), set
  `VCU_PROTO_CAN_SWAPPED 0`.
- After flashing, the boot log shows the pins: `[CAN] TWAI 500 kbit/s TX=16 RX=17 : started`
  (classic) or `TX=15 RX=5` (C6). With the Pi running, `status` must show CAN rx counting and the
  Pi heartbeat, and the `TWAI` line must not show TEC climbing.
