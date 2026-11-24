# Circuit library

`build_tracks.py` turns the circuit outlines in `sources/` into the maps used everywhere:

| Output | Used by |
|---|---|
| `HARDWARE/esp32/main_updated_corrected__1_/track_maps.h` | the VCU: 512 samples per lap (steering, speed fraction, straight/braking/sector/apex flags, modelled lateral g), 24 circuits, ~50 KB of flash |
| `SOFTWARE/tracks/tracks.json` | backend: AI engineer, digital twin (includes the VCU table, so tests can compare) |
| `SOFTWARE/dashboard/public/tracks.json` | dashboard: map outline, zones, corners, speed trace |

`HARDWARE/tests/test_firmware_host.py::test_track_maps_header_matches_tracks_json` compiles the real firmware header and checks it samples identically to the JSON.

**Circuits:**
- The 23 rounds of the 2026 calendar, as listed on formula1.com in September 2026.
- **Jeddah** is kept as an extra: it was on the calendar as first published.
- The default is Monza (id 12).

**Source:** [bacinger/f1-circuits](https://github.com/bacinger/f1-circuits), MIT licence (`sources/LICENSE-f1-circuits.md`). Each outline is scaled to its official lap length.

**Processing:**
1. A periodic smoothing spline through the GeoJSON vertices.
2. Curvature.
3. Point-mass lap simulation with an illustrative 2026-style car:
   - pass 1 all corner mode;
   - straight-mode zones = full-throttle runs ≥ 280 m that are gentler than R 450 m;
   - pass 2 with straight mode, closing it wherever the car brakes.
4. Steering = atan(3.6 m × curvature), clamped to the servo's ±30°.

**Modelled, not official:**
- speeds, lap times and g;
- straight-mode zones (the FIA's published zones are not used);
- sectors (equal thirds);
- corner numbers (detected curvature peaks, not official turn numbers).

**To regenerate** after editing the script (needs numpy and scipy): `python SOFTWARE/tracks/build_tracks.py`
