"""
twin.py — the digital twin: a full-scale lap model driven by the car's REAL settings.

The 1:10 car sits on a dyno, so aero changes cannot change what its wheels measure.
The twin answers the question the aero settings exist for: "what would these flap
angles and zone offsets do to a lap of this circuit at full scale?" It re-runs the
same quasi-steady-state point-mass lap as build_tracks.py (forward/backward passes
with a friction circle and constant power), with drag and downforce taken from the
flap angles the VCU is actually configured with.

Flap angle -> aero coefficients (ILLUSTRATIVE, 2026-style), θ = mean of the pair angles:
    ClA(θ) = 1.782 + 3.372 * (1 - exp(-θ / 18°))    downforce saturates as the flap stalls
    CdA(θ) = 0.923 + 0.004 θ + 0.000281 θ²          drag keeps rising
Both pass through build_tracks.py's reference points (2.6/0.95 m² at 5°, 4.6/1.35 m² at
32.5°), so the firmware defaults reproduce the reference lap. Because downforce saturates
while drag does not, the best corner-mode angle depends on the circuit: slow, twisty
tracks want more flap, fast ones less. That trade-off is what the AI engineer optimises.
The asymmetric left/right split is a yaw-balance tool and has no effect in a
point-mass model — the twin says so instead of inventing a number.
"""
from __future__ import annotations

import math
from functools import lru_cache

from app import tracks

MASS_KG, POWER_W, MU, RHO, G = 800.0, 620e3, 1.80, 1.20, 9.81
MIN_RADIUS_M = 9.0


def aero_coeffs(front_deg: float, rear_deg: float) -> tuple[float, float]:
    th = max(0.0, (front_deg + rear_deg) / 2.0)
    cla = 1.782 + 3.372 * (1.0 - math.exp(-th / 18.0))
    cda = 0.923 + 0.004 * th + 0.000281 * th * th
    return cla, cda


def _lap(kappa: list[float], ds: float, cla: list[float], cda: list[float]) -> list[float]:
    n = len(kappa)
    vlim = []
    for i in range(n):
        k = min(abs(kappa[i]), 1.0 / MIN_RADIUS_M)
        denom = MASS_KG * k - 0.5 * MU * RHO * cla[i]
        vlim.append(120.0 if denom <= 0 else min(120.0, math.sqrt(MU * MASS_KG * G / denom)))
    start = min(range(n), key=lambda i: vlim[i])
    order = [(start + i) % n for i in range(n)]

    def grip_left(i: int, v: float) -> float:
        normal = MASS_KG * G + 0.5 * RHO * cla[i] * v * v
        lat = MASS_KG * v * v * abs(kappa[i])
        return math.sqrt(max((MU * normal) ** 2 - lat * lat, 0.0))

    fwd = [0.0] * n
    v = vlim[start]
    for i in order:
        v = min(v, vlim[i])
        fwd[i] = v
        f = min(POWER_W / max(v, 1.0), grip_left(i, v)) - 0.5 * RHO * cda[i] * v * v
        v = math.sqrt(max(v * v + 2 * f / MASS_KG * ds, 0.0))
    bwd = [0.0] * n
    v = vlim[start]
    for i in reversed(order):
        v = min(v, vlim[i])
        bwd[i] = v
        f = grip_left(i, v) + 0.5 * RHO * cda[i] * v * v
        v = math.sqrt(v * v + 2 * f / MASS_KG * ds)
    return [min(fwd[i], bwd[i], vlim[i]) for i in range(n)]


@lru_cache(maxsize=512)
def simulate(track_id: int, straight_front: float, straight_rear: float, corner_front: float,
             corner_rear: float, open_delay_m: float = 0.0, close_early_m: float = 0.0) -> dict:
    """Full-scale lap with these flap settings (degrees) and zone offsets (metres)."""
    t = tracks.get(track_id)
    kappa = t["series"]["kappa"]
    n = len(kappa)
    ds = t["lengthM"] / n
    straight = [tracks.straight_at(t, i / n, open_delay_m, close_early_m) for i in range(n)]
    s_cla, s_cda = aero_coeffs(straight_front, straight_rear)
    c_cla, c_cda = aero_coeffs(corner_front, corner_rear)
    cla = [s_cla if s else c_cla for s in straight]
    cda = [s_cda if s else c_cda for s in straight]
    v = _lap(kappa, ds, cla, cda)
    seg = [ds / max(x, 1.0) for x in v]
    lap = sum(seg)
    third = n // 3
    sectors = [sum(seg[:third]), sum(seg[third:2 * third]), sum(seg[2 * third:])]
    return {"lapTimeS": round(lap, 3), "sectorsS": [round(x, 3) for x in sectors],
            "vmaxKmh": round(max(v) * 3.6, 1), "vminKmh": round(min(v) * 3.6, 1),
            "straightFraction": round(sum(straight) / n, 3), "speedKmh": [round(x * 3.6, 1) for x in v]}


def simulate_config(track_id: int, cfg: dict) -> dict:
    return simulate(int(track_id), cfg["straightFrontCdeg"] / 100, cfg["straightRearCdeg"] / 100,
                    cfg["cornerFrontCdeg"] / 100, cfg["cornerRearCdeg"] / 100,
                    float(cfg["straightOpenDelayM"]), float(cfg["straightCloseEarlyM"]))


def forces_at(track_id: int, pos: float, cfg: dict, front_deg: float, rear_deg: float) -> dict:
    """Modelled full-scale downforce and drag at this point of the lap, for the flap
    angles the car actually has right now (measured where the MG996R feedback exists)."""
    sim = simulate_config(track_id, cfg)
    t = tracks.get(track_id)
    i = tracks.sample(t, pos)["index"]
    v = sim["speedKmh"][i] / 3.6
    cla, cda = aero_coeffs(front_deg, rear_deg)
    return {"twinSpeedKmh": sim["speedKmh"][i], "downforceN": round(0.5 * RHO * cla * v * v),
            "dragN": round(0.5 * RHO * cda * v * v), "clA": round(cla, 2), "cdA": round(cda, 2),
            "latG": t["series"]["latG"][i], "longG": t["series"]["longG"][i]}


def optimise_aero(track_id: int, cfg: dict) -> dict | None:
    """Grid search the flap angles for the lowest modelled lap time on this circuit.
    Zone offsets are left alone (they exist for servo lag, which the twin cannot see)."""
    base = simulate_config(track_id, cfg)["lapTimeS"]
    best = None
    s_grid = [0.0, 2.5, 5.0, 7.5]
    c_grid = [15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0]
    for s in s_grid:
        for cf in c_grid:
            for cr in c_grid:
                if abs(cf - cr) > 10:        # keep the aero balance within a sane window
                    continue
                r = simulate(int(track_id), s, s, cf, cr, float(cfg["straightOpenDelayM"]),
                             float(cfg["straightCloseEarlyM"]))["lapTimeS"]
                if best is None or r < best[0] - 1e-6:
                    best = (r, s, cf, cr)
    if best is None:
        return None
    return {"baselineLapS": base, "bestLapS": best[0], "gainS": round(base - best[0], 3),
            "straightDeg": best[1], "cornerFrontDeg": best[2], "cornerRearDeg": best[3]}
