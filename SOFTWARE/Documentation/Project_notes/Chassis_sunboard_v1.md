# Car chassis — HAND-CUT SUNBOARD v1 (2026-11-06) — PROTOTYPE (hobby-sensor) build
(supersedes the plywood v0 of 2026-10-31)

Build guide (Claude Doc): https://claude.ai/artifact/2GFWRL63QcXtCBKVnSg3KV — tab "Assembly instructions" = 12 illustrated steps
3D model (interactive, from the template outlines): https://claude.ai/artifact/3ANfjPTYQSL9twR6ybkE5o
Templates + parametric source: F1_VCU_chassis_v1_handcut.zip (car.py params, views.py checks, fitcheck.py tab/slot test,
build.py PDFs, a1a4.py, export3d.py + viewer_template.html). 69 pieces: 62 in 5 mm sheet, 7 in 2-3 mm sheet.
Printed set: templates_A1_main_PRINT_100pct.pdf (56 pieces) + templates_A4_small_parts_PRINT_100pct.pdf (13 pieces).
(The chassis zip is a separate package; it is not inside this project folder.)

## Status 2026-11-07 / 2026-11-12
- Team printed the A1 + A4 v1 templates (verified identical to final), stuck them on "foam board" and CUT ALL PARTS
  before the wheel diameter / sheet thickness were confirmed. Material (PVC sunboard vs paper foam board) not yet confirmed:
  the assembly tab opens with a 743-on-an-offcut test; paper foam board -> hot glue / PVA for joints, Araldite structural.
- Three corrections for the already-cut parts (in the assembly tab; source fixed, later prints correct):
  1. Deck screw holes were over the BH3/BH4 centres (nothing to bite). Nut blocks go on BH3 back face / BH4 front face;
     pierce new deck holes 15 mm toward the deck middle (right side also 10 mm toward centreline): x 320 / 385, y +50 / -40.
  2. Knuckle stack is 40 mm in a 40 mm gap: no nylon washers; wax or packing tape on the sliding faces.
  3. (2026-11-12) The earlier steering-sweep check ignored the front-wing endplates: uncut (48 mm long) they leave only 2.8 mm
     to a 70 mm tyre at +-30 deg and are hit by 75+ mm wheels. Cut 8 mm off each endplate's rear edge (40 mm long):
     clearance 10.7 mm (70 mm wheels), 8.5 (75), 6.4 (80).
- Bearings: only 4 x 606ZZ (6x17x6) for the two free front wheels; rear wheels ride on the motor gearbox bushes.

## Build method (team decision 2026-11-06)
- Everything cut BY HAND with blade cutters, glued with 743 superglue (Metlok cyanoacrylate) + Araldite. No plywood, saw,
  laser or 3D printing. Thin parts were meant to be 2-3 mm sunboard (CA dissolves paper foam board).
- Load-bearing parts laminated from 5 mm sheet: motor plates, rear pylons, BH3/BH4, crash plate, nut blocks = 2 layers (10 mm);
  knuckles = 3 layers (15 mm) with two 606ZZ set in Araldite (stub bolt through them while curing for alignment).
- Araldite for laminations, bearings, nuts, servo tabs; 743 for edge joints (sunboard only). Penny washer under every screw/nut.
  Holes by circle cutter / awl / pin vise — never hot tools (PVC fumes). Flap horns = spare servo horns (no template).

## Geometry
- Coordinates: x rearward from nose tip, y + = LEFT, z up. Wheelbase 340 (FA x=110, RA x=450), length 560.
- Width: front 216, rear 230. Plank 10..15, floor 15..20 (floor front edge 8 mm behind plank front), knuckle plates 20..50,
  upper C-arm 55..60 (x 100..120 so the M3x25 + 10 mm ball studs clear it by 2.7 mm at full lock). Deck top 65.
- Wheels: design value 70 mm dia x 30 mm wide, 4 identical, 6 mm hub with grub screw. Axle height is fixed by the cut parts,
  so plank ground clearance = D/2 - 25 mm (60 -> 5, 70 -> 10, 80 -> 15). Firmware/Pi/bridge assume 70 mm: update if different.
- Motors: 12 V Johnson SIDE-SHAFT GEARED 500 rpm B grade, transverse, body UP, gearbox face on inside of 10 mm plates
  (y +-70..80), 6x M3x16 (holes transferred from the gearbox face). Solder leads before fitting.
- Front: kingpins M3x65 at y +-50; M6x50 DIN931 stub axle (head inboard, fitted before the knuckle goes in).
  MG995 in 5 mm bridge (M3x12), shaft forward, 20 mm horn forward, 1:1, +-30 deg.
- Front flaps: SG90s on inner face of 5 mm nose sides (y +-16, nose 42 wide), shaft toward nose tip, horns UP both ends.
  Nose hangs from the front bulkhead face; prop nose bottom 20 mm above the table (car on wheels) while gluing.
- Rear flaps: MG996R on 10 mm pylons (y +-20..30, M3x20), shaft toward rear; servo horn REARWARD, flap horn FORWARD level
  with hinge (536,106); rod through 10x4 slot in mainplane at y +-48.
- Electronics: L298N in left sidepod bolted through floor; buck left sidepod front; 12 V jack + fuse + CAN exit right sidepod;
  KY-040 through right sidepod roof; VCU board on deck (M3x16 + 5 mm spacers). Engine cover Velcroed, ESP32-CAM T-cam. Pi off-car.
  (2026-11-12: the VCU board currently carries the classic ESP32 DevKit stand-in on jumpers; secure the DevKit and the
  jumpers so vibration cannot pull a wire. See VCU_classic_ESP32_stopgap.md.)
- Servo ids used in the steps: 0 steering, 1 FL, 2 FR, 3 RL (fb), 4 RR (fb). Calibrate with rods unhooked.

## ASSUMED (never confirmed before cutting)
Wheel diameter 70 / width 30; sheet thickness 5.0 / 3.0; Johnson shaft offset 8, boss 13; VCU board 100x80 with MPU6050 on
board; servo tab heights (MG 26.6, SG 15.9); buck 60x40x25; L298N 43/37; DC jack 11.5. Measure one motor's stall current.
