"""
test_analysis.py — the analysis library checked against synthetic logs whose
answers are known analytically. These tests are what make the derived numbers
quotable in the report: each one states the physics it is verifying.

Run: cd SOFTWARE/analysis && python -m pytest -q tests
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metrics                      # noqa: E402
from log_loader import load         # noqa: E402
from tests import synthetic         # noqa: E402


# --------------------------------------------------------------- loading ---
def test_loader_reads_columns_and_time_base(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "a.csv", seconds=10.0, rate_hz=20.0))
    assert len(log) == 201
    assert log.duration_s == pytest.approx(10.0, abs=0.01)
    assert log.sample_rate_hz == pytest.approx(20.0, abs=0.1)
    assert log.t[0] == 0.0                      # time is relative to the run start
    assert log.col("speed_kmh")[0] == 36.0
    assert log.col("not_a_column")[0] != log.col("not_a_column")[0]   # NaN for missing


def test_moving_and_mode_filters(tmp_path):
    log = load(synthetic.circuit(tmp_path / "c.csv", laps=3, aero_mode=1))
    assert len(log.moving(min_speed_kmh=1.0)) == len(log)
    assert list(log.by_aero_mode().keys()) == [1]


# ---------------------------------------------------------------- energy ---
def test_energy_matches_hand_calculation(tmp_path):
    # 10 V x 2 A = 20 W for 20 s = 0.111 Wh; 36 km/h = 10 m/s for 20 s = 200 m
    log = load(synthetic.constant_drive(tmp_path / "e.csv", seconds=20.0, speed_kmh=36.0,
                                        voltage=10.0, current=2.0))
    e = metrics.energy_summary(log)
    assert e["energyWh"] == pytest.approx(20 * 20 / 3600.0, rel=0.02)
    assert e["distanceM"] == pytest.approx(200.0, rel=0.02)
    assert e["whPerKm"] == pytest.approx(e["energyWh"] / 0.2, abs=0.06)   # reported to 0.1 Wh/km
    assert e["peakPowerW"] == pytest.approx(20.0, rel=0.01)
    assert e["meanSpeedKmh"] == pytest.approx(36.0, rel=0.01)


def test_range_estimate_needs_movement(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "r.csv", seconds=2.0, speed_kmh=0.0))
    assert metrics.range_estimate(log)["rangeKm"] is None      # honest refusal, not a guess


def test_pack_resistance_recovered_from_load_steps(tmp_path):
    # V = 12 - 0.05 * I, stepping the current: R must come back as 0.05 ohm
    rows = []
    for k in range(200):
        t = k * 0.05
        current = 20.0 if (k // 10) % 2 else 1.0
        rows.append({"wall_time_s": t, "speed_kmh": 20.0, "battery_current_a": current,
                     "battery_voltage_v": 12.0 - 0.05 * current})
    log = load(synthetic.write_log(tmp_path / "pr.csv", rows))
    assert metrics.pack_resistance(log)["ohm"] == pytest.approx(0.05, rel=0.05)


# ----------------------------------------------------- vehicle dynamics ---
def test_friction_circle_envelope(tmp_path):
    log = load(synthetic.circuit(tmp_path / "f.csv", peak_lat_g=0.8))
    fc = metrics.friction_circle(log)
    assert fc["peakLatG"] == pytest.approx(0.8, abs=0.02)
    assert fc["envelopeG"] == pytest.approx(0.8, abs=0.05)
    assert 0 <= fc["timeNearLimitPct"] <= 100


def test_understeer_gradient_sign_and_magnitude(tmp_path):
    # Build a run needing an extra 4 deg of steering per g beyond the kinematic
    # angle: the fit must return about +4 deg/g and call it understeer.
    rows = []
    v = 15.0                                   # m/s
    wheelbase = 0.26
    for k in range(400):
        t = k * 0.05
        a_lat = 0.9 * math.sin(2 * math.pi * t / 8.0)
        yaw_rate = a_lat * 9.80665 / v
        delta_kin = math.degrees(yaw_rate * wheelbase / v)
        rows.append({"wall_time_s": t, "speed_kmh": v * 3.6, "accel_y_g": a_lat,
                     "gyro_z_dps": math.degrees(yaw_rate),
                     "steering_angle_deg": delta_kin + 4.0 * a_lat})
    log = load(synthetic.write_log(tmp_path / "u.csv", rows))
    us = metrics.understeer_gradient(log, wheelbase_m=wheelbase)
    assert us["gradientDegPerG"] == pytest.approx(4.0, rel=0.05)
    assert us["balance"] == "understeer"
    assert us["rSquared"] > 0.99


def test_understeer_reports_insufficient_data(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "u2.csv", seconds=2.0, speed_kmh=2.0))
    assert metrics.understeer_gradient(log)["gradientDegPerG"] is None


def test_slip_ratio_detects_wheelspin(tmp_path):
    rows = []
    for k in range(200):
        t = k * 0.05
        spinning = 60 <= k < 120
        rows.append({"wall_time_s": t, "speed_kmh": 20.0,
                     "wheel_speed_fl_kmh": 20.0, "wheel_speed_fr_kmh": 20.0,
                     "wheel_speed_rl_kmh": 26.0 if spinning else 20.0,
                     "wheel_speed_rr_kmh": 26.0 if spinning else 20.0})
    log = load(synthetic.write_log(tmp_path / "s.csv", rows))
    slip = metrics.slip_ratios(log)
    assert slip["wheelspinSamples"] == 60
    assert slip["lockupSamples"] == 0
    assert slip["p95Slip"] == pytest.approx(0.3, abs=0.02)      # 26/20 - 1


def test_fused_speed_rejects_a_wheelspin_spike(tmp_path):
    # Wheels briefly report 60 km/h while the IMU says the car is not accelerating.
    rows = []
    for k in range(200):
        t = k * 0.05
        spike = 100 <= k < 110
        rows.append({"wall_time_s": t, "speed_kmh": 60.0 if spike else 20.0,
                     "accel_x_g": 0.0})
    log = load(synthetic.write_log(tmp_path / "fs.csv", rows))
    fused = metrics.fused_speed(log, tau_s=0.5)
    peak_fused = max(fused["series"][100:110])
    assert peak_fused < 45.0                       # filter refuses to believe the jump
    assert fused["maxDisagreementKmh"] > 10.0      # and the disagreement is reported


def test_sideslip_is_bounded_and_flagged(tmp_path):
    log = load(synthetic.circuit(tmp_path / "b.csv"))
    ss = metrics.sideslip_estimate(log)
    assert ss["peakAbsDeg"] is not None and ss["peakAbsDeg"] < 90.0
    assert "indicative" in ss["caveat"]


# ---------------------------------------------------------- aerodynamics ---
def test_coastdown_recovers_the_drag_coefficients(tmp_path):
    # a = 0.3 + 0.004 v^2 with m = 2 kg, rho = 1.184 -> CdA = 2*0.004*2/1.184
    log = load(synthetic.coastdown(tmp_path / "cd.csv", rolling_a=0.3, b_term=0.004))
    drag = metrics.coastdown_drag(log, mass_kg=2.0)
    assert drag["rollingDecelMs2"] == pytest.approx(0.3, rel=0.1)
    assert drag["quadraticTermB"] == pytest.approx(0.004, rel=0.1)
    assert drag["cdA"] == pytest.approx(2 * 0.004 * 2.0 / 1.184, rel=0.1)
    assert drag["rSquared"] > 0.95


def test_coastdown_says_so_when_there_is_no_coasting(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "nc.csv"))
    drag = metrics.coastdown_drag(log)
    assert drag["cdA"] is None and "coast-down" in drag["note"]


def test_lateral_g_vs_speed_bins(tmp_path):
    log = load(synthetic.circuit(tmp_path / "lg.csv", speed_kmh=30.0, peak_lat_g=0.8))
    result = metrics.lateral_g_vs_speed(log, bin_width_kmh=5.0)
    assert len(result["bins"]) == 1
    assert result["bins"][0]["speedBinKmh"] == pytest.approx(32.5)
    assert result["bins"][0]["peakLatG"] == pytest.approx(0.8, abs=0.02)


def test_aero_mode_comparison_quantifies_the_gain(tmp_path):
    baseline = load(synthetic.circuit(tmp_path / "base.csv", peak_lat_g=0.80, aero_mode=0))
    assisted = load(synthetic.circuit(tmp_path / "ai.csv", peak_lat_g=0.88, aero_mode=1))
    cmp_result = metrics.compare_aero_modes(baseline, assisted)
    assert cmp_result["binsCompared"] == 1
    assert cmp_result["meanDeltaPct"] == pytest.approx(10.0, abs=1.5)   # 0.88/0.80 - 1


# ------------------------------------------------------ laps and position ---
def test_lap_detection_finds_the_period(tmp_path):
    log = load(synthetic.circuit(tmp_path / "laps.csv", laps=5, lap_time_s=10.0))
    laps = metrics.detect_laps(log, min_lap_s=5.0)
    assert laps["lapPeriodS"] == pytest.approx(10.0, abs=0.3)
    assert len(laps["laps"]) >= 4
    assert laps["correlation"] > 0.6


def test_lap_detection_refuses_a_straight_line(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "straight.csv", seconds=60.0))
    assert metrics.detect_laps(log)["laps"] == []


def test_lap_energy_splits_by_lap(tmp_path):
    log = load(synthetic.circuit(tmp_path / "le.csv", laps=4, lap_time_s=10.0))
    laps = metrics.detect_laps(log, min_lap_s=5.0)
    per_lap = metrics.lap_energy(log, laps["laps"])
    assert len(per_lap) == len(laps["laps"])
    assert all(lap["energyWh"] > 0 for lap in per_lap)
    # 11 V x 6 A = 66 W for 10 s = 0.183 Wh per lap
    assert per_lap[1]["energyWh"] == pytest.approx(66 * 10 / 3600.0, rel=0.1)


def test_dead_reckoning_closes_a_circle(tmp_path):
    # Constant speed and constant yaw rate for exactly one full turn.
    rate, speed = 20.0, 10.0                       # Hz, m/s
    turn_s = 20.0
    yaw_rate_dps = 360.0 / turn_s
    rows = [{"wall_time_s": k / rate, "speed_kmh": speed * 3.6, "gyro_z_dps": yaw_rate_dps}
            for k in range(int(turn_s * rate) + 1)]
    log = load(synthetic.write_log(tmp_path / "dr.csv", rows))
    dr = metrics.dead_reckon(log)
    assert dr["distanceM"] == pytest.approx(speed * turn_s, rel=0.02)
    assert dr["loopClosureErrorPct"] < 2.0         # a closed circle comes back to the start


# ------------------------------------------------------- health / models ---
def test_thermal_model_recovers_tau_and_steady_state(tmp_path):
    log = load(synthetic.heating(tmp_path / "th.csv", tau_s=120.0, t0=30.0, t_inf=95.0))
    th = metrics.thermal_model(log, limit_c=90.0)      # run ends around 80 C, limit still ahead
    assert th["tauS"] == pytest.approx(120.0, rel=0.15)
    assert th["steadyStateC"] == pytest.approx(95.0, rel=0.1)
    assert th["willReachLimit"] is True
    assert th["secondsToLimit"] is not None and th["secondsToLimit"] > 0
    # once the temperature is past the limit the answer is zero, not "unknown"
    assert metrics.thermal_model(log, limit_c=50.0)["secondsToLimit"] == 0


def test_thermal_model_reports_no_trend_when_cold(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "cold.csv", seconds=30.0))
    assert metrics.thermal_model(log)["tauS"] is None


def test_servo_response_measures_lag_and_slew(tmp_path):
    # Command steps 0 -> 20 deg; the servo follows at 100 deg/s after 100 ms.
    rate = 50.0
    rows = []
    for k in range(int(6 * rate)):
        t = k / rate
        target = 20.0 if t >= 1.0 else 0.0
        if t < 1.1:
            measured = 0.0
        else:
            measured = min(20.0, (t - 1.1) * 100.0)
        rows.append({"wall_time_s": t, "speed_kmh": 20.0,
                     "wing_target_lf_deg": target, "wing_lf_deg": measured})
    log = load(synthetic.write_log(tmp_path / "sv.csv", rows))
    sr = metrics.servo_response(log, "lf")
    assert sr["steps"] == 1
    assert sr["meanLagS"] == pytest.approx(0.1, abs=0.05)
    assert sr["meanSlewDegPerS"] == pytest.approx(100.0, rel=0.15)
    assert abs(sr["meanSteadyErrorDeg"]) < 1.0


def test_sensor_residuals_flag_a_bad_wheel(tmp_path):
    rows = []
    for k in range(400):
        t = k * 0.05
        rows.append({"wall_time_s": t, "speed_kmh": 20.0, "accel_x_g": 0.0, "accel_y_g": 0.0,
                     "wheel_speed_fl_kmh": 20.0, "wheel_speed_fr_kmh": 20.0,
                     "wheel_speed_rl_kmh": 20.0, "wheel_speed_rr_kmh": 14.0})   # one dud
    log = load(synthetic.write_log(tmp_path / "res.csv", rows))
    res = metrics.sensor_residuals(log)
    assert any("RR" in flag for flag in res["flags"])
    assert res["wheelBiasKmh"]["rr"] < -3.0


def test_sensor_residuals_quiet_on_healthy_data(tmp_path):
    log = load(synthetic.constant_drive(tmp_path / "ok.csv", seconds=20.0))
    assert metrics.sensor_residuals(log)["flags"] == []


def test_digital_twin_reports_its_own_error(tmp_path):
    log = load(synthetic.coastdown(tmp_path / "twin.csv", rolling_a=0.3, b_term=0.004))
    twin = metrics.digital_twin_error(log, mass_kg=2.0, cda=2 * 0.004 * 2.0 / 1.184,
                                      rolling_coeff=0.3 / 9.80665)
    assert twin["rmsErrorKmh"] is not None
    assert twin["rmsErrorKmh"] < 2.0          # the model matches the data it was fitted from
    assert twin["relativeErrorPct"] < 10.0
