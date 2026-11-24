# Pinout and Wiring

> **Stage 2d: the VCU section of this document is superseded.** The VCU pin map and motor wiring that the firmware now uses are in **`VCU_final_connections.txt`**, and the changes from Stage 2c are in `VCU_wiring_changes.txt`. The main differences:
> - Hall RL moved to GPIO3.
> - Flap RR is on GPIO8, with a 10k pull-up.
> - An L298N is driven from GPIO17 alone (ENA+ENB, with a 10k pull-down). Its direction is strapped, so the car is forward only.
> - No BTS7960 and no ADS1115.
>
> The Pi, ground-radio and camera sections below still apply.

Stage 2 — new document. Everything here is **design intent, not measured**: no board has been wired. Pin numbers must match the firmware; if you change one, change it in `main_updated_corrected__1_.ino` SECTION 1 too.

## 1. ESP32-C6-Pico (VCU)

This board does not break out GPIO12/13 (native USB), and its ADC only exists on GPIO0-6. Six analogue signals therefore do not fit on-chip: four of them live on the ADS1115.

**Stage 2c correction:** the board's ESP32-C6-MINI-1 module has **no GPIO10 or GPIO11** (module pins are IO0-9, IO12-15, IO18-23, RXD0/TXD0 — Waveshare schematic), so the motor pins moved to 3/16/17. GPIO22/23 are shared with the on-board TCA9554 expander (SDA/SCL). The console is native USB (`USB CDC On Boot: Enabled` is now enforced at compile time), so UART0's GPIO16/17 are free.

| GPIO | Function | Direction | Notes |
|---|---|---|---|
| 0 | Flap LF pot feedback | ADC1 in | Through divider (§5) |
| 1 | Flap RF pot feedback | ADC1 in | Through divider |
| 2 | Hall FR | in, pull-up | |
| 3 | BTS7960 R_EN + L_EN (enable) | out | **Stage 2c** (was GPIO11, which does not exist). Via level shifter; 10 k **pull-down** |
| 4 | Hall RR | in, pull-up | Strapping pin (MTMS) — an idle-high Hall output is fine |
| 5 | CAN TX -> SN65HVD230/VP230 `D` | out | Strapping pin (MTDI); the transceiver input is high-Z at boot |
| 6 | I2C SDA | open-drain | The GY-521 module already carries 4.7 k pull-ups — do not add more |
| 7 | I2C SCL | open-drain | Same |
| 8 | Hall RL | in, pull-up | Also drives the on-board RGB LED (harmless flicker). Strapping pin: must be high at reset, which the pull-up ensures |
| 9 | DS18B20 1-Wire | bidirectional | 4.7 k pull-up to 3V3, which also holds the BOOT strap high |
| 14 | Hall FL | in, pull-up | |
| 15 | CAN RX <- transceiver `R` | in | Strapping pin; the transceiver idles high (recessive) |
| 18 | Steering servo PWM | out | LEDC 50 Hz |
| 19 | Flap LF servo PWM | out | |
| 20 | Flap RF servo PWM | out | |
| 21 | Flap LR servo PWM | out | |
| 22 | Flap RR servo PWM | out | |
| 16 | BTS7960 LPWM (direction) | out | **Stage 2c** (was GPIO10, which does not exist). U0TXD: boot text toggles it, harmless while EN is low |
| 17 | BTS7960 RPWM | out | **Stage 2c** (was GPIO23). LEDC 20 kHz, via level shifter |
| 23 | **spare** | — | Also the on-board TCA9554 SCL — leave free |

**I2C devices:** MPU6050 `0x68` (AD0 -> GND), INA260 `0x40` (A0/A1 -> GND), ADS1115 `0x48` (ADDR -> GND).

**Until the ADS1115 arrives** the firmware is built with `VCU_HAVE_ADS1115 0`: pack voltage comes from the INA260's VBUS register, and steering, LR and RR run without position feedback. Only the LF/RF wiper wires (GPIO0/1) are checked (`VCU_FB_WIRED_MASK 0x06`; set `0x00` if those wires are not soldered yet).

**ADS1115 channels (once fitted):** AIN0 battery divider · AIN1 steering feedback · AIN2 flap LR feedback · AIN3 flap RR feedback. The firmware cycles them as `1, 2, 1, 3, 0` at 100 Hz, so steering is sampled at 40 Hz and everything else at 20 Hz — far above what a servo loop needs.

