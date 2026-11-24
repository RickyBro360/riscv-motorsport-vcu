#!/usr/bin/env python3
"""
build_tracks.py — the track-map library for the whole platform.

Reads the 2026-calendar circuit outlines in sources/*.geojson (bacinger/f1-circuits,
MIT licence, see sources/LICENSE-f1-circuits.md) and produces ONE set of maps that
every layer shares:

  HARDWARE/esp32/main_updated_corrected__1_/track_maps.h   compiled into the VCU
  SOFTWARE/tracks/tracks.json                              backend (AI engineer, twin)
  SOFTWARE/dashboard/public/tracks.json                    dashboard (map, twin panels)

For each circuit it computes, at full scale:
  * centreline resampled every 2 m and smoothed, signed curvature (+ = left turn)
  * a quasi-steady-state point-mass lap simulation (the digital twin's reference lap)
    with 2026-style active aero: STRAIGHT MODE (low drag) in the detected straight
    zones, CORNER MODE (high downforce) everywhere else
  * the road-wheel steering angle a car of 3.6 m wheelbase needs: atan(L * kappa).
    The angle is scale-invariant, so the 1:10 model steers exactly this angle.
  * braking zones, three equal-distance sectors, detected corners

Then it samples everything at N_POINTS equally spaced positions per lap for the VCU.

IMPORTANT — what these numbers are. The circuit geometry is real (within the
accuracy of the public GeoJSON, a few metres). The speeds, lap times and g-forces
are the output of a simple model with ILLUSTRATIVE vehicle parameters (below), not
real F1 data. Straight-mode zones are detected from geometry, not taken from the
FIA's published zones. Sectors are equal thirds, not official timing lines. Every
consumer labels them as modelled.

Run:  python SOFTWARE/tracks/build_tracks.py        (numpy + scipy)
A test (HARDWARE/tests/test_tracks.py) checks the three outputs stay in sync.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.interpolate import splev, splprep
from scipy.ndimage import gaussian_filter1d

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = HERE / "sources"
OUT_JSON = HERE / "tracks.json"
OUT_DASH = ROOT / "SOFTWARE" / "dashboard" / "public" / "tracks.json"
OUT_H = ROOT / "HARDWARE" / "esp32" / "main_updated_corrected__1_" / "track_maps.h"

N_POINTS = 512          # VCU samples per lap (4 bytes each)
N_DISPLAY = 600         # dashboard polyline points per lap
STEP_M = 2.0            # resample spacing before smoothing
SPLINE_TOL_M = 2.5      # periodic smoothing spline: RMS distance allowed from the GeoJSON vertices
KAPPA_SIGMA_M = 4.0     # light extra smoothing on curvature
MIN_RADIUS_M = 9.0      # tightest corner allowed after smoothing (Monaco hairpin ~10 m)

# --- illustrative 2026-style car (full scale) --------------------------------
MASS_KG = 800.0         # 768 kg minimum + driver/fuel margin
POWER_W = 620e3         # ICE + MGU-K, simplified to constant power
MU = 1.80               # tyre friction coefficient
RHO = 1.20
G = 9.81
CLA_CORNER, CDA_CORNER = 4.6, 1.35      # corner mode (m^2)
CLA_STRAIGHT, CDA_STRAIGHT = 2.6, 0.95  # straight mode (m^2)
WHEELBASE_M = 3.6
STEER_LIMIT_DEG = 30.0  # the model car's mechanical lock
STRAIGHT_MIN_LEN_M = 280.0     # a straight-mode zone must be at least this long
STRAIGHT_MAX_KAPPA = 1.0 / 450.0  # and gentler than this everywhere in it

# 2026 calendar in round order (formula1.com/en/racing/2026, checked Sep 2026).
# Jeddah was on the calendar as first published and is kept as an extra map.
CALENDAR = [
    ("au-1953", "AUS", "Australian GP", "Albert Park"),
    ("cn-2004", "CHN", "Chinese GP", "Shanghai"),
    ("jp-1962", "JPN", "Japanese GP", "Suzuka"),
    ("us-2022", "MIA", "Miami GP", "Miami"),
    ("ca-1978", "CAN", "Canadian GP", "Gilles-Villeneuve"),
    ("mc-1929", "MON", "Monaco GP", "Monaco"),
    ("es-1991", "BCN", "Barcelona-Catalunya GP", "Barcelona-Catalunya"),
    ("at-1969", "AUT", "Austrian GP", "Red Bull Ring"),
    ("gb-1948", "GBR", "British GP", "Silverstone"),
    ("be-1925", "BEL", "Belgian GP", "Spa-Francorchamps"),
    ("hu-1986", "HUN", "Hungarian GP", "Hungaroring"),
    ("nl-1948", "NED", "Dutch GP", "Zandvoort"),
    ("it-1922", "ITA", "Italian GP", "Monza"),
    ("es-2026", "ESP", "Spanish GP", "Madring"),
    ("az-2016", "AZE", "Azerbaijan GP", "Baku"),
    ("bh-2002", "BHR", "Bahrain GP", "Sakhir"),
    ("sg-2008", "SGP", "Singapore GP", "Marina Bay"),
    ("us-2012", "USA", "United States GP", "COTA"),
    ("mx-1962", "MEX", "Mexico City GP", "Hermanos Rodriguez"),
    ("br-1940", "BRA", "Sao Paulo GP", "Interlagos"),
    ("us-2023", "LVG", "Las Vegas GP", "Las Vegas"),
    ("qa-2004", "QAT", "Qatar GP", "Lusail"),
    ("ae-2009", "ABU", "Abu Dhabi GP", "Yas Marina"),
    ("sa-2021", "KSA", "Saudi Arabian GP", "Jeddah"),
]
DEFAULT_TRACK_CODE = "ITA"

# flags byte in the VCU table
F_STRAIGHT = 0x01   # straight mode allowed here (low-drag aero)
F_BRAKING = 0x02    # model is braking here
F_SECTOR_SHIFT = 2  # bits 2-3: sector 0..2
F_APEX = 0x10       # local curvature peak (corner apex)


def load_xy(code: str) -> tuple[np.ndarray, dict]:
    d = json.loads((SRC / f"{code}.geojson").read_text(encoding="utf-8"))
    feat = d["features"][0]
    c = np.array(feat["geometry"]["coordinates"], dtype=float)
    if np.allclose(c[0], c[-1]):
        c = c[:-1]
    lat0 = math.radians(c[:, 1].mean())
    x = (c[:, 0] - c[0, 0]) * 111320.0 * math.cos(lat0)
    y = (c[:, 1] - c[0, 1]) * 110540.0
    return np.column_stack([x, y]), feat["properties"]


def resample_closed(xy: np.ndarray, step: float) -> np.ndarray:
    closed = np.vstack([xy, xy[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    n = int(round(s[-1] / step))
    t = np.linspace(0, s[-1], n, endpoint=False)
    return np.column_stack([np.interp(t, s, closed[:, 0]), np.interp(t, s, closed[:, 1])])


def curvature(xy: np.ndarray, ds: float) -> np.ndarray:
    dx = np.gradient(np.concatenate([xy[-2:, 0], xy[:, 0], xy[:2, 0]]), ds)[2:-2]
    dy = np.gradient(np.concatenate([xy[-2:, 1], xy[:, 1], xy[:2, 1]]), ds)[2:-2]
    ddx = np.gradient(np.concatenate([[dx[-2], dx[-1]], dx, [dx[0], dx[1]]]), ds)[2:-2]
    ddy = np.gradient(np.concatenate([[dy[-2], dy[-1]], dy, [dy[0], dy[1]]]), ds)[2:-2]
    k = (dx * ddy - dy * ddx) / np.power(dx * dx + dy * dy, 1.5)
    return np.clip(k, -1 / MIN_RADIUS_M, 1 / MIN_RADIUS_M)


def corner_speed(kappa: np.ndarray, cla: np.ndarray) -> np.ndarray:
    k = np.abs(kappa)
    denom = MASS_KG * k - 0.5 * MU * RHO * cla
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.sqrt(MU * MASS_KG * G / denom)
    v[denom <= 0] = 1e3
    return v


def lap_sim(kappa: np.ndarray, ds: float, straight: np.ndarray):
    """Forward/backward quasi-steady-state point-mass lap. Returns v (m/s), a_long (m/s^2)."""
    n = len(kappa)
    cla = np.where(straight, CLA_STRAIGHT, CLA_CORNER)
    cda = np.where(straight, CDA_STRAIGHT, CDA_CORNER)
    vlim = corner_speed(kappa, cla)
    vlim = np.minimum(vlim, 120.0)
    start = int(np.argmin(vlim))
    order = [(start + i) % n for i in range(n)]

    def grip_left(i: int, v: float) -> float:
        normal = MASS_KG * G + 0.5 * RHO * cla[i] * v * v
        lat = MASS_KG * v * v * abs(kappa[i])
        return math.sqrt(max((MU * normal) ** 2 - lat * lat, 0.0))

    fwd = np.empty(n)
    v = vlim[start]
    for i in order:
        v = min(v, vlim[i])
        fwd[i] = v
        drag = 0.5 * RHO * cda[i] * v * v
        f = min(POWER_W / max(v, 1.0), grip_left(i, v)) - drag
        v = math.sqrt(max(v * v + 2 * f / MASS_KG * ds, 0.0))
    bwd = np.empty(n)
    v = vlim[start]
    for i in reversed(order):
        v = min(v, vlim[i])
        bwd[i] = v
        drag = 0.5 * RHO * cda[i] * v * v
        f = grip_left(i, v) + drag
        v = math.sqrt(v * v + 2 * f / MASS_KG * ds)
    vel = np.minimum(fwd, bwd)
    vel = np.minimum(vel, vlim)
    acc = (np.roll(vel, -1) ** 2 - vel ** 2) / (2 * ds)
    return vel, acc


def runs(mask: np.ndarray):
    """Circular runs of True: list of (start, length)."""
    n = len(mask)
    if mask.all():
        return [(0, n)]
    if not mask.any():
        return []
    first_false = int(np.argmin(mask))
    out, i = [], 0
    while i < n:
        j = (first_false + i) % n
        if mask[j]:
            L = 0
            while L < n and mask[(j + L) % n]:
                L += 1
            out.append((j, L))
            i += L
        else:
            i += 1
    return out


def build_track(code: str, abbr: str, gp: str, short: str) -> dict:
    xy_raw, props = load_xy(code)
    official_len = float(props.get("length") or 0)
    # Smooth periodic spline through the (deduplicated) vertices: the GeoJSON is a
    # polyline with ~40 m between vertices, and differentiating that directly turns
    # every vertex into a fake corner.
    keep = [0]
    for i in range(1, len(xy_raw)):
        if np.hypot(*(xy_raw[i] - xy_raw[keep[-1]])) > 1.0:
            keep.append(i)
    pts = np.vstack([xy_raw[keep], xy_raw[:1]])
    tck, _ = splprep([pts[:, 0], pts[:, 1]], s=len(pts) * SPLINE_TOL_M ** 2, per=True)
    sx, sy = splev(np.linspace(0, 1, 20000, endpoint=False), tck)
    xy = resample_closed(np.column_stack([sx, sy]), STEP_M)
    raw_len = len(xy) * STEP_M
    scale = official_len / raw_len if official_len else 1.0   # match the official lap length
    xy = xy * scale
    ds = STEP_M * scale
    n = len(xy)
    length = n * ds
    kappa = gaussian_filter1d(curvature(xy, ds), KAPPA_SIGMA_M / ds, mode="wrap")

    # pass 1: corner mode everywhere; detect straights
    gentle = np.abs(gaussian_filter1d(kappa, 15.0 / ds, mode="wrap")) < STRAIGHT_MAX_KAPPA
    v1, a1 = lap_sim(kappa, ds, np.zeros(n, bool))
    full_throttle = a1 > -0.5
    cand = gentle & full_throttle
    straight = np.zeros(n, bool)
    for s0, L in runs(cand):
        if L * ds >= STRAIGHT_MIN_LEN_M:
            for k in range(L):
                straight[(s0 + k) % n] = True
    # pass 2: with straight mode; aero closes the moment the car brakes
    v2, a2 = lap_sim(kappa, ds, straight)
    braking = a2 < -2.0
    straight &= ~braking
    v, a = lap_sim(kappa, ds, straight)
    braking = a < -2.0
    lap_time = float(np.sum(ds / np.maximum(v, 1.0)))
    lap_time_corner_only = float(np.sum(ds / np.maximum(v1, 1.0)))
    lat_g = v * v * kappa / G
    steer = np.degrees(np.arctan(WHEELBASE_M * kappa))
    # apexes: local |kappa| maxima tighter than R 250 m, at least 60 m apart
    ak = np.abs(gaussian_filter1d(kappa, 6.0 / ds, mode="wrap"))
    apex_idx = []
    win = max(1, int(30 / ds))
    for i in range(n):
        if ak[i] < 1 / 250.0:
            continue
        lo, hi = i - win, i + win
        neigh = ak[[j % n for j in range(lo, hi + 1)]]
        if ak[i] >= neigh.max() and all(min(abs(i - p), n - abs(i - p)) * ds > 60 for p in apex_idx):
            apex_idx.append(i)
    apex = np.zeros(n, bool)
    apex[apex_idx] = True
    sector = np.minimum((np.arange(n) * 3) // n, 2)

    # --- VCU table: N_POINTS equally spaced ---
    idx = np.floor(np.arange(N_POINTS) * n / N_POINTS).astype(int)
    vmax = float(v.max())
    tbl = []
    for j, i in enumerate(idx):
        seg = [(i + k) % n for k in range(max(1, n // N_POINTS))]
        flags = 0
        if straight[i]:
            flags |= F_STRAIGHT
        if braking[i]:
            flags |= F_BRAKING
        if apex[seg].any():
            flags |= F_APEX
        flags |= int(sector[i]) << F_SECTOR_SHIFT
        st = float(np.clip(steer[i], -STEER_LIMIT_DEG, STEER_LIMIT_DEG))
        tbl.append({
            "steerQ": int(np.clip(round(st * 4), -127, 127)),          # 0.25 deg
            "speedQ": int(np.clip(round(v[i] / vmax * 255), 0, 255)),  # fraction of vmax
            "flags": flags,
            "latgQ": int(np.clip(round(lat_g[i] * 20), -127, 127)),    # 0.05 g
        })

    # --- display polyline + per-point series for the dashboard / backend ---
    didx = np.floor(np.arange(N_DISPLAY) * n / N_DISPLAY).astype(int)
    x0, y0 = xy[:, 0].min(), xy[:, 1].min()
    zones = [{"start": s0 * ds / length, "end": ((s0 + L) % n) * ds / length, "lengthM": round(L * ds, 1)}
             for s0, L in runs(straight)]
    brake_zones = [{"start": s0 * ds / length, "end": ((s0 + L) % n) * ds / length}
                   for s0, L in runs(braking) if L * ds > 15]
    corners = [{"n": k + 1, "pos": round(i * ds / length, 5), "radiusM": round(1 / max(abs(kappa[i]), 1e-6), 1),
                "dir": "L" if kappa[i] > 0 else "R", "speedKmh": round(float(v[i]) * 3.6, 1)}
               for k, i in enumerate(sorted(apex_idx))]
    return {
        "code": abbr, "source": code, "grandPrix": gp, "name": short, "fullName": props.get("Name"),
        "location": props.get("Location"), "lengthM": round(length, 1),
        "model": {
            "lapTimeS": round(lap_time, 3), "lapTimeCornerModeOnlyS": round(lap_time_corner_only, 3),
            "vmaxKmh": round(vmax * 3.6, 1), "vminKmh": round(float(v.min()) * 3.6, 1),
            "maxLatG": round(float(np.abs(lat_g).max()), 2),
            "straightModeFraction": round(float(straight.mean()), 3),
        },
        "polyline": [[round(float(xy[i, 0] - x0), 1), round(float(xy[i, 1] - y0), 1)] for i in didx],
        "series": {
            "speedKmh": [round(float(v[i]) * 3.6, 1) for i in didx],
            "steerDeg": [round(float(np.clip(steer[i], -STEER_LIMIT_DEG, STEER_LIMIT_DEG)), 2) for i in didx],
            "latG": [round(float(lat_g[i]), 3) for i in didx],
            "longG": [round(float(a[i]) / G, 3) for i in didx],
            "kappa": [round(float(kappa[i]), 6) for i in didx],   # 1/m, + = left (twin re-simulation)
            "straight": [bool(straight[i]) for i in didx],
            "braking": [bool(braking[i]) for i in didx],
        },
        "straightZones": zones, "brakeZones": brake_zones, "corners": corners,
        "sectors": [0.0, 1 / 3, 2 / 3],
        "vcuTable": tbl,
    }


def write_header(tracks: list[dict]) -> None:
    L = []
    L.append("// track_maps.h — GENERATED by SOFTWARE/tracks/build_tracks.py. Do not edit by hand.")
    L.append("// Circuit geometry: bacinger/f1-circuits (MIT). Speed/steering/aero: modelled, see the script.")
    L.append("#pragma once")
    L.append("#include <stdint.h>")
    L.append("")
    L.append(f"#define TRACK_POINTS {N_POINTS}")
    L.append(f"#define TRACK_COUNT {len(tracks)}")
    L.append(f"#define TRACK_DEFAULT_ID {next(i for i, t in enumerate(tracks) if t['code'] == DEFAULT_TRACK_CODE)}")
    L.append("#define TRACK_F_STRAIGHT 0x01")
    L.append("#define TRACK_F_BRAKING 0x02")
    L.append("#define TRACK_F_SECTOR_SHIFT 2")
    L.append("#define TRACK_F_APEX 0x10")
    L.append("")
    L.append("// One sample: steering (0.25 deg, + = left), speed (/255 of the track's vmax),")
    L.append("// flags (straight mode, braking, sector, apex), modelled lateral g (0.05 g, + = left).")
    L.append("struct TrackPoint { int8_t steer_q; uint8_t speed_q; uint8_t flags; int8_t latg_q; };")
    L.append("struct TrackInfo { const char *code; const char *name; uint32_t length_m; uint16_t vmax_kmh;")
    L.append("                   uint32_t model_lap_ms; const TrackPoint *pts; };")
    L.append("")
    for k, t in enumerate(tracks):
        L.append(f"static const TrackPoint TRACK_{k}_PTS[TRACK_POINTS] = {{  // {t['code']} {t['name']}")
        row = []
        for p in t["vcuTable"]:
            row.append(f"{{{p['steerQ']},{p['speedQ']},{p['flags']},{p['latgQ']}}}")
            if len(row) == 8:
                L.append("  " + ",".join(row) + ",")
                row = []
        if row:
            L.append("  " + ",".join(row) + ",")
        L.append("};")
    L.append("")
    L.append("static const TrackInfo TRACKS[TRACK_COUNT] = {")
    for k, t in enumerate(tracks):
        L.append(f"  {{\"{t['code']}\", \"{t['name']}\", {int(round(t['lengthM']))}, "
                 f"{int(round(t['model']['vmaxKmh']))}, {int(round(t['model']['lapTimeS'] * 1000))}, TRACK_{k}_PTS}},")
    L.append("};")
    OUT_H.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    tracks = []
    for i, (code, abbr, gp, short) in enumerate(CALENDAR):
        t = build_track(code, abbr, gp, short)
        t["id"] = i
        tracks.append(t)
        m = t["model"]
        print(f"{i:2d} {abbr} {short:22s} {t['lengthM']:7.0f} m  lap {m['lapTimeS']:6.2f} s "
              f"(corner-mode only {m['lapTimeCornerModeOnlyS']:6.2f})  vmax {m['vmaxKmh']:5.1f}  "
              f"vmin {m['vminKmh']:5.1f}  maxLat {m['maxLatG']:.2f} g  straight {m['straightModeFraction']:.0%}  "
              f"corners {len(t['corners'])}")
    doc = {
        "version": 1,
        "generator": "SOFTWARE/tracks/build_tracks.py",
        "defaultTrackId": next(t["id"] for t in tracks if t["code"] == DEFAULT_TRACK_CODE),
        "points": N_POINTS,
        "attribution": "Circuit outlines: bacinger/f1-circuits (MIT). Speeds, g, steering and aero zones are "
                       "MODELLED (illustrative 2026-style car), not official F1 data. Sectors are equal thirds.",
        "vehicleModel": {"massKg": MASS_KG, "powerW": POWER_W, "mu": MU, "claCorner": CLA_CORNER,
                         "cdaCorner": CDA_CORNER, "claStraight": CLA_STRAIGHT, "cdaStraight": CDA_STRAIGHT,
                         "wheelbaseM": WHEELBASE_M, "rho": RHO},
        "tracks": tracks,
    }
    text = json.dumps(doc, separators=(",", ":"))
    OUT_JSON.write_text(text, encoding="utf-8")
    OUT_DASH.parent.mkdir(parents=True, exist_ok=True)
    # the dashboard does not need the VCU table
    dash = dict(doc, tracks=[{k: v for k, v in t.items() if k != "vcuTable"} for t in tracks])
    OUT_DASH.write_text(json.dumps(dash, separators=(",", ":")), encoding="utf-8")
    write_header(tracks)
    print(f"wrote {OUT_JSON.name} ({len(text)//1024} KB), dashboard copy, {OUT_H.name}")


if __name__ == "__main__":
    main()
