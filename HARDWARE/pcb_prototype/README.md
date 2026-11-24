# Prototype carrier PCB ("MajorProject-VCU", hobby modules)

| File | What |
|---|---|
| `MajorProject-VCU_Gerbers_GBR-1.zip` | Gerbers + drill files of the final version reviewed on 2026-10-01 (86.05 × 99.75 mm, 2 layers) |
| `VCU_PCB_1to1_fitcheck.pdf` | 1:1 A4 print of the top side with pin names and two 100 mm check rulers. Print at 100 % / "Actual size" |

Wiring for this board: `SOFTWARE/Documentation/VCU_final_connections.txt`.
U1 is currently a classic ESP32 DevKit stand-in (the ESP32-C6 was killed by 12 V on 2026-11-12):
`SOFTWARE/Documentation/VCU_classic_ESP32_stopgap.md`.

Notes from the 2026-10-01 review, still valid:
- The INA260_VBUS pad needs no wire if the module's VB jumper is closed (VBUS tied to Vin+).
- The 12 V supply runs through the INA260 module's own screw terminal, not through this board.
- C3 1000 µF / C4 100 nF sit at the 5 V input (J15). If the servos still twitch under load,
  add a second 470–1000 µF across +5V/GND at the servo headers.
- No mounting holes on this revision.
