#!/usr/bin/env python3
"""
analyze.py — turn a Raspberry Pi telemetry log into the numbers that go in the
report.

    python analyze.py logs/vehicle_20260918_101500.csv
    python analyze.py logs/*.csv --json results.json
    python analyze.py baseline.csv --compare ai_assist.csv     # active-aero result
    python analyze.py run.csv --plots plots/                   # needs matplotlib

Everything printed here is derived from sensors already on the car (wheel
Halls, MPU6050, INA226, ADS1115, servo feedback). Nothing is simulated, and
anything the data cannot support is reported as "insufficient data" rather
than filled in. Definitions: SOFTWARE/Documentation/DerivedMetrics.md.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import metrics
from log_loader import Log, load


def analyse(log: Log, *, mass_kg: float, wheelbase_m: float, capacity_ah: float,
            frontal_area_m2: float) -> dict:
    laps = metrics.detect_laps(log)
    result = {
        "file": str(log.path),
        "samples": len(log),
        "durationS": round(log.duration_s, 1),
        "sampleRateHz": round(log.sample_rate_hz, 1),
        "energy": metrics.energy_summary(log),
        "range": metrics.range_estimate(log, capacity_ah=capacity_ah),
        "packResistance": metrics.pack_resistance(log),
        "frictionCircle": {k: v for k, v in metrics.friction_circle(log).items() if k != "points"},
        "understeer": metrics.understeer_gradient(log, wheelbase_m=wheelbase_m),
        "slip": {k: v for k, v in metrics.slip_ratios(log).items() if k != "series"},
        "sideslip": {k: v for k, v in metrics.sideslip_estimate(log).items() if k != "series"},
        "fusedSpeed": {k: v for k, v in metrics.fused_speed(log).items() if k != "series"},
        "coastdownDrag": metrics.coastdown_drag(log, mass_kg=mass_kg, frontal_area_m2=frontal_area_m2),
        "lateralGvsSpeed": metrics.lateral_g_vs_speed(log),
        "laps": laps,
        "lapEnergy": metrics.lap_energy(log, laps["laps"]) if laps["laps"] else [],
        "deadReckon": {k: v for k, v in metrics.dead_reckon(log).items() if k != "points"},
        "thermalMotor": metrics.thermal_model(log, limit_c=80.0, column="motor_temperature_c"),
        "thermalBattery": metrics.thermal_model(log, limit_c=60.0, column="battery_temperature_c"),
        "servoResponse": [metrics.servo_response(log, ch) for ch in ("lf", "rf", "lr", "rr")],
        "sensorResiduals": metrics.sensor_residuals(log),
        "digitalTwin": {k: v for k, v in metrics.digital_twin_error(log, mass_kg=mass_kg).items()
                        if k != "series"},
    }
    return result


def _fmt(value, unit: str = "", digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}{unit}"
    return f"{value}{unit}"


def print_summary(r: dict) -> None:
    e, fc, us = r["energy"], r["frictionCircle"], r["understeer"]
    print(f"\n=== {Path(r['file']).name} ===")
    print(f"{r['samples']} samples over {_fmt(r['durationS'], ' s', 1)} at {_fmt(r['sampleRateHz'], ' Hz', 1)}")

    print("\n-- Energy --")
    print(f"  distance          {_fmt(e['distanceM'], ' m', 1)}")
    print(f"  energy used       {_fmt(e['energyWh'], ' Wh', 3)}   (regen {_fmt(e['regenWh'], ' Wh', 3)})")
    print(f"  consumption       {_fmt(e['whPerKm'], ' Wh/km', 1)}")
    print(f"  mean / peak power {_fmt(e['meanPowerW'], ' W', 1)} / {_fmt(e['peakPowerW'], ' W', 1)}")
    print(f"  pack resistance   {_fmt(r['packResistance']['ohm'], ' ohm', 4)} "
          f"({r['packResistance']['samples']} load steps)")
    print(f"  range estimate    {_fmt(r['range'].get('rangeKm'), ' km', 2)} "
          f"at SoC {_fmt(r['range'].get('socPct'), ' %', 0)}")

    print("\n-- Vehicle dynamics --")
    print(f"  peak lateral      {_fmt(fc.get('peakLatG'), ' g', 2)}")
    print(f"  peak accel/brake  {_fmt(fc.get('peakLongAccelG'), ' g', 2)} / "
          f"{_fmt(fc.get('peakLongBrakeG'), ' g', 2)}")
    print(f"  friction envelope {_fmt(fc.get('envelopeG'), ' g', 2)}, "
          f"{_fmt(fc.get('timeNearLimitPct'), ' %', 1)} of the time near it")
    print(f"  understeer        {_fmt(us.get('gradientDegPerG'), ' deg/g', 2)} -> {us.get('balance', 'n/a')}"
          f"  (R2 {_fmt(us.get('rSquared'), '', 2)})")
    print(f"  slip p95          {_fmt(r['slip'].get('p95Slip'), '', 3)}  "
          f"(wheelspin {r['slip'].get('wheelspinSamples')}, lock-up {r['slip'].get('lockupSamples')} samples)")
    print(f"  sideslip p95      {_fmt(r['sideslip'].get('p95AbsDeg'), ' deg', 1)} (indicative)")

    print("\n-- Aerodynamics --")
    cd = r["coastdownDrag"]
    if cd.get("cdA") is None:
        print(f"  coast-down CdA    n/a ({cd.get('note', '')})")
    else:
        print(f"  coast-down CdA    {_fmt(cd['cdA'], ' m2', 5)} (Cd {_fmt(cd.get('cd'), '', 3)}, "
              f"R2 {_fmt(cd.get('rSquared'), '', 2)}, {cd['segmentsUsed']} segments)")
    for b in r["lateralGvsSpeed"]["bins"]:
        print(f"    {b['speedBinKmh']:>5.1f} km/h : peak {b['peakLatG']:.2f} g, "
              f"p95 {b['p95LatG']:.2f} g ({b['samples']} samples)")

    print("\n-- Laps --")
    laps = r["laps"]
    if laps["laps"]:
        print(f"  period {_fmt(laps['lapPeriodS'], ' s', 2)}, best {_fmt(laps.get('bestLapS'), ' s', 2)}, "
              f"mean {_fmt(laps.get('meanLapS'), ' s', 2)} over {len(laps['laps'])} laps")
        for lap in r["lapEnergy"]:
            print(f"    lap {lap['lap']}: {lap['lapTimeS']:.2f} s, {lap['energyWh']:.3f} Wh, "
                  f"{lap['distanceM']:.0f} m")
    else:
        print(f"  {laps.get('note', 'no laps detected')}")
    dr = r["deadReckon"]
    print(f"  dead-reckoned path {_fmt(dr.get('distanceM'), ' m', 0)}, "
          f"loop closure error {_fmt(dr.get('loopClosureErrorPct'), ' %', 1)}")

    print("\n-- Health --")
    for key, label in (("thermalMotor", "motor"), ("thermalBattery", "battery")):
        th = r[key]
        if th.get("tauS") is None:
            print(f"  {label:<8} thermal: {th.get('note', 'n/a')}")
        else:
            print(f"  {label:<8} tau {_fmt(th['tauS'], ' s', 0)}, steady state "
                  f"{_fmt(th['steadyStateC'], ' C', 1)}, time to limit "
                  f"{_fmt(th.get('secondsToLimit'), ' s', 0)}")
    for sr in r["servoResponse"]:
        if sr.get("steps"):
            print(f"  servo {sr['channel'].upper():<3} lag {_fmt(sr['meanLagS'], ' s', 3)}, "
                  f"slew {_fmt(sr['meanSlewDegPerS'], ' deg/s', 0)}, "
                  f"steady error {_fmt(sr['meanSteadyErrorDeg'], ' deg', 2)}")
    print(f"  twin RMS error    {_fmt(r['digitalTwin'].get('rmsErrorKmh'), ' km/h', 2)} "
          f"({_fmt(r['digitalTwin'].get('relativeErrorPct'), ' %', 1)} of mean speed)")
    res = r["sensorResiduals"]
    print(f"  sensor residuals  accel rms {_fmt(res.get('accelResidualRmsG'), ' g', 3)}, "
          f"wheel spread p95 {_fmt(res.get('wheelSpreadP95Kmh'), ' km/h', 2)}")
    for flag in res["flags"]:
        print(f"    ! {flag}")
    print()


def print_comparison(comparison: dict) -> None:
    print("\n=== Active aero: baseline vs AI-assist ===")
    if not comparison["bins"]:
        print("  no overlapping speed bins — the two runs need to cover the same speeds")
        return
    print(f"  {'speed':>8} {'baseline':>10} {'assisted':>10} {'delta':>8} {'delta %':>9}")
    for row in comparison["bins"]:
        print(f"  {row['speedBinKmh']:>6.1f}   {row['baselineP95LatG']:>8.3f} g "
              f"{row['assistedP95LatG']:>8.3f} g {row['deltaG']:>+7.3f} "
              f"{_fmt(row['deltaPct'], ' %', 1):>9}")
    print(f"  mean change: {_fmt(comparison['meanDeltaPct'], ' %', 1)} "
          f"over {comparison['binsCompared']} bins")
    print(f"  {comparison['caveat']}\n")


def write_plots(log: Log, out_dir: Path) -> list[str]:
    """Optional: matplotlib figures for the report. Skipped with a clear
    message if matplotlib is not installed — it is not a dependency."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plots (pip install matplotlib)")
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    fc = metrics.friction_circle(log)
    if fc.get("points"):
        lat = [p[0] for p in fc["points"]]
        lon = [p[1] for p in fc["points"]]
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(lat, lon, s=3, alpha=0.3)
        circle = plt.Circle((0, 0), fc["envelopeG"], fill=False, linestyle="--")
        ax.add_patch(circle)
        ax.set_xlabel("lateral g"); ax.set_ylabel("longitudinal g")
        ax.set_title("Friction circle"); ax.grid(True, alpha=0.3); ax.set_aspect("equal")
        path = out_dir / "friction_circle.png"; fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig); written.append(str(path))

    bins = metrics.lateral_g_vs_speed(log)["bins"]
    if bins:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot([b["speedBinKmh"] for b in bins], [b["peakLatG"] for b in bins], "o-", label="peak")
        ax.plot([b["speedBinKmh"] for b in bins], [b["p95LatG"] for b in bins], "s--", label="p95")
        ax.set_xlabel("speed (km/h)"); ax.set_ylabel("lateral acceleration (g)")
        ax.set_title("Lateral grip vs speed"); ax.legend(); ax.grid(True, alpha=0.3)
        path = out_dir / "lateral_g_vs_speed.png"; fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig); written.append(str(path))

    dr = metrics.dead_reckon(log)
    if dr["points"]:
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.plot([p[0] for p in dr["points"]], [p[1] for p in dr["points"]], linewidth=1)
        ax.set_title(f"Dead-reckoned path (closure error {dr['loopClosureErrorPct']} %)")
        ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)"); ax.grid(True, alpha=0.3); ax.set_aspect("equal")
        path = out_dir / "path.png"; fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig); written.append(str(path))

    t = log.t
    fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    axes[0].plot(t, log.col("speed_kmh"), linewidth=1); axes[0].set_ylabel("km/h")
    axes[1].plot(t, log.col("battery_voltage_v"), linewidth=1, label="V")
    axes[1].plot(t, log.col("battery_current_a"), linewidth=1, label="A")
    axes[1].set_ylabel("pack"); axes[1].legend()
    for ch in ("lf", "rf", "lr", "rr"):
        axes[2].plot(t, log.col(f"wing_{ch}_deg"), linewidth=1, label=ch.upper())
    axes[2].set_ylabel("flap deg"); axes[2].set_xlabel("time (s)"); axes[2].legend(ncol=4)
    for ax in axes:
        ax.grid(True, alpha=0.3)
    path = out_dir / "overview.png"; fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig); written.append(str(path))
    return written


