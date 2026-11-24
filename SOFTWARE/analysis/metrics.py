#!/usr/bin/env python3
"""
metrics.py — vehicle-dynamics, energy and health metrics derived from the
Raspberry Pi's telemetry logs. No extra sensors: everything here comes from
the wheel-speed Halls, the MPU6050, the INA226/ADS1115 and the servo feedback
already in the bill of materials.

Design rules
  * Standard library only (`math`, `statistics`) — runs anywhere, no install.
  * Every function returns plain dicts/lists so results can be JSON-dumped,
    plotted, or pasted into the report.
  * Every estimate states its assumptions in the docstring, and returns
    `None`/empty rather than guessing when the data cannot support it.

Definitions and error sources: SOFTWARE/Documentation/DerivedMetrics.md.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable

from log_loader import Log

G = 9.80665
AIR_DENSITY = 1.184          # kg/m^3 at 25 C, sea level [PLACEHOLDER: adjust for altitude]


# ---------------------------------------------------------------- helpers ---
def _finite(values: Iterable[float]) -> list[float]:
    return [v for v in values if isinstance(v, float) and math.isfinite(v)]


def _mean(values: Iterable[float], default: float = 0.0) -> float:
    vals = _finite(values)
    return statistics.fmean(vals) if vals else default


def _percentile(values: Iterable[float], p: float) -> float:
    vals = sorted(_finite(values))
    if not vals:
        return math.nan
    k = (len(vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return vals[int(k)] if lo == hi else vals[lo] * (hi - k) + vals[hi] * (k - lo)


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least squares y = a + b x. Returns (a, b, r_squared); b is NaN if the
    data cannot support a fit (fewer than 3 points or no spread in x)."""
    pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    n = len(pairs)
    if n < 3:
        return math.nan, math.nan, math.nan
    mx = statistics.fmean(x for x, _ in pairs)
    my = statistics.fmean(y for _, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    if sxx <= 1e-12:
        return math.nan, math.nan, math.nan
    b = sum((x - mx) * (y - my) for x, y in pairs) / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for _, y in pairs)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in pairs)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else math.nan
    return a, b, r2


def _derivative(values: list[float], t: list[float]) -> list[float]:
    """Central difference; endpoints one-sided. NaN where dt is 0."""
    n = len(values)
    out = [math.nan] * n
    for i in range(n):
        lo, hi = max(0, i - 1), min(n - 1, i + 1)
        dt = t[hi] - t[lo]
        if dt > 0 and math.isfinite(values[hi]) and math.isfinite(values[lo]):
            out[i] = (values[hi] - values[lo]) / dt
    return out


def _low_pass(values: list[float], t: list[float], tau_s: float) -> list[float]:
    out: list[float] = []
    y = math.nan
    for i, v in enumerate(values):
        if not math.isfinite(v):
            out.append(y)
            continue
        if not math.isfinite(y):
            y = v
        else:
            dt = t[i] - t[i - 1] if i else 0.0
            alpha = dt / (tau_s + dt) if (tau_s + dt) > 0 else 1.0
            y += alpha * (v - y)
        out.append(y)
    return out


# ------------------------------------------------------------ 1. ENERGY -----
def energy_summary(log: Log) -> dict:
    """Real electrical energy, integrated from pack voltage x current.

    This replaces the backend's placeholder formula (energyPerLap = 35 +
    0.15 x speed), which was not derived from anything measured."""
    t, v, i = log.t, log.col("battery_voltage_v"), log.col("battery_current_a")
    speed = log.col("speed_kmh")
    energy_wh = 0.0
    charge_ah = 0.0
    distance_m = 0.0
    peak_w = 0.0
    regen_wh = 0.0
    for k in range(1, len(t)):
        dt = t[k] - t[k - 1]
        if not (0 < dt < 1.0):
            continue
        p = v[k] * i[k]
        if math.isfinite(p):
            energy_wh += p * dt / 3600.0
            if p < 0:
                regen_wh += -p * dt / 3600.0
            peak_w = max(peak_w, p)
        if math.isfinite(i[k]):
            charge_ah += i[k] * dt / 3600.0
        if math.isfinite(speed[k]):
            distance_m += speed[k] / 3.6 * dt
    return {
        "durationS": round(log.duration_s, 1),
        "distanceM": round(distance_m, 1),
        "energyWh": round(energy_wh, 3),
        "chargeAh": round(charge_ah, 4),
        "whPerKm": round(energy_wh / (distance_m / 1000.0), 1) if distance_m > 1 else None,
        "meanPowerW": round(energy_wh * 3600.0 / log.duration_s, 1) if log.duration_s > 0 else None,
        "peakPowerW": round(peak_w, 1),
        "regenWh": round(regen_wh, 3),
        "meanSpeedKmh": round(_mean(speed), 1),
    }


