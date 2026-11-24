# Development Status — Stage 2

Stage-1 record (an earlier `Firmware/`/`RaspberryPi/` integration pass, kept for traceability): `Stage1_Archive/SoftwareDevelopmentStatus.md`.

## Where the project stands

| Area | State |
|---|---|
| VCU firmware | Complete and compiling; **untested on hardware** |
| Vehicle gateway | Complete, including real CAN and radio backends; tested against simulated buses |
| Ground station (backend, dashboard, bridge, driver client, radio firmware) | Complete for the current scope |
| Protocols | CAN v2 and LoRa v2, byte-identical across three codebases, enforced by tests |
| Documentation | Rewritten for the final BOM in Stage 2 |
| Hardware | Not yet wired; no measurements taken; every `[PLACEHOLDER]` still a guess |
| Machine learning / vision | Not attempted; the "AI" is a deterministic rule set at the ground station |

## Test and build results (end of Stage 2)

| Check | Result |
|---|---|
| `HARDWARE/tests` | 48 passed (Stage 2b) |
| `SOFTWARE/backend/tests` | 31 passed |
| Dashboard `tsc --noEmit` | Clean |
| Dashboard `eslint .` | 0 errors, 3 pre-existing warnings |
| VCU firmware (production build) | 0 warnings, 26 % flash, 4 % RAM |
| VCU firmware (bench build) | Compiles |
| Ground radio bridge firmware | Compiles, 0 sketch warnings |
| Physical hardware | Nothing tested |

## What changed in Stage 2

Summarised in `ChangeLog_Stage2.md`; reasoning and the gap list in `ProjectAudit_Stage2.md`. The headline items: the VCU firmware existed only as an empty file and was written; the CAN and LoRa protocols had range bugs that clipped real aero angles and currents; the gateway loop would have tripped the VCU's own communication timeouts; and there was no way to drive the car at all.

## Next milestones

1. Buy the missing parts: second SX1278 + spare ESP32, 2 mΩ shunt, level shifter, second buck converter.
2. Bench bring-up in the order given in `BringUpGuide.md`, recording every result.
3. Calibration (`CalibrationPlan.md`).
4. Failure drills (`SafetyArchitecture.md` §5) — these are the safety evidence for the report.
5. First logged runs; analyse the Pi CSV logs.
6. Optional extensions, in rough order of value: aero gains tuned from real data, a vision pipeline at the ground station, a trained model replacing the rule set (behind the same VCU validation), on-vehicle digital-twin comparison.