**Why the split:** four flap feedbacks + steering + battery = six analogue signals, but GPIO5 and GPIO6 (two of the seven ADC pins) are already CAN TX and I2C SDA. Two flaps stay on the chip's ADC, the rest move to the ADS1115. The per-servo calibration absorbs the small scaling difference between the two sources.

## 2. CAN bus

```
ESP32-C6 GPIO5 ──► D  ┌───────────────┐ CANH ──────────┬──────────  CANH  ┌──────────────┐
ESP32-C6 GPIO15 ◄─ R  │ VP230 module  │                │ (twisted pair)   │ Pi CAN HAT   │
3V3 ─────────────► VCC│  Rs → GND     │ CANL ──────────┼──────────  CANL  │ (MCP2515)    │
GND ─────────────► GND└───────────────┘               120 Ω             120 Ω (HAT jumper)
```

- 120 Ω at **each end only**. Measure ≈ 60 Ω across CANH/CANL with everything powered off.
- On the VP230 breakout, `Rs` is already handled; leave the on-board 120R jumper FITTED (this is one end of the bus) and set the HAT's CAN0 120R switch to ON.
- **Common ground between the VCU and the Pi is mandatory.** On the 2-channel HAT that is the terminal marked **G** next to H/L. A missing ground was the first real bring-up fault on this project (no frames, `can0` ERROR-PASSIVE).
- **Which Linux interface is which terminal is not fixed.** Linux names `can0`/`can1` in probe order; on this Pi `can0` came up as `spi0.1` (the chip on CE1). Run `sudo HARDWARE/raspberry_pi/setup/can_up.sh`: it lists `canX -> spi0.Y` and tells you which interface actually carries VCU frames. Pass that to the gateway as `--can-channel`.
- `ip link ... restart-ms 100` (done by `can_up.sh`) lets the MCP2515 recover from bus-off by itself.
- The `oscillator=` value in `config.txt` must match the crystal printed on the HAT. A wrong value still passes the loopback test but runs the bus at the wrong bitrate.
- Keep the pair twisted and away from the motor leads.

## 3. Raspberry Pi 4B

The 2-channel CAN HAT occupies SPI0 with **both** chip selects plus GPIO23/25, so the radio goes on SPI1.

| Function | Pi BCM | Physical pin | Notes |
|---|---|---|---|
| CAN HAT | SPI0 CE0/CE1, SCLK 11, MOSI 10, MISO 9, INT GPIO25/23 | — | Seated on the header; CAN0 terminal block, 120R switch ON |
| SX1278 NSS | GPIO18 (SPI1 CE0) | 12 | Needs `dtoverlay=spi1-1cs` -> `/dev/spidev1.0` |
| SX1278 SCK | GPIO21 (SPI1 SCLK) | 40 | |
| SX1278 MOSI | GPIO20 (SPI1 MOSI) | 38 | |
| SX1278 MISO | GPIO19 (SPI1 MISO) | 35 | |
| SX1278 RST | GPIO17 | 11 | `--sx1278-reset` |
| SX1278 DIO0 | GPIO27 | 13 | RxDone/TxDone; wired, but RX is polled |
| SX1278 VCC | 3V3 | 1 or 17 | **Never 5 V.** 10 uF close to the module |
| SX1278 GND | GND | 6, 9 | At least two ground wires |
| Drive link, backend, video | Wi-Fi | — | No GPIO |

The Ra-02 has no BUSY pin, no TCXO control and no TX/RX enable pins — it switches its own RF path.

## 4. Power

```
3S LiPo (11.1 V nom, 12.6 V full)
 ├── BTS7960 B+ ─── traction motor          (high current, thick wire, fuse)
 ├── Buck #1 6 V ≥ 10 A ──► 5 servos        [BOM GAP: sized for stall current]
 ├── Buck #2 5 V ≥ 3 A ───► Raspberry Pi 4B
 └── Buck #2 (or #3) 5 V ─► ESP32-C6 (5V pin), ESP32-CAM, level shifter
 All grounds joined at one point near the battery.
```

- **Do not power the servos from the same rail as the Pi.** Five MG996Rs can pull ~2.5 A each at stall; a shared 5 V supply will brown out the Pi mid-run. The BOM lists one buck — budget a second.
- 470–1000 µF low-ESR capacitor across the servo rail, close to the servos.
- The INA260 has an integrated 2 milliohm shunt. **Its VBUS pin must be connected** (to IN+ or IN−; many breakouts do this with a solder jumper, some leave a separate VBUS pin) — without an ADS1115 it is the firmware's only pack-voltage source, and a floating VBUS reads 0 V and keeps the VCU in FAULT. Wire IN+ to the battery positive (after the fuse) and IN- to the distribution point, so all current flows through its terminal block. **Its continuous rating is about 15 A** — the overcurrent threshold is set to 14 A to match the sensor, not the motor.
- Fuse the pack (e.g. 40 A) before anything else.