def lap_energy(log: Log, laps: list[dict]) -> list[dict]:
    """Energy per detected lap — the honest version of `energyPerLap`."""
    out = []
    t = log.t
    v, i, speed = log.col("battery_voltage_v"), log.col("battery_current_a"), log.col("speed_kmh")
    for lap in laps:
        wh = 0.0
        metres = 0.0
        for k in range(1, len(t)):
            if not (lap["startS"] <= t[k] <= lap["endS"]):
                continue
            dt = t[k] - t[k - 1]
            if not (0 < dt < 1.0):
                continue
            if math.isfinite(v[k]) and math.isfinite(i[k]):
                wh += v[k] * i[k] * dt / 3600.0
            if math.isfinite(speed[k]):
                metres += speed[k] / 3.6 * dt
        out.append({"lap": lap["lap"], "lapTimeS": lap["lapTimeS"],
                    "energyWh": round(wh, 3), "distanceM": round(metres, 1)})
    return out


def range_estimate(log: Log, capacity_ah: float = 2.2, reserve_pct: float = 15.0) -> dict:
    """Remaining range from measured consumption, not from a guess.

    Uses the logged coulomb-counted state of charge if present, otherwise the
    charge integrated here. Returns None fields when the run is too short or
    too stationary to mean anything."""
    energy = energy_summary(log)
    soc = _finite(log.col("soc_coulomb_pct"))
    soc_now = soc[-1] if soc else None
    wh_per_km = energy["whPerKm"]
    if wh_per_km is None or wh_per_km <= 0 or soc_now is None:
        return {"socPct": soc_now, "whPerKm": wh_per_km, "rangeKm": None, "note":
                "insufficient movement to estimate range"}
    usable_pct = max(0.0, soc_now - reserve_pct)
    nominal_v = 3.7 * 3
    usable_wh = capacity_ah * nominal_v * usable_pct / 100.0
    return {
        "socPct": round(soc_now, 1),
        "whPerKm": wh_per_km,
        "usableWh": round(usable_wh, 2),
        "rangeKm": round(usable_wh / wh_per_km, 2),
        "reservePct": reserve_pct,
    }


def pack_resistance(log: Log, min_step_a: float = 3.0) -> dict:
    """Pack + wiring internal resistance from load steps: R = -dV/dI.

    Every sample pair with a current step larger than `min_step_a` gives one
    estimate; the median is reported. A rising value across sessions is a
    pack-health indicator."""
    v, i = log.col("battery_voltage_v"), log.col("battery_current_a")
    estimates = []
    for k in range(1, len(v)):
        dv, di = v[k] - v[k - 1], i[k] - i[k - 1]
        if not (math.isfinite(dv) and math.isfinite(di)) or abs(di) < min_step_a:
            continue
        r = -dv / di
        if 0.005 < r < 0.5:
            estimates.append(r)
    if not estimates:
        return {"ohm": None, "samples": 0, "note": "no load steps large enough"}
    return {"ohm": round(statistics.median(estimates), 4), "samples": len(estimates),
            "spreadOhm": round(_percentile(estimates, 0.9) - _percentile(estimates, 0.1), 4)}


# -------------------------------------------------- 2. VEHICLE DYNAMICS -----
def friction_circle(log: Log, limit_fraction: float = 0.8) -> dict:
    """Combined longitudinal/lateral acceleration envelope.

    The scatter of (a_lat, a_long) is the classic friction circle; how much of
    it the driver uses is a setup and driving metric. Radius is in g."""
    ax, ay = log.col("accel_x_g"), log.col("accel_y_g")
    points = [(y, x) for x, y in zip(ax, ay) if math.isfinite(x) and math.isfinite(y)]
    if not points:
        return {"samples": 0}
    radii = [math.hypot(lat, lon) for lat, lon in points]
    envelope = _percentile(radii, 0.99)
    near_limit = sum(1 for r in radii if r >= limit_fraction * envelope)
    return {
        "samples": len(points),
        "peakLatG": round(max(abs(lat) for lat, _ in points), 3),
        "peakLongAccelG": round(max(lon for _, lon in points), 3),
        "peakLongBrakeG": round(min(lon for _, lon in points), 3),
        "envelopeG": round(envelope, 3),
        "timeNearLimitPct": round(100.0 * near_limit / len(points), 1),
        "points": [(round(lat, 3), round(lon, 3)) for lat, lon in points],
    }


