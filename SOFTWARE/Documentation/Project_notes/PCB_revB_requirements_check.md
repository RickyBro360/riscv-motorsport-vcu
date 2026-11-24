# PCB rev B — requirements check (2026-10-26)

Automated check of the rev B KiCad design against the project requirements. The rev B Gerbers,
BOM and CPL live in the `F1_VCU_FW2.2_PCB_revB` folder. Three WARNs are still open before ordering.

| Area | Requirement | Result | Detail |
|---|---|---|---|
| VCU pin map | Every GPIO matches the rev B map (FL flap servo GPIO19 → GPIO16; MOTOR_EN new on GPIO19) | PASS | 20 GPIOs match the authoritative map |
| VCU pin map | EN (37), VBUS (40), RUN (30) not connected | PASS | all three open |
| VCU pin map | VSYS fed from 5V_LOGIC through a removable jumper (JP1) | PASS | U9.39 = VSYS = JP1.2; JP1.1 = V5L |
| VCU pin map | GPIO22/23 go only to the encoder (no I2C on the TCA9554 lines) | PASS | GPIO22/23 connect to ['R_E1', 'R_E2'] |
| Boot & safety | GPIO8 (strap) pulled HIGH: 10k to 3V3 | PASS | pull-ups on SRV_RR_M: ['R49'] |
| Boot & safety | GPIO9 (strap) pulled HIGH: 4.7k 1-Wire pull-up | PASS | ['R50'] |
| Boot & safety | GPIO17 MOTOR_PWM pulled LOW (motor off during reset) | PASS | pull-downs: ['R16'] (4.7k) |
| Boot & safety | GPIO19 MOTOR_EN pulled LOW (kill switch stays off until firmware enables it) | PASS | pull-downs: ['R7'] (10k) |
| Boot & safety | No motor-control signal on a pin the boot ROM drives | PASS | GPIO16 (U0TXD) carries SRV_FL_M: worst case a front-flap twitch at boot |
| Boot & safety | With PWM low, both BTN8982s sit low-side-on (motors braked, not driven) | PASS | INH tied high via 10k; IN pulled low via R16 → LSS on (datasheet truth table) |
| 12 V input | Reverse-polarity protection on the input | PASS | Q1 IPD90P03P4L-04: drain = VIN, source = V12, gate clamp D1 10 V |
| 12 V input | Bulk + TVS on the 12 V bus before the kill switch | PASS | C1 1000 µF + D2 SMBJ18A on V12 |
| 5 V buck | TLS4125: FREQ open (2.2 MHz), SYNC/SSON/RT to GND, EN pulled to 12 V | PASS | pin 1 open; pins 2/12/13 GND; EN via R2 100k |
| 5 V buck | Output ≥ 2 × 22 µF and 2.2 µH ≥ 4.5 A Isat (datasheet table, 1.6–2.8 MHz) | PASS | 66 µF nominal output; L1 XAL5030-222 (Isat 9.2 A) |
| 5 V buck | Pin 14 (FB) on the 5 V output — fixed V50 part | WARN | connected to V5L. CONFIRM in the TLS4125D0EPV50 pin table before ordering |
| Kill switch | BTS7004 exposed pad on V12 (VS), not GND | PASS | EP net = V12 |
| Kill switch | RIN 4.7k, RDEN 4.7k, RGND 47 Ω (datasheet table 22) | PASS | R4 4.7k, R5 4.7k, R6 47R |
| Kill switch | Capacitance after the BTS7004 ≤ 220 µF (inrush vs overload trip) | PASS | 100.2 µF on V12M → ~27 A at 0.27 V/µs vs 103 A min trip |
| Motor drive | BTN8982 IN/INH series resistors > 7 kΩ (datasheet 6.2) | PASS | IN: ['R8', 'R10'], INH: R9, R11 — all 10k |
| Motor drive | One half-bridge per motor, motor between OUT and GND | PASS | J3 = U4 OUT/GND, J4 = U5 OUT/GND |
| CAN | SN65HVD267 RXD (5 V) divided to a safe 3.3 V level | PASS | 1k/2k divider: RXD high reaches the C6 as 2.61–3.40 V (C6 VIH 0.75·VDD = 2.48 V; abs max VDD + 0.3 = 3.6 V). TXD VIH = 2 V so 3.3 V drive is fine |
| CAN | S pin to GND (normal mode), split termination on a jumper | PASS | U8.8 = GND; R43/R44 60.4 Ω + C30 4.7 nF via JP2 |
| Wheel speed | TLE4941 7/14 mA levels straddle the comparator threshold (worst case) | PASS | low ≤ 0.63 V, high ≥ 0.89 V, threshold 0.76 V |
| Wheel speed | Sensor sees ≥ 4.5 V at its leads (datasheet minimum) | PASS | worst case ~8.5 V with an 11.4 V adapter |
| Wheel speed | Four wheel connectors, each on its own comparator channel and GPIO | PASS | FL→GPIO14, FR→GPIO2, RL→GPIO3, RR→GPIO4 |
| Servos | RL/RR feedback: 10k/10k divider + 100 nF into GPIO0/GPIO1 | PASS | [(['C33', 'R45', 'R47'], ['C34', 'R46', 'R48'])] |
| Servos | Servo headers: 1 GND, 2 +5V (middle), 3 PWM, 4 FB | PASS | J14–J18 checked |
| Servos | Servo rail bulk ≥ 470 µF next to the headers | PASS | C31 1000 µF on V5S |
| Connectors | Every off-board device has a connector | PASS | J1, J3, J4, J12, J13, J10, J_IMU, J_INA, J_T1, J_T2, J_ENC, J_FL..J_RR, J5, 5 servo headers |
| Copper | VIN: 12 V input → reverse-protection FET carries 2.2 A | PASS | narrowest copper on the path 1.20 mm → 2.7 A @10 °C rise, 3.7 A @20 °C (1 oz) |
| Copper | V12: FET → BTS7004 (motor supply) carries 2.2 A | PASS | narrowest copper on the path 1.20 mm → 2.7 A @10 °C rise, 3.7 A @20 °C (1 oz) |
| Copper | V12: FET → servo-buck terminal carries 2.2 A | PASS | narrowest copper on the path 1.20 mm → 2.7 A @10 °C rise, 3.7 A @20 °C (1 oz) |
| Copper | V12: FET → TLS4125 input carries 0.6 A | PASS | narrowest copper on the path 1.00 mm → 2.4 A @10 °C rise, 3.2 A @20 °C (1 oz) |
| Copper | V12M: BTS7004 OUT → left half-bridge carries 2.2 A | PASS | narrowest copper on the path 1.20 mm → 2.7 A @10 °C rise, 3.7 A @20 °C (1 oz) |
| Copper | V12M: BTS7004 OUT → right half-bridge carries 2.2 A | WARN | narrowest copper on the path 0.80 mm → 2.0 A @10 °C rise, 2.8 A @20 °C (1 oz) |
| Copper | MOT_L: U4 OUT → motor L terminal carries 2.2 A | PASS | narrowest copper on the path 1.00 mm → 2.4 A @10 °C rise, 3.2 A @20 °C (1 oz) |
| Copper | MOT_R: U5 OUT → motor R terminal carries 2.2 A | PASS | narrowest copper on the path 1.00 mm → 2.4 A @10 °C rise, 3.2 A @20 °C (1 oz) |
| Copper | V5S: servo 5 V in → far end of the servo bus carries 3.0 A | PASS | narrowest copper on the path 2.00 mm → 4.0 A @10 °C rise, 5.4 A @20 °C (1 oz) |
| Copper | V5L: every track carries 0.6 A | PASS | narrowest 0.30 mm → 1.0 A |
| Copper | Servo stall (~8 A peak) on the 2 mm bus | WARN | 2.0 mm carries 5.4 A @20 °C on 1 oz: fine for running servos, marginal for a full 3-servo stall. Order 2 oz copper or add solder over a mask opening if you see the bus heat |
| Board | 100 × 80 mm, 2 layers, 4 × M3 | PASS | 100.0 × 80.0 mm, 2 layers |
| DRC | No DRC errors | PASS | 176 items: 0 errors, 176 warnings |
| DRC | Every net fully connected | PASS | 0 unconnected pads |
