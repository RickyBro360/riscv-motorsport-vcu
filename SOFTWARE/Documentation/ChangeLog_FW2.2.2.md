# ChangeLog — FW 2.2.2 (`2.2.2-stage2e`), 2026-11-12

**One reason:** on the prototype PCB, header J1 is drawn **3V3, GND, CAN_TX, CAN_RX**, but the
SN65HVD230 module's header is **VCC, GND, CRX, CTX**. Plugged straight in, the module's receive
output (CRX) lands on the PCB's CAN_TX net (socket pin 5) and its transmit input (CTX) on
CAN_RX (socket pin 10). With FW 2.2.1 or older the MCU would transmit into the transceiver's
output and never hear the bus.

**Fix: in firmware, not on the board.** The ESP32's CAN controller (TWAI) can use any GPIO,
so the prototype profile now swaps TX and RX. Nothing is rewired; the module stays plugged
straight into J1.

| Build | CAN TX (→ socket 10 → module CTX) | CAN RX (← socket 5 ← module CRX) |
|---|---|---|
| Prototype, classic ESP32 stand-in | GPIO16 (the RX2 jumper) | GPIO17 (the TX2 jumper) |
| Prototype, ESP32-C6 | GPIO15 | GPIO5 |
| PCB rev B (C6) | GPIO5 (unchanged) | GPIO15 (unchanged) |

- Switch: `VCU_PROTO_CAN_SWAPPED` in `vcu_hw_profile.h`, default 1. Set 0 if the two signal
  wires are ever crossed by hand, or if a meter shows the module's labels are the other way round
  (CTX must beep to SN65HVD230 pin 1 = D, CRX to pin 4 = R).
- GPIO5 on the C6 is a strapping pin that only matters in download mode; the transceiver output
  idles HIGH, so it is safe as RX.
- `vcu_logic.h`: version string only. CAN protocol, Pi, bridge, backend and dashboard unchanged.
- Next PCB revision: draw J1 in the module's pin order.

## Checks (2026-11-12)
| Check | Result |
|---|---|
| Resolved pins (preprocessor) | classic 16/17, C6 prototype 15/5, C6 rev B 5/15, classic with `VCU_PROTO_CAN_SWAPPED=0` 17/16 |
| Compile, C6 prototype | clean, 447,888 B |
| Compile, C6 rev B | clean, 447,974 B |
| Compile, classic ESP32 | clean, 451,247 B, 0 warnings (`--warnings all`) |
| `HARDWARE/tests` / backend / analysis | 61 / 63 / 25 passed |

## Re-test on the car
1. Flash 2.2.2 **before** powering the board with the CAN module plugged in.
2. Boot log: `[CAN] TWAI 500 kbit/s TX=16 RX=17 : started` (classic) or `TX=15 RX=5` (C6).
3. Pi running (`can_up.sh`, then `main.py`): `status` shows CAN rx counting, the Pi heartbeat, and
   the `TWAI` line with TEC not climbing. The dashboard shows live data.
4. If TEC climbs to 128+ and nothing is received: check the module labels with a meter (above),
   common GND to the HAT's G, CANH/CANL not swapped, ~60 Ω across H–L with power off, and which HAT
   channel (`can0`/`can1`) is wired.

## Network update (same day, no firmware logic change)
The `CTRL` network moved to 10.49.218.x: laptop 1 = 10.49.218.128, Pi = 10.49.218.178,
ESP32-CAM = 10.49.218.243. Updated: the ESP32-CAM `BACKEND_HOST` (reflash the camera once),
the Pi's `THIS_PI_IP` (informational) and its test, the VCU's Wi-Fi-UDP bench `PI_IP` (unused with
CAN), `drive_client.py` examples, `vcu-gateway.service`, the dashboard dev-origin list, comments,
`BringUpGuide.md`, `VCU_wiring_map.html` and the handoff. New `HOW_TO_RUN.md` at the project root
has every command. Historical Stage 2c/2d documents keep their old addresses.

## FW 2.2.3 (`2.2.3-stage2e`), 2026-11-13 — boot diagnostics
Reason: on the classic ESP32 stand-in the VCU rebooted in a loop right after
`drive mode at boot: TRACK`, the point where all five servos used to start at once.
- The boot log prints `[BOOT] last reset: ...` (power-on, BROWN-OUT, CRASH, watchdog...) with a hint.
- Servos start one at a time, 150 ms apart (LF, RF, LR, RR, then steering), each printed as
  `[BOOT] servo <name> on GPIO<n>`, then `[BOOT] I2C, 1-Wire, wheel and knob inputs`, then `[BOOT] CAN`.
  The last line before a reboot shows the step that fails.
- No control-logic change. Compiles clean: classic ESP32 452,091 B, C6 448,792 B.

## FW 2.2.4 (`2.2.4-stage2e`), 2026-11-13 — classic ESP32 reboot loop
The reset reason was `rst:0xc (SW_CPU_RESET)`: on a classic ESP32 that is the reboot after a
firmware PANIC (a brown-out would be `rst:0xf`). Nothing in the firmware calls a software restart.
Two causes that exist only on the classic ESP32 are removed (C6 build unchanged in behaviour):
- **Task stacks doubled** (`VCU_STACK_SCALE 2`): Xtensa code uses noticeably more stack than RISC-V,
  and an overflow panics. `status` now prints the free stack of each task.
- **Knob polled every 1 ms** instead of CHANGE interrupts: GPIO34/35 have no pull-ups, so a
  floating pin (KY-040 unplugged or without its own pull-ups) fired the interrupt continuously,
  tripping the interrupt watchdog.
If it still reboots, the `Guru Meditation Error` / `Backtrace` lines printed just before the reboot
name the cause. Compiles clean: classic ESP32 452,323 B, C6 449,086 B.