def understeer_gradient(log: Log, wheelbase_m: float = 0.26,
                        min_speed_kmh: float = 8.0) -> dict:
    """Understeer gradient K from the difference between the yaw rate the
    steering asks for and the yaw rate the car actually achieves.

        kinematic yaw rate  r_ref = v * delta / L        (rad/s)
        residual            dr    = r_ref - r_measured
        K [deg/g]           slope of (delta_required - delta_kinematic) vs a_lat

    Positive K = understeer (more steering needed as lateral load rises),
    negative = oversteer. Assumes the steering column angle in the log is the
    road-wheel angle — check `CFG_STEERING_MAX_DEG` and the linkage ratio
    before quoting the number."""
    moving = log.where(lambda lg, i: lg.col("speed_kmh")[i] >= min_speed_kmh)
    if len(moving) < 20:
        return {"samples": len(moving), "gradientDegPerG": None,
                "note": "not enough time above the minimum speed"}
    xs, ys = [], []
    for i in range(len(moving)):
        v = moving.col("speed_kmh")[i] / 3.6
        delta = math.radians(moving.col("steering_angle_deg")[i])
        r_meas = math.radians(moving.col("gyro_z_dps")[i])
        a_lat = moving.col("accel_y_g")[i]
        if not all(math.isfinite(x) for x in (v, delta, r_meas, a_lat)) or v < 1e-3:
            continue
        if abs(a_lat) < 0.05:          # straight-line samples carry no information
            continue
        delta_kinematic = r_meas * wheelbase_m / v     # steering the yaw rate implies
        xs.append(a_lat)
        ys.append(math.degrees(delta - delta_kinematic))
    intercept, slope, r2 = _linear_fit(xs, ys)
    if not math.isfinite(slope):
        return {"samples": len(xs), "gradientDegPerG": None, "note": "fit not supported by the data"}
    balance = "understeer" if slope > 0.2 else "oversteer" if slope < -0.2 else "neutral"
    return {
        "samples": len(xs),
        "gradientDegPerG": round(slope, 3),
        "interceptDeg": round(intercept, 3),
        "rSquared": round(r2, 3) if math.isfinite(r2) else None,
        "balance": balance,
        "wheelbaseM": wheelbase_m,
    }


def slip_ratios(log: Log, driven_rear: bool = True, min_speed_kmh: float = 3.0) -> dict:
    """Longitudinal slip per sample from the driven vs reference axle, plus
    per-wheel disagreement. Mirrors the firmware's `slipRatioPermille()` so the
    live value and the offline value can be compared."""
    fl, fr = log.col("wheel_speed_fl_kmh"), log.col("wheel_speed_fr_kmh")
    rl, rr = log.col("wheel_speed_rl_kmh"), log.col("wheel_speed_rr_kmh")
    series, spins, locks = [], 0, 0
    for i in range(len(log)):
        driven = (rl[i] + rr[i]) / 2 if driven_rear else (fl[i] + fr[i]) / 2
        ref = (fl[i] + fr[i]) / 2 if driven_rear else (rl[i] + rr[i]) / 2
        if not (math.isfinite(driven) and math.isfinite(ref)) or max(driven, ref) < min_speed_kmh:
            series.append(math.nan)
            continue
        s = (driven - ref) / max(ref, min_speed_kmh)
        series.append(s)
        if s > 0.15:
            spins += 1
        elif s < -0.15:
            locks += 1
    valid = _finite(series)
    return {
        "samples": len(valid),
        "meanSlip": round(_mean(valid), 4) if valid else None,
        "p95Slip": round(_percentile([abs(v) for v in valid], 0.95), 4) if valid else None,
        "wheelspinSamples": spins,
        "lockupSamples": locks,
        "series": series,
    }


