# PCB rev B — VCU connections (FW 2.2, `VCU_HW_PROFILE_PCB_REV_B`)

This is the authoritative wiring for the **rev B PCB** build of the VCU. The prototype (hobby
modules on the carrier board) keeps `VCU_final_connections.txt`. Both builds run the same
firmware logic: only `vcu_hw_profile.h` differs, so pins below are the ones in that header.

Board: 100 × 80 mm, 2 layers, 4 × M3. Gerbers, BOM and CPL are in the `VCU_revB_gerbers.zip`
release. `VCU_revB_requirements_check.md` lists every electrical check made on the layout.

## 1. GPIO map (SmartElex ESP32-C6 Pico, ESP32-C6-MINI-1)

| GPIO | Pico pin | Net | Goes to | Notes |
|---|---|---|---|---|
| 0 | 35 | FB_RL | J17 pin 4 through R45 10k / R47 10k + C33 100 nF | rear-left MG996R pot |
| 1 | 34 | FB_RR | J18 pin 4 through R46 10k / R48 10k + C34 100 nF | rear-right MG996R pot |
| 2 | 32 | WSS_FR | U6 LM393 ch B output, R_P 10k to 3V3 | wheel FR |
| 3 | 31 | WSS_RL | U7 LM393 ch A output | wheel RL (not GPIO8: strap) |
| 4 | 4 | WSS_RR | U7 LM393 ch B output | wheel RR |
| 5 | 5 | CAN_TX | U8 SN65HVD267 TXD | |
| 6 | 6 | SDA | J_IMU, J_INA; R51 4.7k to 3V3 | MPU6050 0x68, INA260 0x40 |
| 7 | 7 | SCL | J_IMU, J_INA; R52 4.7k to 3V3 | |
| 8 | 11 | SRV_RR | J18 PWM through 220 Ω; **R49 10k pull-up** | strapping pin: must be HIGH at reset |
| 9 | 12 | OW | J_T1, J_T2; R50 4.7k to 3V3 | DS18B20 ×2; also holds BOOT high |
| 14 | 9 | WSS_FL | U6 LM393 ch A output | wheel FL |
| 15 | 10 | CAN_RX | U8 RXD through 1k / 2k divider | 5 V → 3.3 V |
| **16** | 1 | SRV_FL | J15 PWM through 220 Ω | **moved from GPIO19** (U0TXD: a flap may twitch at boot, harmless) |
| 17 | 2 | MOTOR_PWM | both BTN8982 IN through 10k (R8, R10); J5 pin 1; **R16 4.7k pull-down** | 20 kHz (1 kHz with the L298N fallback) |
| 18 | 14 | SRV_STEER | J14 PWM through 220 Ω | MG995 |
| **19** | 15 | MOTOR_EN | BTS7004 IN through R4 4.7k; **R7 10k pull-down** | **new**: HIGH = 12 V to the half-bridges |
| 20 | 16 | SRV_FR | J16 PWM through 220 Ω | SG90 |
| 21 | 17 | SRV_RL | J17 PWM through 220 Ω | MG996R |
| 22 | 26 | ENC_CLK | J_ENC pin 3 through R_E1 1k | also the module's TCA9554 line: never start I2C here |
| 23 | 27 | ENC_DT | J_ENC pin 4 through R_E2 1k | same |

All 20 usable GPIOs are used. EN (37), VBUS (40) and RUN (30) are not connected. VSYS (39) is
fed from the 5 V logic buck through jumper **JP1** (fit the shunt; remove it to power the
module from USB alone while bringing the board up).

## 2. Connectors (everything off the board arrives on one of these)

