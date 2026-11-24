/*
 * =============================================================================
 * vcu_hw_profile.h — WHICH HARDWARE this VCU firmware is built for
 * =============================================================================
 * The control logic (vcu_logic.h) is identical for every build. Only the pins and the
 * driver details below differ. Two independent choices:
 *
 * 1. THE CAR ELECTRONICS — pick with ONE line (VCU_HW_PROFILE below):
 *   VCU_HW_PROFILE_PROTOTYPE  the Stage 2c/2d prototype: hobby modules on the carrier
 *                             board (A3144 hall modules, L298N, SN65HVD230 module)
 *   VCU_HW_PROFILE_PCB_REV_B  the rev B PCB: TLE4941 wheel sensors through LM393
 *                             comparators, BTS7004 kill switch, 2x BTN8982 half-bridges,
 *                             SN65HVD267 CAN, TLS4125 5 V buck
 *
 * 2. THE MICROCONTROLLER — picked AUTOMATICALLY from the board selected in the Arduino IDE:
 *   "ESP32C6 Dev Module"  SmartElex ESP32-C6 Pico (ESP32-C6-MINI-1, RISC-V) — the real VCU.
 *                         GPIO10/11 do not exist, GPIO12/13 are the USB port, GPIO22/23 are
 *                         also the module's TCA9554 I2C lines (only ever read here as plain
 *                         inputs for the encoder — never start I2C on them).
 *   "ESP32 Dev Module"    FW 2.2.1: classic ESP32 DevKit (ESP-WROOM-32, Xtensa LX6, NOT
 *                         RISC-V), jumpered into the empty U1 socket of the PROTOTYPE board
 *                         after the C6 was killed by 12 V (2026-11-12). TEMPORARY stand-in
 *                         until a replacement C6 is fitted. Prototype profile only.
 *                         Wiring: SOFTWARE/Documentation/VCU_classic_ESP32_stopgap.md
 *   The car still reports hardware profile 0 (prototype) on CAN 0x148: the sensors,
 *   drivers and protocol are unchanged, so the Pi, bridge.py, backend and dashboard need
 *   no update. The console and the boot banner name the stand-in MCU.
 * =============================================================================
 */
#pragma once

#define VCU_HW_PROFILE_PROTOTYPE 0
#define VCU_HW_PROFILE_PCB_REV_B 1

#ifndef VCU_HW_PROFILE
#define VCU_HW_PROFILE VCU_HW_PROFILE_PROTOTYPE  // <-- the ONE line that differs between the two project folders
#endif

// ---------------------------------------------------------------- which microcontroller
#if defined(CONFIG_IDF_TARGET_ESP32C6)
#define VCU_MCU_CLASSIC_ESP32 0
#define VCU_MCU_NAME "ESP32-C6 (RISC-V)"
#elif defined(CONFIG_IDF_TARGET_ESP32)
#define VCU_MCU_CLASSIC_ESP32 1
#define VCU_MCU_NAME "classic ESP32 (Xtensa) - TEMPORARY stand-in for the ESP32-C6"
#else
#error "Arduino IDE: select board \"ESP32C6 Dev Module\" (the VCU) or \"ESP32 Dev Module\" (classic ESP32 stand-in)"
#endif