def sideslip_estimate(log: Log, washout_tau_s: float = 2.0, min_speed_kmh: float = 8.0) -> dict:
    """Vehicle sideslip angle beta by integrating

        beta_dot = a_lat / v - yaw_rate

    with a washout (high-pass) so the inevitable bias does not run away. This
    is an indicative signal, NOT a calibrated state estimate: without a
    two-antenna GNSS or an optical sensor there is nothing to correct the
    drift. Treat it as 'is the car rotating more than the speed and steering
    suggest', and only within a few seconds of a transient."""
    t = log.t
    beta = 0.0
    series = []
    for i in range(len(log)):
        v = log.col("speed_kmh")[i] / 3.6
        a_lat = log.col("accel_y_g")[i] * G
        r = math.radians(log.col("gyro_z_dps")[i])
        dt = t[i] - t[i - 1] if i else 0.0
        if v < min_speed_kmh / 3.6 or not all(math.isfinite(x) for x in (v, a_lat, r)):
            beta *= 0.9
            series.append(math.nan)
            continue
        beta += (a_lat / v - r) * dt
        beta -= beta * (dt / washout_tau_s)          # washout
        series.append(math.degrees(beta))
    valid = _finite(series)
    return {
        "samples": len(valid),
        "peakAbsDeg": round(max((abs(v) for v in valid), default=math.nan), 2) if valid else None,
        "p95AbsDeg": round(_percentile([abs(v) for v in valid], 0.95), 2) if valid else None,
        "washoutTauS": washout_tau_s,
        "series": series,
        "caveat": "washout-filtered integration; indicative only, drifts without an absolute reference",
    }


def fused_speed(log: Log, tau_s: float = 0.5) -> dict:
    """Complementary filter blending wheel speed (right on average, wrong
    during wheelspin or lock-up) with the integral of longitudinal
    acceleration (right during transients, drifts over time).

        v_fused = (1-a) * (v_prev + a_x * dt) + a * v_wheel

    Reported alongside the raw wheel speed so the disagreement is visible —
    large disagreement is exactly when the wheels are not telling the truth."""
    t = log.t
    v_wheel = [s / 3.6 for s in log.col("speed_kmh")]
    ax = [a * G for a in log.col("accel_x_g")]
    fused, v = [], math.nan
    for i in range(len(log)):
        dt = t[i] - t[i - 1] if i else 0.0
        if not math.isfinite(v):
            v = v_wheel[i] if math.isfinite(v_wheel[i]) else 0.0
        else:
            if math.isfinite(ax[i]):
                v += ax[i] * dt
            if math.isfinite(v_wheel[i]):
                alpha = dt / (tau_s + dt) if (tau_s + dt) > 0 else 1.0
                v += alpha * (v_wheel[i] - v)
            v = max(0.0, v)
        fused.append(v)
    diffs = [abs(f - w) for f, w in zip(fused, v_wheel) if math.isfinite(w)]
    return {
        "tauS": tau_s,
        "series": [round(v * 3.6, 3) for v in fused],
        "maxDisagreementKmh": round(max(diffs, default=0.0) * 3.6, 2),
        "meanDisagreementKmh": round(_mean(diffs) * 3.6, 3),
    }


# ------------------------------------------------------ 3. AERODYNAMICS -----
def coastdown_segments(log: Log, min_speed_kmh: float = 10.0, min_duration_s: float = 1.0) -> list[dict]:
    """Find stretches where the car is coasting: no throttle, no brake, speed
    falling. These are the only clean windows for a drag estimate."""
    t, speed = log.t, log.col("speed_kmh")
    throttle, brake = log.col("throttle_pct"), log.col("brake_pct")
    segments, start = [], None
    for i in range(1, len(log)):
        coasting = (throttle[i] < 1.0 and brake[i] < 1.0 and speed[i] >= min_speed_kmh
                    and speed[i] <= speed[i - 1] + 0.05)
        if coasting and start is None:
            start = i
        elif not coasting and start is not None:
            if t[i - 1] - t[start] >= min_duration_s:
                segments.append({"startIndex": start, "endIndex": i - 1,
                                 "startS": round(t[start], 2), "endS": round(t[i - 1], 2),
                                 "fromKmh": round(speed[start], 1), "toKmh": round(speed[i - 1], 1)})
            start = None
    if start is not None and t[-1] - t[start] >= min_duration_s:
        segments.append({"startIndex": start, "endIndex": len(log) - 1,
                         "startS": round(t[start], 2), "endS": round(t[-1], 2),
                         "fromKmh": round(speed[start], 1), "toKmh": round(speed[-1], 1)})
    return segments


