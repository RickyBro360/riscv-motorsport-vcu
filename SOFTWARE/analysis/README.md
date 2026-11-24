# Analysis (ground station, offline)

Turns the Raspberry Pi's telemetry logs into the numbers that go in the report. Nothing here runs on the car; nothing here can move an actuator.

```bash
cd SOFTWARE/analysis
python analyze.py path/to/vehicle_20260918_101500.csv
python analyze.py baseline.csv --compare ai_assist.csv     # active-aero comparison
python analyze.py run.csv --json results.json --plots plots/
python -m pytest -q tests                                  # 25 tests against known physics
```

Standard library only. `matplotlib` is optional and only needed for `--plots`.

| File | Contents |
|---|---|
| `log_loader.py` | Reads the gateway's CSV; column access, time base, filters (`moving()`, `by_aero_mode()`) |
| `metrics.py` | Energy, friction circle, understeer gradient, slip, sideslip, fused speed, coast-down drag, lateral-g vs speed, aero-mode comparison, lap detection, dead reckoning, thermal fit, servo identification, sensor residuals, digital-twin error |
| `analyze.py` | CLI: printed summary, JSON export, optional figures |
| `tests/synthetic.py` | Builds logs with known physics (constant drive, coast-down, circuit, heating) |
| `tests/test_analysis.py` | Checks every metric against those known answers |

Definitions, assumptions and error sources for each metric: `SOFTWARE/Documentation/DerivedMetrics.md`.
