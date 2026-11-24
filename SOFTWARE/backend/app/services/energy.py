"""
energy.py — session energy and range, measured from recorded telemetry.

Stage 2.1. Before this, `ai_engine.generate_recommendation()` reported
`energyPerLap = 35 + 0.15 x speed` and `predictedRangeLaps = battery% / 4`:
formulas invented to fill the dashboard fields, derived from nothing the car
measured. They are now computed here by integrating pack voltage x current
over the frames the session actually recorded, and the AI response says
whether the numbers are measured or unavailable — it never fabricates them.

The vehicle gateway does the same integration live for its own log
(HARDWARE/raspberry_pi/main.py, DerivedEstimator); this is the ground-station
copy, working from what reached the database. Definitions and error sources:
SOFTWARE/Documentation/DerivedMetrics.md.
"""
from __future__ import annotations

import math

from app.db import get_conn, loads

# A lap is only meaningful once the car has moved; below this the per-lap
# figures would divide by ~nothing.
_MIN_LAP_DISTANCE_M = 20.0
_DEFAULT_LAP_DISTANCE_M = 100.0     # [PLACEHOLDER] until a real track length is measured
_PACK_CAPACITY_AH = 2.2             # [PLACEHOLDER] printed on the LiPo
_PACK_NOMINAL_V = 11.1              # 3S
_RESERVE_PCT = 15.0


def _finite(value, default: float = math.nan) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def session_energy(session_id: str, limit: int = 20000) -> dict:
    """Integrate power and distance over one session's recorded frames.

    Returns `available: False` when there is not enough recorded movement to
    say anything — the caller must not substitute a guess."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT frame_json FROM telemetry_frames
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    energy_wh = 0.0
    distance_m = 0.0
    samples = 0
    prev_t: float | None = None
    prev_distance: float | None = None
    soc_pct: float | None = None

    for row in rows:
        frame = loads(row["frame_json"]) or {}
        t_ms = _finite(frame.get("timestamp"))
        v = _finite(frame.get("voltage"))
        i = _finite(frame.get("current"))
        speed_kmh = _finite(frame.get("speed"), 0.0)
        derived = frame.get("derived") if isinstance(frame.get("derived"), dict) else {}
        if math.isfinite(_finite(derived.get("socCoulombPct"))):
            soc_pct = _finite(derived.get("socCoulombPct"))
        elif math.isfinite(_finite(frame.get("battery"))):
            soc_pct = _finite(frame.get("battery"))

        if not math.isfinite(t_ms):
            continue
        if prev_t is not None:
            dt = (t_ms - prev_t) / 1000.0
            if 0.0 < dt < 1.0:                      # ignore gaps and restarts
                if math.isfinite(v) and math.isfinite(i):
                    energy_wh += v * i * dt / 3600.0
                if math.isfinite(speed_kmh):
                    distance_m += speed_kmh / 3.6 * dt
                samples += 1
        prev_t = t_ms

        # Prefer the gateway's own integrated distance when it is present:
        # it was computed at the full 20 Hz rate, not from stored frames.
        gateway_distance = _finite(frame.get("distance"))
        if math.isfinite(gateway_distance) and gateway_distance > 0:
            prev_distance = gateway_distance

    if prev_distance is not None and prev_distance > distance_m:
        distance_m = prev_distance

    if samples < 10 or distance_m < _MIN_LAP_DISTANCE_M:
        return {
            "available": False,
            "reason": "not enough recorded movement to measure energy",
            "samples": samples,
            "distanceM": round(distance_m, 1),
            "energyWh": round(energy_wh, 3),
        }

    wh_per_km = energy_wh / (distance_m / 1000.0)
    return {
        "available": True,
        "samples": samples,
        "distanceM": round(distance_m, 1),
        "energyWh": round(energy_wh, 3),
        "whPerKm": round(wh_per_km, 1),
        "socPct": round(soc_pct, 1) if soc_pct is not None else None,
    }


def energy_context(session_id: str | None, lap_distance_m: float = _DEFAULT_LAP_DISTANCE_M) -> dict | None:
    """What `generate_recommendation()` needs: measured energy per lap and
    laps remaining, or None when the data cannot support either."""
    if not session_id:
        return None
    stats = session_energy(session_id)
    if not stats.get("available"):
        return None

    energy_per_lap = stats["whPerKm"] * (lap_distance_m / 1000.0)
    soc = stats.get("socPct")
    laps_remaining = None
    if soc is not None and energy_per_lap > 0:
        usable_wh = _PACK_CAPACITY_AH * _PACK_NOMINAL_V * max(0.0, soc - _RESERVE_PCT) / 100.0
        laps_remaining = usable_wh / energy_per_lap
    return {
        "energyPerLap": round(energy_per_lap, 1),
        "predictedRangeLaps": round(laps_remaining, 1) if laps_remaining is not None else None,
        "whPerKm": stats["whPerKm"],
        "distanceM": stats["distanceM"],
        "energyWh": stats["energyWh"],
        "lapDistanceM": lap_distance_m,
        "measured": True,
    }