def coastdown_drag(log: Log, mass_kg: float = 2.0, frontal_area_m2: float = 0.018,
                   air_density: float = AIR_DENSITY) -> dict:
    """Drag and rolling resistance from coast-down data.

        deceleration a(v) = A + B * v^2
        A = rolling/driveline losses [m/s^2]
        B = 0.5 * rho * CdA / m      ->  CdA = 2 * B * m / rho

    Run several coast-downs at a fixed flap angle, then repeat at another
    angle: the change in CdA is a *measured* aerodynamic result to put beside
    the CFD, which is exactly what an active-aero project needs to show.
    `frontal_area_m2` is only used to report a Cd; CdA is the primary result."""
    segments = coastdown_segments(log)
    t, speed = log.t, log.col("speed_kmh")
    xs, ys, used = [], [], 0
    for seg in segments:
        lo, hi = seg["startIndex"], seg["endIndex"]
        v_ms = [speed[i] / 3.6 for i in range(lo, hi + 1)]
        ts = [t[i] for i in range(lo, hi + 1)]
        decel = [-d for d in _derivative(v_ms, ts)]
        for v, a in zip(v_ms, decel):
            if math.isfinite(v) and math.isfinite(a) and a > 0:
                xs.append(v * v)
                ys.append(a)
        used += 1
    a0, b, r2 = _linear_fit(xs, ys)
    if not math.isfinite(b) or b <= 0:
        return {"segments": len(segments), "usableSamples": len(xs), "cdA": None,
                "note": "no usable coast-down data — need throttle-off, brake-off decelerations"}
    cda = 2.0 * b * mass_kg / air_density
    return {
        "segments": len(segments),
        "segmentsUsed": used,
        "usableSamples": len(xs),
        "rollingDecelMs2": round(a0, 4),
        "quadraticTermB": round(b, 6),
        "cdA": round(cda, 5),
        "cd": round(cda / frontal_area_m2, 3) if frontal_area_m2 > 0 else None,
        "rSquared": round(r2, 3) if math.isfinite(r2) else None,
        "massKg": mass_kg,
        "caveat": "assumes constant mass, flat ground and no wind; repeat runs in both directions",
    }