#if !VCU_MCU_CLASSIC_ESP32
// ---------------------------------------------------------------- ESP32-C6: shared by both builds
#define VCU_PIN_FB_LR 0          // rear-left MG996R pot wiper -> 10k/10k + 100 nF -> ADC1_CH0
#define VCU_PIN_FB_RR 1          // rear-right MG996R pot wiper -> ADC1_CH1
#define VCU_PIN_CAN_TX 5         // as drawn on both PCBs (prototype: see VCU_PROTO_CAN_SWAPPED below)
#define VCU_PIN_CAN_RX 15
#define VCU_PIN_I2C_SDA 6        // MPU6050 0x68 + INA260 0x40
#define VCU_PIN_I2C_SCL 7
#define VCU_PIN_WHEEL_FL 14
#define VCU_PIN_WHEEL_FR 2
#define VCU_PIN_WHEEL_RL 3       // NOT GPIO8: a strapping pin
#define VCU_PIN_WHEEL_RR 4
#define VCU_PIN_ONEWIRE 9        // DS18B20 x2, 4.7k pull-up also holds the BOOT strap high
#define VCU_PIN_MOTOR_PWM 17     // U0RXD: an input during boot, held LOW by the external pull-down
#define VCU_PIN_SERVO_STEER 18
#define VCU_PIN_SERVO_RF 20
#define VCU_PIN_SERVO_LR 21
#define VCU_PIN_SERVO_RR 8       // strapping pin: 10k pull-up to 3V3 keeps it HIGH at reset
#define VCU_PIN_ENC_CLK 22       // KY-040 through 1k (shares the module's TCA9554 SDA)
#define VCU_PIN_ENC_DT 23        // KY-040 through 1k (shares the module's TCA9554 SCL)
#else
// ---------------------------------------------------------------- classic ESP32 stand-in (FW 2.2.1)
// DevKit pin -> PCB U1 socket pin, see VCU_classic_ESP32_stopgap.md. Avoided on purpose:
// GPIO0/2/5/12/15 (strapping pins), GPIO1/3 (USB console via the CP2102), GPIO6-11 (flash).
// GPIO34/35/36/39 are INPUT-ONLY with NO internal pull-ups: used only for the two ADC
// feedback taps (driven by the 10k/10k dividers) and the KY-040 (its own 10k pull-ups).
#if VCU_HW_PROFILE != VCU_HW_PROFILE_PROTOTYPE
#error "The classic ESP32 stand-in is wired for the PROTOTYPE board only. Select ESP32C6 Dev Module for PCB rev B."
#endif
#define VCU_PIN_FB_LR 36         // VP  -> socket 35 (RL_FB tap), ADC1_CH0
#define VCU_PIN_FB_RR 39         // VN  -> socket 34 (RR_FB tap), ADC1_CH3
#define VCU_PIN_CAN_TX 17        // TX2 -> socket 5  (PCB J1 pin 3). [WROVER boards: 16/17 are PSRAM — use a WROOM DevKit]
#define VCU_PIN_CAN_RX 16        // RX2 -> socket 10 (PCB J1 pin 4). Swapped below when VCU_PROTO_CAN_SWAPPED = 1
#define VCU_PIN_I2C_SDA 21       // D21 -> socket 6  (MPU6050 0x68 + INA260 0x40, R10 pull-up)
#define VCU_PIN_I2C_SCL 22       // D22 -> socket 7  (R9 pull-up)
#define VCU_PIN_WHEEL_FL 25      // D25 -> socket 9
#define VCU_PIN_WHEEL_FR 26      // D26 -> socket 32
#define VCU_PIN_WHEEL_RL 32      // D32 -> socket 31
#define VCU_PIN_WHEEL_RR 33      // D33 -> socket 4
#define VCU_PIN_ONEWIRE 13       // D13 -> socket 12 (R1 4.7k pull-up)
#define VCU_PIN_MOTOR_PWM 4      // D4  -> socket 2  (R6 4.7k pull-down keeps the motor off through reset)
#define VCU_PIN_SERVO_STEER 18   // D18 -> socket 14
#define VCU_PIN_SERVO_RF 23      // D23 -> socket 16
#define VCU_PIN_SERVO_LR 27      // D27 -> socket 17
#define VCU_PIN_SERVO_RR 14      // D14 -> socket 11 (R5 10k pull-up is harmless here; GPIO14 may blip at boot)
#define VCU_PIN_ENC_CLK 34       // D34 -> socket 26 (input only: KY-040's own 10k pull-up)
#define VCU_PIN_ENC_DT 35        // D35 -> socket 27
#endif
#define VCU_HAVE_ENCODER 1       // 0 = no knob (then drive_client.py's wheel steers in ARMED/DRIVE)
// FW 2.3: counts per detent moved to vcu_calibration.h (VCU_CAL_KNOB_COUNTS_PER_CLICK, measured 2).

#if VCU_HW_PROFILE == VCU_HW_PROFILE_PROTOTYPE
// ================================================================ PROTOTYPE (hobby modules)
#define VCU_HW_PROFILE_ID 0      // vcu::HW_PROFILE_PROTOTYPE
#if VCU_MCU_CLASSIC_ESP32
#define VCU_HW_NAME "prototype (hobby modules) on classic ESP32 stand-in"
#else
#define VCU_HW_NAME "prototype (hobby modules)"
#endif
#define VCU_PIN_SERVO_LF 19      // front-left SG90 (D19 on the classic ESP32 too -> socket 15)
#define VCU_PIN_MOTOR_EN -1      // no kill switch on the prototype
// CAN on the prototype PCB (FW 2.2.2). J1 is drawn 3V3, GND, CAN_TX, CAN_RX, but the
// SN65HVD230 module's header is VCC, GND, CRX, CTX. Plugged straight into J1, the module's CRX
// (transceiver output) lands on the CAN_TX net and its CTX (transceiver input) on CAN_RX.
// TWAI can use any GPIO, so the firmware swaps the two pins instead of a hardware fix.
//   1 = module plugged STRAIGHT into J1 (as built, 2026-11-12)
//   0 = the two signal wires crossed by hand (socket 5 -> module CTX, socket 10 -> module CRX)
#ifndef VCU_PROTO_CAN_SWAPPED
#define VCU_PROTO_CAN_SWAPPED 1
#endif
#if VCU_PROTO_CAN_SWAPPED
#undef VCU_PIN_CAN_TX
#undef VCU_PIN_CAN_RX
#if VCU_MCU_CLASSIC_ESP32
#define VCU_PIN_CAN_TX 16        // RX2 jumper -> socket 10 -> J1 pin 4 -> module CTX (transceiver D)
#define VCU_PIN_CAN_RX 17        // TX2 jumper -> socket 5  -> J1 pin 3 -> module CRX (transceiver R)
#else
#define VCU_PIN_CAN_TX 15        // socket 10 -> J1 pin 4 -> module CTX (transceiver D)
#define VCU_PIN_CAN_RX 5         // socket 5  -> J1 pin 3 -> module CRX (transceiver R); idles HIGH, safe on strap GPIO5
#endif
#endif
// Motor: L298N, ENA+ENB tied on GPIO17, IN1/IN3 strapped to +5V, IN2/IN4 to GND (forward only).
// PWM off-time = bridge disabled = the motor COASTS.
#if VCU_MCU_CLASSIC_ESP32
#define VCU_MOTOR_DRIVER_NAME "L298N (ENA+ENB on GPIO4, forward only)"
#else
#define VCU_MOTOR_DRIVER_NAME "L298N (ENA+ENB on GPIO17, forward only)"
#endif
#define VCU_MOTOR_PWM_HZ 1000    // bipolar L298N output stage: keep it slow
#define VCU_MOTOR_OFF_BRAKES 0
// Wheels: LM393/A3144 hall modules, open collector, one magnet per wheel, pulse = LOW.
#define VCU_WHEEL_SENSOR_NAME "A3144 hall modules, 1 magnet per wheel"
#define VCU_WHEEL_PIN_MODE INPUT_PULLUP
#define VCU_WHEEL_EDGE FALLING
#define VCU_WHEEL_PULSES_PER_REV 1
#define VCU_WHEEL_DEBOUNCE_US 2000
#define VCU_CAN_TRANSCEIVER_NAME "SN65HVD230 module (3.3 V)"