def main() -> None:
    p = argparse.ArgumentParser(description="Analyse Raspberry Pi telemetry logs")
    p.add_argument("logs", nargs="+", help="CSV log files from the Pi (logs/vehicle_*.csv)")
    p.add_argument("--compare", default=None,
                   help="Second log to compare against the first (baseline vs AI-assist aero)")
    p.add_argument("--json", default=None, help="Write the full results to this JSON file")
    p.add_argument("--plots", default=None, help="Directory for PNG figures (needs matplotlib)")
    p.add_argument("--mass-kg", type=float, default=2.0, help="[PLACEHOLDER] weigh the car with battery")
    p.add_argument("--wheelbase-m", type=float, default=0.26, help="[PLACEHOLDER] measure it")
    p.add_argument("--frontal-area-m2", type=float, default=0.018, help="[PLACEHOLDER] from CAD")
    p.add_argument("--pack-capacity-ah", type=float, default=2.2, help="printed on the LiPo")
    p.add_argument("-q", "--quiet", action="store_true", help="JSON only, no printed summary")
    args = p.parse_args()

    results = []
    logs = []
    for path in args.logs:
        log = load(path)
        if len(log) < 10:
            print(f"{path}: only {len(log)} samples — skipped")
            continue
        logs.append(log)
        r = analyse(log, mass_kg=args.mass_kg, wheelbase_m=args.wheelbase_m,
                    capacity_ah=args.pack_capacity_ah, frontal_area_m2=args.frontal_area_m2)
        results.append(r)
        if not args.quiet:
            print_summary(r)

    payload: dict = {"runs": results}
    if args.compare:
        other = load(args.compare)
        comparison = metrics.compare_aero_modes(logs[0], other)
        payload["aeroComparison"] = comparison
        if not args.quiet:
            print_comparison(comparison)

    if args.plots and logs:
        written = write_plots(logs[0], Path(args.plots))
        payload["plots"] = written
        if written and not args.quiet:
            print("plots written:", ", ".join(written))

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"results written to {args.json}")


if __name__ == "__main__":
    main()