def lateral_g_vs_speed(log: Log, bin_width_kmh: float = 5.0, min_samples: int = 5) -> dict:
    """Peak sustained lateral acceleration in each speed bin.

    Aerodynamic downforce grows with v^2, so if the active aero is doing
    anything the peak lateral g should climb with speed — and climb further in
    AI-assist mode than in baseline. This is the headline active-aero result;
    compare two runs with `compare_aero_modes()`."""
    speed, ay = log.col("speed_kmh"), log.col("accel_y_g")
    bins: dict[int, list[float]] = {}
    for v, a in zip(speed, ay):
        if not (math.isfinite(v) and math.isfinite(a)) or v < bin_width_kmh:
            continue
        bins.setdefault(int(v // bin_width_kmh), []).append(abs(a))
    out = []
    for key in sorted(bins):
        values = bins[key]
        if len(values) < min_samples:
            continue
        out.append({
            "speedBinKmh": round((key + 0.5) * bin_width_kmh, 1),
            "samples": len(values),
            "peakLatG": round(max(values), 3),
            "p95LatG": round(_percentile(values, 0.95), 3),
            "meanLatG": round(_mean(values), 3),
        })
    return {"binWidthKmh": bin_width_kmh, "bins": out}


def compare_aero_modes(baseline: Log, assisted: Log, bin_width_kmh: float = 5.0) -> dict:
    """Side-by-side peak lateral g per speed bin for two runs (or two segments
    of one run). The 'deltaPct' column is the active-aero claim, quantified.

    Honest experiment design: same track, same tyres, same battery state,
    alternating laps, at least three of each — otherwise you are measuring
    tyre temperature and driver learning, not aerodynamics."""
    a = {b["speedBinKmh"]: b for b in lateral_g_vs_speed(baseline, bin_width_kmh)["bins"]}
    b = {x["speedBinKmh"]: x for x in lateral_g_vs_speed(assisted, bin_width_kmh)["bins"]}
    rows = []
    for speed_bin in sorted(set(a) & set(b)):
        base, assist = a[speed_bin]["p95LatG"], b[speed_bin]["p95LatG"]
        rows.append({
            "speedBinKmh": speed_bin,
            "baselineP95LatG": base,
            "assistedP95LatG": assist,
            "deltaG": round(assist - base, 3),
            "deltaPct": round((assist - base) / base * 100.0, 1) if base > 0 else None,
            "samples": min(a[speed_bin]["samples"], b[speed_bin]["samples"]),
        })
    gains = [r["deltaPct"] for r in rows if r["deltaPct"] is not None]
    return {
        "bins": rows,
        "meanDeltaPct": round(_mean(gains), 1) if gains else None,
        "binsCompared": len(rows),
        "caveat": "not a controlled experiment unless the runs were alternated on the same day",
    }


# ------------------------------------------------- 4. LAPS AND POSITION -----
def detect_laps(log: Log, min_lap_s: float = 8.0, similarity: float = 0.6) -> dict:
    """Lap boundaries without GPS, by correlating the yaw-rate trace against
    itself: a closed circuit repeats its yaw signature every lap.

    Method: resample yaw rate onto a fixed grid, autocorrelate, take the
    strongest peak beyond `min_lap_s` as the lap period, then split on the
    best-matching phase. Works on a repeatable circuit; returns nothing useful
    for free driving, which is the correct answer in that case."""
    t = log.t
    yaw = log.col("gyro_z_dps")
    pairs = [(tt, y) for tt, y in zip(t, yaw) if math.isfinite(y)]
    if len(pairs) < 100 or log.duration_s < 3 * min_lap_s:
        return {"laps": [], "lapPeriodS": None, "note": "run too short for lap detection"}
    dt = log.duration_s / (len(pairs) - 1)
    values = [y for _, y in pairs]
    mean = statistics.fmean(values)
    centred = [v - mean for v in values]
    energy0 = sum(v * v for v in centred)
    if energy0 <= 1e-9:
        return {"laps": [], "lapPeriodS": None, "note": "no yaw signature (straight-line run?)"}
    min_lag = int(min_lap_s / dt)
    max_lag = len(centred) // 2
    # Normalised over the overlapping window only (a Pearson-style coefficient).
    # Using an un-normalised sum here made two laps score as highly as one, so
    # the detector reported double the real lap time — see test_lap_detection.
    scores: list[tuple[int, float]] = []
    for lag in range(min_lag, max_lag):
        overlap = len(centred) - lag
        num = sum(centred[i] * centred[i + lag] for i in range(overlap))
        e_a = sum(centred[i] * centred[i] for i in range(overlap))
        e_b = sum(centred[i + lag] * centred[i + lag] for i in range(overlap))
        denom = math.sqrt(e_a * e_b)
        scores.append((lag, num / denom if denom > 1e-12 else 0.0))
    # The FIRST lag that is both a local maximum and above the threshold is one
    # lap; later peaks are multiples of it.
    best_lag, best_score = None, 0.0
    for idx in range(1, len(scores) - 1):
        lag, score = scores[idx]
        if score >= similarity and score >= scores[idx - 1][1] and score >= scores[idx + 1][1]:
            best_lag, best_score = lag, score
            break
    if best_lag is None:
        best_lag, best_score = max(scores, key=lambda pair: pair[1], default=(None, 0.0))
    if best_lag is None or best_score < similarity:
        return {"laps": [], "lapPeriodS": None,
                "note": f"no repeating pattern found (best correlation {best_score:.2f})"}
    period_s = best_lag * dt
    laps, start = [], 0.0
    lap_index = 1
    while start + period_s <= log.duration_s + 0.5 * period_s:
        end = min(start + period_s, log.duration_s)
        if end - start > 0.5 * period_s:
            laps.append({"lap": lap_index, "startS": round(start, 2), "endS": round(end, 2),
                         "lapTimeS": round(end - start, 2)})
            lap_index += 1
        start += period_s
    times = [lap["lapTimeS"] for lap in laps]
    return {
        "laps": laps,
        "lapPeriodS": round(period_s, 2),
        "correlation": round(best_score, 3),
        "bestLapS": min(times) if times else None,
        "meanLapS": round(_mean(times), 2) if times else None,
        "caveat": "period-based split: it finds the lap length, not a physical start/finish line",
    }


def dead_reckon(log: Log, min_speed_kmh: float = 1.0) -> dict:
    """Reconstruct the driven path from speed and yaw rate alone.

        heading += yaw_rate * dt ;  x += v cos(h) dt ;  y += v sin(h) dt

    Gyro bias makes the path rotate slowly, so the honest quality measure is
    the loop-closure error: how far the end point is from the start, as a
    percentage of distance travelled. Quote that number with any map you show."""
    t = log.t
    x = y = heading = 0.0
    path, distance = [], 0.0
    for i in range(len(log)):
        dt = t[i] - t[i - 1] if i else 0.0
        v = log.col("speed_kmh")[i] / 3.6
        r = math.radians(log.col("gyro_z_dps")[i])
        if not all(math.isfinite(z) for z in (v, r, dt)):
            continue
        heading += r * dt
        if v >= min_speed_kmh / 3.6:
            x += v * math.cos(heading) * dt
            y += v * math.sin(heading) * dt
            distance += v * dt
        path.append((round(x, 3), round(y, 3)))
    closure = math.hypot(x, y)
    return {
        "points": path,
        "distanceM": round(distance, 1),
        "loopClosureErrorM": round(closure, 2),
        "loopClosureErrorPct": round(100.0 * closure / distance, 2) if distance > 1 else None,
        "caveat": "gyro-only heading: error grows with time; good for shape, not for absolute position",
    }


# --------------------------------------------------- 5. HEALTH / MODELS -----
def thermal_model(log: Log, limit_c: float = 80.0, column: str = "motor_temperature_c") -> dict:
    """First-order thermal fit on a heating phase:

        T(t) = T_inf - (T_inf - T0) * exp(-t / tau)

    Estimated by regressing dT/dt against T: the slope gives -1/tau and the
    intercept gives T_inf/tau, so both the time constant and the steady-state
    temperature at this duty come out of one fit. Extrapolating to `limit_c`
    turns reactive protection into a prediction."""
    t, temps = log.t, log.col(column)
    rate = _derivative(_low_pass(temps, t, 2.0), t)
    xs, ys = [], []
    for temp, dT in zip(temps, rate):
        if math.isfinite(temp) and math.isfinite(dT):
            xs.append(temp)
            ys.append(dT)
    intercept, slope, r2 = _linear_fit(xs, ys)
    if not math.isfinite(slope) or slope >= -1e-6:
        return {"column": column, "tauS": None, "steadyStateC": None,
                "note": "no clear heating trend — needs a sustained load phase"}
    tau = -1.0 / slope
    t_inf = intercept * tau
    now = next((v for v in reversed(temps) if math.isfinite(v)), math.nan)
    if math.isfinite(now) and now >= limit_c:
        seconds_to_limit = 0.0                 # already there (same convention as the firmware)
    elif math.isfinite(now) and t_inf > limit_c:
        seconds_to_limit = -tau * math.log((t_inf - limit_c) / (t_inf - now))
    else:
        seconds_to_limit = None                # will settle below the limit at this duty
    return {
        "column": column,
        "tauS": round(tau, 1),
        "steadyStateC": round(t_inf, 1),
        "currentC": round(now, 1) if math.isfinite(now) else None,
        "limitC": limit_c,
        "secondsToLimit": round(seconds_to_limit, 0) if seconds_to_limit is not None else None,
        "willReachLimit": bool(t_inf > limit_c),
        "rSquared": round(r2, 3) if math.isfinite(r2) else None,
    }


def servo_response(log: Log, channel: str = "lf", step_deg: float = 5.0) -> dict:
    """Identify each aero servo from command-vs-feedback during step moves:
    transport lag, achieved slew rate and steady-state error.

    Feed the measured slew rate back into `CFG_WING_RATE_MAX_DPS`; watch the
    numbers across sessions for wear or a failing linkage."""
    target = log.col(f"wing_target_{channel}_deg")
    measured = log.col(f"wing_{channel}_deg")
    t = log.t
    if not any(math.isfinite(v) for v in measured):
        return {"channel": channel, "steps": 0, "note": "no feedback logged (servo not calibrated?)"}
    lags, rates, errors = [], [], []
    i = 1
    while i < len(t):
        if math.isfinite(target[i]) and math.isfinite(target[i - 1]) and abs(target[i] - target[i - 1]) >= step_deg:
            t0, start_meas, goal = t[i], measured[i - 1], target[i]
            moved_at = None
            for k in range(i, min(i + 200, len(t))):
                if not math.isfinite(measured[k]):
                    continue
                if moved_at is None and abs(measured[k] - start_meas) > 0.5:
                    moved_at = t[k]
                if abs(measured[k] - goal) < 1.0:
                    if moved_at is not None and t[k] > moved_at:
                        rates.append(abs(goal - start_meas) / (t[k] - moved_at))
                    lags.append(moved_at - t0 if moved_at else 0.0)
                    errors.append(measured[k] - goal)
                    break
            i += 5
        i += 1
    if not rates:
        return {"channel": channel, "steps": 0, "note": "no step large enough to identify"}
    return {
        "channel": channel,
        "steps": len(rates),
        "meanLagS": round(_mean(lags), 3),
        "meanSlewDegPerS": round(_mean(rates), 1),
        "maxSlewDegPerS": round(max(rates), 1),
        "meanSteadyErrorDeg": round(_mean(errors), 2),
    }


def sensor_residuals(log: Log, accel_tolerance_g: float = 0.25,
                     wheel_tolerance_kmh: float = 2.0) -> dict:
    """Cross-check sensors against each other instead of against thresholds.

      * does longitudinal acceleration match the derivative of wheel speed?
      * do the four wheel speeds agree while driving in a straight line?

    A loose magnet, a failing Hall sensor or a shifted IMU shows up here long
    before any single-sensor limit is exceeded."""
    t = log.t
    v_ms = [s / 3.6 for s in log.col("speed_kmh")]
    a_from_wheels = _derivative(_low_pass(v_ms, t, 0.3), t)
    a_imu = [a * G for a in log.col("accel_x_g")]
    accel_res = [w - i for w, i in zip(a_from_wheels, a_imu) if math.isfinite(w) and math.isfinite(i)]

    wheels = ["fl", "fr", "rl", "rr"]
    spreads, per_wheel = [], {w: [] for w in wheels}
    for i in range(len(log)):
        if abs(log.col("accel_y_g")[i]) > 0.15 or log.col("speed_kmh")[i] < 5:
            continue                                  # only straight-line, moving samples
        values = {w: log.col(f"wheel_speed_{w}_kmh")[i] for w in wheels}
        if not all(math.isfinite(v) for v in values.values()):
            continue
        mean_v = statistics.fmean(values.values())
        spreads.append(max(values.values()) - min(values.values()))
        for w, v in values.items():
            per_wheel[w].append(v - mean_v)

    flags = []
    accel_rms = math.sqrt(_mean([r * r for r in accel_res])) / G if accel_res else math.nan
    if math.isfinite(accel_rms) and accel_rms > accel_tolerance_g:
        flags.append(f"wheel-speed derivative and IMU disagree by {accel_rms:.2f} g rms "
                     "(check wheel diameter, magnet count, or IMU axis mapping)")
    for w in wheels:
        bias = _mean(per_wheel[w]) if per_wheel[w] else 0.0
        if abs(bias) > wheel_tolerance_kmh:
            flags.append(f"wheel {w.upper()} reads {bias:+.1f} km/h vs the others in a straight line "
                         "(magnet spacing, sensor gap, or a missed pulse)")
    return {
        "accelResidualRmsG": round(accel_rms, 3) if math.isfinite(accel_rms) else None,
        "wheelSpreadMeanKmh": round(_mean(spreads), 2) if spreads else None,
        "wheelSpreadP95Kmh": round(_percentile(spreads, 0.95), 2) if spreads else None,
        "wheelBiasKmh": {w: round(_mean(per_wheel[w]), 2) for w in wheels if per_wheel[w]},
        "flags": flags,
        "straightLineSamples": len(spreads),
    }


def digital_twin_error(log: Log, mass_kg: float = 2.0, cda: float = 0.010,
                       rolling_coeff: float = 0.015, motor_force_per_duty_n: float = 12.0,
                       air_density: float = AIR_DENSITY) -> dict:
    """Run a simple longitudinal model alongside the recorded run and report
    how far it drifts from the measured speed.

        m dv/dt = F_motor(throttle) - 0.5 rho CdA v^2 - Cr m g

    The point is not the model's sophistication: it is that the digital twin
    is *validated* against measured data and reports its own error, instead of
    being an animation. Feed `cda` from `coastdown_drag()` and tune
    `motor_force_per_duty_n` on a straight-line pull, then quote the RMS error."""
    t = log.t
    throttle = log.col("throttle_pct")
    measured = [s / 3.6 for s in log.col("speed_kmh")]
    v = measured[0] if measured and math.isfinite(measured[0]) else 0.0
    sim, errors = [], []
    for i in range(len(log)):
        dt = t[i] - t[i - 1] if i else 0.0
        if not (0 < dt < 1.0):
            sim.append(v)
            continue
        duty = (throttle[i] / 100.0) if math.isfinite(throttle[i]) else 0.0
        drag = 0.5 * air_density * cda * v * v
        rolling = rolling_coeff * mass_kg * G if v > 0.1 else 0.0
        accel = (motor_force_per_duty_n * duty - drag - rolling) / mass_kg
        v = max(0.0, v + accel * dt)
        sim.append(v)
        if math.isfinite(measured[i]):
            errors.append(v - measured[i])
    rms = math.sqrt(_mean([e * e for e in errors])) if errors else math.nan
    mean_measured = _mean([m for m in measured if math.isfinite(m)])
    return {
        "samples": len(errors),
        "rmsErrorKmh": round(rms * 3.6, 2) if math.isfinite(rms) else None,
        "maxErrorKmh": round(max((abs(e) for e in errors), default=0.0) * 3.6, 2),
        "relativeErrorPct": round(100.0 * rms / mean_measured, 1) if mean_measured > 0.1 else None,
        "series": [round(s * 3.6, 2) for s in sim],
        "parameters": {"massKg": mass_kg, "cdA": cda, "rollingCoeff": rolling_coeff,
                       "motorForcePerDutyN": motor_force_per_duty_n},
        "note": "parameters are [PLACEHOLDER] until fitted from coast-down and straight-line data",
    }