## 5. Servo feedback modification (MG996R)

Each servo needs a wire soldered to its internal potentiometer wiper and brought out. The wiper swings roughly between 0 V and the servo supply (up to 6 V), which exceeds both the ESP32-C6 ADC input (3.3 V) and the ADS1115 (VDD + 0.3 V).

```
wiper ──[ 10 kΩ ]──┬──► ADC input        Vout = Vwiper × 10/(10+10) = Vwiper/2
                   │                      6 V → 3.0 V  (inside 3.3 V)
                [ 10 kΩ ]
                   │
                  GND                     100 nF to GND at the ADC pin
```

`[PLACEHOLDER]` — confirm the actual wiper range on your servos before trusting these resistor values; the calibration procedure (`CalibrationPlan.md`) then maps raw counts to degrees, so exact ratios do not need to be known in advance, only that nothing exceeds 3.3 V.

## 6. BTS7960 (IBT-2) interface

| IBT-2 pin | Connect to | Note |
|---|---|---|
| RPWM | GPIO17 via level shifter | 20 kHz PWM |
| LPWM | GPIO16 via level shifter | Direction level |
| R_EN, L_EN | GPIO3 via level shifter (tie both) | 10 kΩ pull-down to GND |
| VCC | 5 V | Module logic supply |
| GND | common ground | |
| B+ / B− | battery | Thick wire, fuse |
| M+ / M− | motor | |

The module buffers its inputs with a 74HC244 running at 5 V, whose guaranteed input-high threshold is above 3.3 V. Use a 74AHCT125 (or equivalent 3.3 V → 5 V shifter). Without it the bridge may behave erratically or not at all.

## 7. Hall sensors

**Stage 2c — the modules on the bench are LM393/A3144 breakout boards: power them from 3V3, not 5 V.** Those boards pull DO up to their own VCC through an on-board resistor, so a 5 V supply puts 5 V on the ESP32-C6 pin (max 3.6 V). DO is active-LOW (magnet present = LOW, module LED on); the firmware counts falling edges. If a module will not trigger reliably at 3V3, power it from 5 V only with a 10 k / 20 k divider on DO. A bare A3144 (no board) is open-collector: 5 V supply is fine there, with the output pulled up to 3V3 only (the internal pull-up is enabled; add an external 10 kΩ if the run is long). One magnet per wheel by default (`CFG_WHEEL_PULSES_PER_REV = 1`); more magnets give better low-speed resolution — update the constant if you add them.

## 8. ESP32-CAM

Independent device: 5 V supply, own Wi-Fi connection, stock `CameraWebServer` sketch. Note its IP and enter `http://<ip>:81/stream` on the dashboard's Live Camera page. Its 5 V rail should be well decoupled — brownouts during transmission are the usual cause of resets.

## 9. Ground station radio (second Ra-02 + classic ESP32 dev module)

| ESP32 dev module (WROOM-32) | SX1278 |
|---|---|
| GPIO5 | NSS |
| GPIO18 | SCK |
| GPIO19 | MISO |
| GPIO23 | MOSI |
| GPIO14 | NRST |
| GPIO26 | DIO0 |
| 3V3 | VCC |
| GND | GND |

VSPI pins, chosen because the classic ESP32 has no GPIO20 and GPIO1 is the USB serial TX. Avoid GPIO6-11 (flash) and GPIO34-39 (input-only). **Attach the antenna before powering up** — transmitting without one can damage the PA, and both antennas must be 433 MHz types.

## 10. Pre-power checklist

1. Continuity: every ground joined; no short between 3V3/5 V/pack and GND.
2. Polarity of the pack connector, INA260 shunt and motor leads.
3. CAN: ≈ 60 Ω across CANH/CANL; CANH and CANL not swapped.
4. Servo feedback dividers measured with a meter at both travel extremes — must stay under 3.3 V.
5. BTS7960 enable line reads 0 V with the ESP32-C6 in reset.
6. Antenna (433 MHz) fitted to both radios.
7. Wheels off the ground.
