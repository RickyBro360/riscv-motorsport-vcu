# Derived Metrics

Stage 2.1 — new document. Everything here is computed from sensors the bill of materials already contains: four Hall wheel-speed sensors, the MPU6050, the INA260, the ADS1115, the servo potentiometer feedback and the flap commands. **No hardware was added.**

Three places compute derived values, for three different reasons:

| Where | What | Why there |
|---|---|---|
| **VCU** (`vcu_logic.h`) | Wheel slip, seconds-to-temperature-limit | A control loop could act on them; they must be available without a laptop |
| **Gateway** (`raspberry_pi/main.py`, `DerivedEstimator`) | Distance, wheel rpm, power, energy, Wh/km, coulomb SoC, pack resistance | Needs every sample at full rate; feeds the dashboard and the CSV log |
| **Ground station** (`SOFTWARE/analysis/`, `backend/app/services/energy.py`) | Everything that needs the whole run: understeer gradient, friction circle, drag, laps, thermal fit, twin error, residuals | Offline, iterable, no timing constraints, no risk to the car |

> **Rule followed throughout:** if the data cannot support a number, the code returns `None` / "insufficient data" instead of a plausible-looking value. Several of these metrics are estimates with real error sources; each one below states them.

---

## 1. On the VCU (real time, CAN 0x141 at 10 Hz)

### Wheel slip ratio — `slipRatioPermille()`
```
slip = (driven_axle_mean - reference_axle_mean) / max(reference, 3 km/h)
```
Rear-wheel drive assumed (`CFG_DRIVEN_AXLE_REAR`). Positive = wheelspin, negative = lock-up, reported in per-mille and clamped to ±200 %. Below 3 km/h it returns 0 because the division is meaningless there.
*Error sources:* wheel-diameter error cancels (it appears in both terms) but a wrong magnet count on one wheel does not — check `sensor_residuals()` before trusting it. Cornering makes the inner/outer wheels differ legitimately; this is a longitudinal metric only.

### Seconds to temperature limit — `ThermalEstimator`
Samples each DS18B20 once per second, smooths d*T*/d*t* (α = 0.10) and extrapolates linearly to the configured limit (`CFG_MOTOR_OVERTEMP_C`, `CFG_BATTERY_OVERTEMP_C`). Returns `0xFFFF` = "not trending toward the limit", or 0 if already past it.
*Error sources:* a linear extrapolation over-estimates the danger early in a heating curve (the real curve flattens) — it is a warning aid, not a prediction of steady state. The offline `thermal_model()` does the proper first-order fit.

Both are transmitted in **CAN 0x141 VEHICLE_DERIVED** (`slip i16` per-mille, `t_motor u16` s, `t_batt u16` s) and decoded by the gateway.

## 2. On the gateway (every telemetry sample, 20 Hz)

| Metric | Method | Notes and error sources |
|---|---|---|
| `distance_m` | ∫ v d*t* | Wheel-speed based: wheelspin inflates it, lock-up shrinks it |
| `wheel_rpm`, `wheel_angle_rad` | from speed and wheel circumference | These fill the dashboard's `rpm`, `distance` and `wheelAngle`, which were hard-coded 0 before Stage 2.1. `rpm` is **wheel** rpm — there is no motor rpm sensor |
| `power_w` | pack V × I | Sign follows current: negative during regenerative braking |
| `energy_wh` | ∫ P d*t* / 3600 | Per run; the CSV log and the run summary both report it |
| `wh_per_km` | energy ÷ distance | Only once past 1 m of travel |
| `soc_coulomb_pct` | coulomb counting, seeded from open-circuit voltage, corrected at ≤ 1 %/s while resting (\|I\| < 0.5 A and stationary for > 2 s) | Depends on `--pack-capacity-ah`; drifts if the current sensor has an offset. Far better than the voltage-only estimate under load, which sags with current |
| `pack_resistance_ohm` | R = −ΔV/ΔI across load steps > 3 A, EMA of plausible values (5 mΩ–0.5 Ω) | Includes wiring and connector resistance, not just the cells. Rising values across sessions indicate pack ageing |

All appear as CSV columns and in the WebSocket frame under `derived` (`TelemetryProtocol.md` §4).

## 3. Offline analysis (`SOFTWARE/analysis/`)

```bash
cd SOFTWARE/analysis
python analyze.py ../../HARDWARE/raspberry_pi/logs/vehicle_*.csv
python analyze.py baseline.csv --compare ai_assist.csv        # the active-aero result
python analyze.py run.csv --plots plots/ --json results.json  # plots need matplotlib
```
Standard library only (matplotlib optional, for figures). 25 tests check the maths against synthetic logs with analytically known answers.

### 3.1 Energy — `energy_summary`, `lap_energy`, `range_estimate`, `pack_resistance`
Integrated energy, consumption, peak and mean power, regenerated energy, per-lap energy, and range from measured consumption rather than a guess.
*Replaces:* the backend's old `energyPerLap = 35 + 0.15 × speed` and `predictedRangeLaps = battery% / 4`, which were invented. The backend now reports measured values (`/api/ai/energy`) or `null` with `energyMeasured: false`.