| Ref | Label | Pins | Off-board device |
|---|---|---|---|
| J1 | 12V IN | 1 +12 V · 2 GND | from the **INA260 module IN−** (the INA260 sits in the adapter cable, inline 3 A fuse before it) |
| J3 / J4 | MOTOR L / MOTOR R | 1 OUT · 2 GND | rear Johnson motors (forward only; motor between OUT and GND) |
| J5 | L298N PWM | 1 MOTOR_PWM · 2 GND | fallback only: an L298N's ENA+ENB (build with `VCU_REVB_L298N_FALLBACK 1`) |
| J10 | CAN | 1 CANH · 2 CANL · 3 GND | Raspberry Pi MCP2515 HAT |
| JP2 | CAN TERM | shunt | fit = split 120 Ω termination on the VCU end |
| J12 | TO SERVO BUCK | 1 +12 V · 2 GND | input of the external ≥ 5 A servo buck |
| J13 | SERVO 5V IN | 1 +5 V · 2 GND | output of that buck (5.0–5.2 V) |
| J14 | SERVO STEER | 1 GND · 2 +5 V · 3 PWM | MG995 |
| J15 / J16 | SERVO FL / FR | 1 GND · 2 +5 V · 3 PWM | SG90 front flaps |
| J17 / J18 | SERVO RL / RR | 1 GND · 2 +5 V · 3 PWM · 4 FB | MG996R rear flaps + pot wiper |
| J_FL, J_FR, J_RL, J_RR | WHEEL xx | 1 sensor VDD · 2 sensor GND | **TLE4941plusC** 2-wire wheel sensors (off-board, at each wheel) |
| J_IMU | IMU | 1 3V3 · 2 GND · 3 SCL · 4 SDA | MPU6050 GY-521 near the centre of mass (AD0 → GND) |
| J_INA | INA260 | 1 3V3 · 2 GND · 3 SCL · 4 SDA | INA260 module (VBUS tied to IN− on the module) |
| J_T1, J_T2 | TEMP 1/2 | 1 3V3 · 2 GND · 3 DATA | DS18B20 battery / motor (`ds`, `tempmap`, `cal save`) |
| J_ENC | ENCODER | 1 3V3 · 2 GND · 3 CLK · 4 DT | KY-040 knob (SW not wired) |

Servo headers are GND / +5 V / PWM with +5 V in the **middle**, so a reversed plug cannot
reverse the supply. JST-XH connectors are keyed.

## 3. What the firmware assumes about this board (`vcu_hw_profile.h`)

* **Kill switch.** GPIO19 drives the BTS7004. The VCU turns it on only when the motor may run
  (DRIVE with a driver, or an accepted run) and off on IDLE, FAULT, EMERGENCY_STOP and at every
  reset (R7 holds it off before the firmware starts). The dashboard shows it as **Motor power**.
* **Half-bridges.** Both BTN8982 IN pins follow GPIO17. IN low = low-side on = the motor is
  **braked**, not coasting. So on rev B the PWM off-time brakes: the duty → speed curve differs
  from the L298N prototype. **Re-run `dyno cal` and re-set the duty floor on rev B.**
* **Wheel sensors.** TLE4941 (7 / 14 mA) → 75 Ω sense → LM393 against 0.76 V → open collector,
  10k pull-up → GPIO. The GPIO goes HIGH while the sensor draws 14 mA: interrupt on the RISING
  edge, plain `INPUT`, 200 µs debounce. Pulses per revolution = teeth of the target gear, or
  pole **pairs** of the magnet ring. `VCU_REVB_WHEEL_PPR` is a **placeholder (1)**: count your
  target wheel and set it, then check `status` against a hand-turned wheel.
* **CAN transceiver.** SN65HVD267 (5 V). The firmware does not read its FAULT pin (TP_FLT).

## 4. Pre-power checklist (rev B)

1. Meter, board unpowered: J1 pin 1 to GND not shorted; V12 to GND not shorted; 3V3 to GND
   not shorted; J13 +5 V to GND not shorted.
2. Module **not** fitted, JP1 open: power J1 from the adapter. Expect 5.0 V at JP1 pin 1,
   12 V at J12, **0 V at J3/J4** (kill switch off), 0 V at GPIO17 and GPIO19 pads.
3. Fit the module and JP1. With the module held in reset: GPIO8 = 3.3 V, GPIO9 = 3.3 V,
   GPIO17 = 0 V, GPIO19 = 0 V.
4. Flash the **PCB rev B** firmware folder. `status` must print `hw=PCB rev B  MODE=TRACK
   state=IDLE … selftest=0x00` and `kill switch off`.
5. `mode dyno`, wheels off the ground: `motor 30`. Expect J3/J4 to come up and both rear wheels
   to turn forward; `motor stop` must drop motor power to OFF.
6. Hand-turn each wheel: its speed must show in `status`. Set `VCU_REVB_WHEEL_PPR` if the speed
   is off by a whole factor.