#elif VCU_HW_PROFILE == VCU_HW_PROFILE_PCB_REV_B
// ================================================================ PCB rev B
#define VCU_HW_PROFILE_ID 1      // vcu::HW_PROFILE_PCB_REV_B
#define VCU_HW_NAME "PCB rev B"
// GPIO16 is U0TXD: the boot ROM prints on it. A front flap servo tolerates that (worst case a
// twitch at power-up); the kill switch must not, so MOTOR_EN is on GPIO19 (quiet at boot).
#define VCU_PIN_SERVO_LF 16      // front-left SG90
#define VCU_PIN_MOTOR_EN 19      // BTS7004 IN through R4 4.7k, R7 10k pull-down: HIGH = 12 V to the bridges
// Motor: 2x BTN8982TA half-bridges, one per rear motor, motor between OUT and GND. IN of both
// <- GPIO17 through 10k each, INH tied high. IN high = high side on (drive), IN low = low side
// on (BRAKE). So PWM off-time brakes instead of coasting: redo `dyno cal` and the duty floor.
// Set VCU_REVB_L298N_FALLBACK 1 to drive an L298N from header J5 instead (1 kHz, coasts).
#ifndef VCU_REVB_L298N_FALLBACK
#define VCU_REVB_L298N_FALLBACK 0
#endif
#if VCU_REVB_L298N_FALLBACK
#define VCU_MOTOR_DRIVER_NAME "L298N on J5 (fallback, forward only)"
#define VCU_MOTOR_PWM_HZ 1000
#define VCU_MOTOR_OFF_BRAKES 0
#else
#define VCU_MOTOR_DRIVER_NAME "2x BTN8982TA (IN on GPIO17) + BTS7004 kill switch (GPIO19)"
#define VCU_MOTOR_PWM_HZ 20000   // above hearing; fine for the BTN8982 with SR = 5.1k
#define VCU_MOTOR_OFF_BRAKES 1
#endif
// Wheels: TLE4941plusC 2-wire current sensors -> 75 R sense -> LM393 (0.76 V threshold) -> 10k
// pull-up -> GPIO. Output HIGH while the sensor draws 14 mA. Clean comparator edges with
// hysteresis, so only a short debounce. Pulses per rev = teeth (gear) or pole PAIRS (magnet ring)
// of YOUR target wheel. [PLACEHOLDER] count it and set it here; the dashboard shows the value.
#define VCU_WHEEL_SENSOR_NAME "TLE4941 via LM393 comparators"
#define VCU_WHEEL_PIN_MODE INPUT
#define VCU_WHEEL_EDGE RISING
#ifndef VCU_REVB_WHEEL_PPR
#define VCU_REVB_WHEEL_PPR 1
#endif
#define VCU_WHEEL_PULSES_PER_REV VCU_REVB_WHEEL_PPR
#define VCU_WHEEL_DEBOUNCE_US 200
#define VCU_CAN_TRANSCEIVER_NAME "SN65HVD267 (5 V, RXD via 1k/2k divider)"

#else
#error "VCU_HW_PROFILE must be VCU_HW_PROFILE_PROTOTYPE or VCU_HW_PROFILE_PCB_REV_B"
#endif