### 3.2 Friction circle — `friction_circle`
Scatter of (lateral g, longitudinal g), its 99th-percentile envelope radius, and the share of samples within 80 % of that envelope — how much of the available grip the driver actually used.
*Error sources:* body roll tilts the IMU, so a fraction of gravity leaks into lateral g. Comparisons between runs are sound; absolute values are optimistic by a few percent.

### 3.3 Understeer gradient — `understeer_gradient`
Regresses (steering angle − kinematic steering angle implied by the measured yaw rate) against lateral acceleration:
```
δ_kinematic = yaw_rate × wheelbase / v     K = d(δ − δ_kinematic)/d(a_lat)   [deg/g]
```
Positive K = understeer, negative = oversteer, with R² so you can see whether the fit means anything.
*Error sources:* the logged steering angle is the servo angle — divide by the steering linkage ratio if it is not 1:1, or the magnitude will be wrong (the sign and the trend still hold).

### 3.4 Slip, sideslip, fused speed — `slip_ratios`, `sideslip_estimate`, `fused_speed`
Offline slip mirrors the firmware so the live and offline values can be compared. Sideslip integrates `β̇ = a_lat/v − r` with a 2 s washout; it is **indicative only** — without GNSS or an optical sensor nothing corrects the drift. Fused speed is a complementary filter of wheel speed and ∫a_x, and reports its disagreement with the raw wheel speed — large disagreement marks the moments when the wheels were lying.

### 3.5 Aerodynamics — `coastdown_drag`, `lateral_g_vs_speed`, `compare_aero_modes`
Coast-down fits `a(v) = A + B·v²` over throttle-off, brake-off segments, giving rolling losses and `CdA = 2·B·m/ρ`. Repeat at several fixed flap angles to get a **measured** drag-versus-angle curve to set beside the CFD.
`lateral_g_vs_speed` bins peak and 95th-percentile lateral g by speed; `compare_aero_modes` puts two runs side by side and reports the percentage change per speed bin. **This pair is the headline active-aero result** — downforce grows with v², so if the flaps work the grip curve should rise with speed and rise further in AI-assist mode.
*Experiment design, or the numbers mean nothing:* same track, same tyres, similar pack state, alternating baseline/assisted laps, at least three of each. Otherwise you are measuring tyre temperature and driver learning. Set the mode with `main.py --aero-mode baseline|ai`; the VCU's reported mode is logged in the `aero_mode` column, so runs can be split after the fact.

### 3.6 Laps and position — `detect_laps`, `dead_reckon`
Laps come from the autocorrelation of the yaw-rate trace: a circuit repeats its yaw signature every lap. The first correlation peak above the threshold is one lap (an earlier version locked onto two laps — see the regression test). Reports the period, best and mean lap, and per-lap energy.
*Limitation:* it finds the lap **length**, not a physical start/finish line, and returns nothing for free driving, which is the correct answer.
Dead reckoning integrates speed and yaw rate into a path and reports **loop-closure error** as a percentage of distance — quote that number with any map you show. Gyro bias makes the path rotate slowly; it is good for shape, not absolute position.

### 3.7 Health and models — `thermal_model`, `servo_response`, `sensor_residuals`, `digital_twin_error`
- **Thermal:** regresses d*T*/d*t* against *T* to recover the time constant τ and the steady-state temperature at the current duty, then extrapolates to the limit.
- **Servo response:** finds command steps and measures transport lag, achieved slew rate and steady-state error per flap. Feed the measured slew into `CFG_WING_RATE_MAX_DPS`; watch it across sessions for wear.
- **Sensor residuals:** cross-checks sensors against each other — does a_x match the derivative of wheel speed, do the four wheels agree in a straight line — and names the suspect wheel. Catches a loose magnet or a shifted IMU that no single-sensor threshold would.
- **Digital twin:** runs `m·dv/dt = F(throttle) − ½ρ·CdA·v² − C_r·m·g` alongside the recorded run and reports RMS speed error. The point is a twin that is **validated and states its own error**, rather than an animation. Feed `CdA` from the coast-down fit.

## 4. Suggested order for using this

1. First logged runs → `analyze.py` → check `sensorResiduals` before believing anything else.
2. Coast-downs at two flap angles → `coastdownDrag` → compare with CFD.
3. Circuit laps in baseline mode, then AI-assist, alternating → `--compare` → the active-aero result.
4. A sustained load run → `thermalMotor` → set realistic protection limits.
5. Flap step tests → `servoResponse` → set `CFG_WING_RATE_MAX_DPS`.
6. Feed the fitted CdA into the digital twin and quote its error.

## 5. What is still not measurable without new hardware

Absolute position (no GNSS), true vehicle speed independent of the wheels (no optical/radar ground sensor), individual wheel torque or motor rpm (no encoder or torque sensor), tyre temperature, ride height, and actual downforce (that needs load cells or strain gauges — the drag estimate above is inferred, not measured directly).
