"""
ai_engineer.py — the Racing Engineer AI (Stage 2d).

What it is: a deterministic, explainable rule engine plus a digital-twin optimiser
(app/twin.py). No trained model, and it says so. Every conclusion carries the numbers
it was drawn from, so it can be checked in a viva.

What it does, in three layers:

1. LIVE AERO (position-based, every ~200 ms) — `live_aero_recommendation()`.
   The VCU already moves the flaps from its own map. The AI looks AHEAD: it predicts
   where the car will be once its message has crossed LoRa and the servos have moved
   (LOOKAHEAD_S), and recommends the flap angles for THAT point. Near a braking zone
   that means "corner mode now", before the map's own switch. The VCU accepts it only
   in AI-assist mode, only if fresh and confident, and only within ±20° of its own
   baseline — the AI cannot move a flap anywhere the VCU would not.

2. INFERENCES — `analyse()` reads the last ANALYSIS_WINDOW_S of the session and
   reports what the data shows (supply headroom, thermal trend, flap tracking, rear
   motor balance, vibration, link quality, where the car is on the circuit…).

3. SUGGESTIONS — when an inference implies a change the VCU can make, the AI writes a
   proposal: which keys, from what to what, why, the expected effect and the risk.
   It is only a proposal. The operator approves or rejects it; an approved change is
   queued as CONFIG_WRITEs, the VCU validates it, staged changes take effect at the
   next start/finish line, and the ACK is what flips the status to "applied". After
   it is applied the AI compares before/after and reports whether it helped.
"""
from __future__ import annotations

import math
import statistics
import time
from typing import Any

from app import tracks, twin, vcu_config
from app.db import dumps, get_conn, loads, now
from app.services import vehicle_commands

LOOKAHEAD_S = 0.35            # LoRa hop + VCU staleness margin + servo start
ANALYSIS_WINDOW_S = 30.0
ADAPTER_LIMIT_A = 2.2         # the bench supply's rating
MOTOR_LIMIT_C = 80.0          # vcu_logic.h CFG_MOTOR_OVERTEMP_C
REJECT_COOLDOWN_S = 120.0
EXPIRE_PROPOSED_S = 300.0


# =============================================================================
# helpers
# =============================================================================
def _f(v, default=math.nan) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _pct(values: list[float], q: float) -> float:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return math.nan
    return vals[min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))]


def station_settings() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT setup_json FROM configuration WHERE id = 'station'").fetchone()
    s = {"powerSource": "adapter", "adapterLimitA": ADAPTER_LIMIT_A, "lookaheadS": LOOKAHEAD_S}
    if row is not None:
        s.update(loads(row["setup_json"]) or {})
    return s


def save_station_settings(update: dict) -> dict:
    s = station_settings()
    for k in ("powerSource", "adapterLimitA", "lookaheadS"):
        if k in update and update[k] is not None:
            s[k] = update[k]
    if s["powerSource"] not in ("adapter", "lipo"):
        raise ValueError("powerSource must be 'adapter' or 'lipo'")
    s["adapterLimitA"] = float(s["adapterLimitA"])
    s["lookaheadS"] = max(0.0, min(1.5, float(s["lookaheadS"])))
    with get_conn() as conn:
        conn.execute("INSERT INTO configuration (id, setup_json, version, updated_at) VALUES ('station', ?, 1, ?) "
                     "ON CONFLICT(id) DO UPDATE SET setup_json = excluded.setup_json, updated_at = excluded.updated_at",
                     (dumps(s), now()))
    return s


def recent_frames(session_id: str | None, seconds: float = ANALYSIS_WINDOW_S, limit: int = 800) -> list[dict]:
    if not session_id:
        return []
    with get_conn() as conn:
        rows = conn.execute("SELECT frame_json, diagnostics_json FROM telemetry_frames WHERE session_id = ? "
                            "ORDER BY seq DESC LIMIT ?", (session_id, limit)).fetchall()
    frames = []
    for r in rows:
        f = loads(r["frame_json"]) or {}
        f["_diag"] = loads(r["diagnostics_json"]) or {}
        frames.append(f)
    frames.reverse()
    if frames:
        t_end = _f(frames[-1].get("timestamp"), 0.0)
        frames = [f for f in frames if t_end - _f(f.get("timestamp"), 0.0) <= seconds * 1000.0]
    return frames


def _cfg_from(frames: list[dict]) -> tuple[dict, bool]:
    for f in reversed(frames):
        rep = f.get("vcuConfig")
        if isinstance(rep, dict) and rep:
            return vcu_config.effective_config(rep), bool(rep.get("pending"))
    return vcu_config.effective_config(None), False


def _track_of(frame: dict, cfg: dict) -> dict:
    tr = frame.get("track") if isinstance(frame.get("track"), dict) else {}
    tid = tr.get("id", cfg.get("trackId"))
    return tracks.get(tid)


def _rear_kmh(frame: dict) -> float:
    w = frame.get("wheels") if isinstance(frame.get("wheels"), dict) else None
    if not w:
        return _f(frame.get("speed"), 0.0)
    return (_f(w.get("rl"), 0.0) + _f(w.get("rr"), 0.0)) / 2.0


def _flaps_for(track: dict, pos: float, cfg: dict) -> tuple[dict, bool, float]:
    """Exactly the VCU's TrackRunner::command() aero formula, at `pos`."""
    straight = tracks.straight_at(track, pos, cfg["straightOpenDelayM"], cfg["straightCloseEarlyM"])
    front = (cfg["straightFrontCdeg"] if straight else cfg["cornerFrontCdeg"]) / 100.0
    rear = (cfg["straightRearCdeg"] if straight else cfg["cornerRearCdeg"]) / 100.0
    latg = tracks.sample(track, pos)["latG"]
    lat = 0.0 if straight else max(-10.0, min(10.0, latg * cfg["asymGainCdegPerG"] / 100.0))
    clamp = lambda x: max(0.0, min(45.0, x))  # noqa: E731
    return ({"frontLeft": clamp(front - lat), "frontRight": clamp(front + lat),
             "rearLeft": clamp(rear - lat), "rearRight": clamp(rear + lat)}, straight, latg)


def drive_mode(frame: dict | None) -> str | None:
    """FW 2.2: 'dyno' | 'track' | None (car not reporting yet / older firmware)."""
    if not frame:
        return None
    vcu = frame.get("vcu") if isinstance(frame.get("vcu"), dict) else {}
    if vcu.get("driveMode") in ("dyno", "track"):
        return vcu["driveMode"]
    cfg = frame.get("vcuConfig") if isinstance(frame.get("vcuConfig"), dict) else {}
    if "driveMode" in cfg:
        return "track" if int(cfg["driveMode"]) == 1 else "dyno"
    return None


def run_active(frame: dict | None) -> bool:
    if not frame:
        return False
    tr = frame.get("track") if isinstance(frame.get("track"), dict) else {}
    return bool(tr.get("runActive")) or frame.get("runState") in ("track_drive", "bench_laps")


# =============================================================================
# 1. LIVE AERO — position-based, look-ahead
# =============================================================================
def live_aero_recommendation(frame: dict, cfg: dict | None = None, lookahead_s: float | None = None) -> dict | None:
    """Recommendation for the AI_RECOMMENDATION downlink while a track run is active.
    Returns None when the car is not running a circuit (the caller falls back to the
    Stage-2 speed/lateral-g engine)."""
    if not run_active(frame):
        return None
    if cfg is None:
        cfg = vcu_config.effective_config(frame.get("vcuConfig"))
    lookahead_s = LOOKAHEAD_S if lookahead_s is None else lookahead_s
    t = _track_of(frame, cfg)
    tr = frame["track"]
    pos = _f(tr.get("pos"), 0.0)
    lap_len_m = max(1.0, cfg["lapLengthCm"] / 100.0)
    rate = (_rear_kmh(frame) / 3.6) / lap_len_m                  # lap fraction per second on the dyno
    ahead = (pos + rate * lookahead_s) % 1.0
    wing_now, straight_now, _ = _flaps_for(t, pos, cfg)
    wing, straight_ahead, latg = _flaps_for(t, ahead, cfg)
    ev = tracks.next_event(t, pos, cfg["straightOpenDelayM"], cfg["straightCloseEarlyM"])
    corner = tracks.corner_at(t, ahead)
    transition = straight_now != straight_ahead
    if transition and not straight_ahead:
        reason = (f"Look-ahead {lookahead_s:.2f}s: straight mode ends in {ev['inM'] if ev else '?'} m "
                  f"(full scale) — calling corner mode now so the flaps are up before the braking point.")
    elif transition:
        reason = f"Look-ahead {lookahead_s:.2f}s: exit of the corner — opening to straight mode."
    elif straight_ahead:
        reason = f"Straight-mode zone; next event: {ev['type'].replace('_', ' ')} in {ev['inM']} m." if ev \
            else "Straight-mode zone."
    else:
        reason = (f"Corner mode" + (f" through T{corner['n']} ({corner['dir']}, R{corner['radiusM']:.0f} m)" if corner else "")
                  + f"; modelled lateral load {abs(latg):.1f} g -> asymmetric split.")
    confidence = 0.72 if transition else 0.9
    return {
        "type": "AERO_ADVICE", "target": "aero.flaps", "reason": reason, "confidence": confidence,
        "recommendedWing": wing, "corner": (f"T{corner['n']}" if corner else ("Straight" if straight_ahead else "Corner")),
        "grip": round(max(0.0, 1.0 - abs(latg) / 6.0), 3), "recommendedSpeed": tracks.sample(t, ahead)["speedKmh"],
        "efficiency": 100.0 if straight_ahead else 70.0, "racingLine": "follow the map",
        "positionAware": True, "trackCode": t["code"], "pos": round(pos, 5), "posAhead": round(ahead, 5),
        "lookaheadS": lookahead_s, "straightModeAhead": straight_ahead, "transitionAhead": transition,
        "note": "Deterministic look-ahead on the stored circuit map (no trained model). The VCU accepts it only in "
                "AI-assist mode, if fresh and confident, and within ±20° of its own map baseline.",
    }


def live_track_mode_recommendation(frame: dict, cfg: dict | None = None) -> dict | None:
    """FW 2.2, TRACK mode with the driver in DRIVE. There is no map position on the floor, so
    the look-ahead comes from the DRIVER: steering (and yaw rate) lead the lateral load by a
    few hundred ms, so turning in is the earliest sign of a corner. The VCU's own track-mode
    logic switches on lateral g / yaw / steering thresholds; the AI asks for corner mode a
    little earlier and with an asymmetric split estimated from yaw rate x speed, and opens
    straight mode only on a clearly straight, powered run. Same VCU gates as always (AI-assist
    mode, fresh, confident, within ±20° of the VCU's baseline)."""
    if drive_mode(frame) != "track" or frame.get("vcuState") != "DRIVE":
        return None
    if cfg is None:
        cfg = vcu_config.effective_config(frame.get("vcuConfig"))
    steer = _f(frame.get("steering"), 0.0)
    glat = _f(frame.get("gForceLat"), 0.0)
    yaw = _f(frame.get("gyroZ"), 0.0)
    thr = _f(frame.get("throttle"), 0.0)
    brake = _f(frame.get("brake"), 0.0)
    speed = _f(frame.get("speed"), 0.0)
    vcu = frame.get("vcu") if isinstance(frame.get("vcu"), dict) else {}
    tr = frame.get("track") if isinstance(frame.get("track"), dict) else {}
    vcu_straight = bool(vcu.get("aeroStraight") if vcu.get("aeroStraight") is not None else tr.get("straightMode"))
    turning_in = abs(steer) > 4.0 or abs(yaw) > 15.0 or abs(glat) > 0.18 or brake > 0
    straight_ok = speed >= 2.0 and abs(steer) < 2.0 and abs(glat) < 0.08 and abs(yaw) < 8.0 and thr >= 35.0
    want_straight = straight_ok and not turning_in
    # lateral load: measured if it has built up, else predicted from yaw rate x speed (a_y = r * v)
    lat_est = glat if abs(glat) > 0.05 else math.radians(yaw) * (speed / 3.6) / 9.81
    front = (cfg["straightFrontCdeg"] if want_straight else cfg["cornerFrontCdeg"]) / 100.0
    rear = (cfg["straightRearCdeg"] if want_straight else cfg["cornerRearCdeg"]) / 100.0
    lat = 0.0 if want_straight else max(-10.0, min(10.0, lat_est * cfg["asymGainCdegPerG"] / 100.0))
    clamp = lambda x: max(0.0, min(45.0, x))  # noqa: E731
    wing = {"frontLeft": clamp(front - lat), "frontRight": clamp(front + lat),
            "rearLeft": clamp(rear - lat), "rearRight": clamp(rear + lat)}
    if want_straight and not vcu_straight:
        reason = "Straight, powered run (steering < 2°, lateral g < 0.08): open straight mode — low drag."
    elif not want_straight and vcu_straight:
        cue = ("steering %.0f°" % steer) if abs(steer) > 4.0 else ("yaw %.0f °/s" % yaw) if abs(yaw) > 15.0 else \
              ("lateral %.2f g" % glat) if abs(glat) > 0.18 else "braking"
        reason = (f"Turn-in detected ({cue}) before the lateral load builds — calling corner mode now, "
                  f"split for ≈{abs(lat_est):.2f} g {'left' if lat_est > 0 else 'right'}.")
    elif want_straight:
        reason = "Straight mode holds."
    else:
        reason = f"Corner mode, measured lateral {glat:+.2f} g -> asymmetric split {2 * lat:+.1f}° right-minus-left."
    agree = want_straight == vcu_straight
    return {
        "type": "AERO_ADVICE", "target": "aero.flaps", "reason": reason, "confidence": 0.88 if agree else 0.72,
        "recommendedWing": wing, "corner": "Straight" if want_straight else "Medium Corner",
        "grip": round(max(0.0, 1.0 - abs(glat) / 1.5), 3), "recommendedSpeed": speed,
        "efficiency": 100.0 if want_straight else 70.0, "racingLine": "driver",
        "positionAware": False, "trackMode": True, "straightModeAhead": want_straight, "transitionAhead": not agree,
        "note": "Track mode: deterministic anticipation from the driver's steering and yaw rate (no circuit map, "
                "no trained model). The VCU accepts it only in AI-assist mode, fresh, confident and within ±20°.",
    }


# =============================================================================
# 2 + 3. INFERENCES AND SUGGESTIONS
# =============================================================================
def _series(frames: list[dict], getter) -> list[float]:
    out = []
    for f in frames:
        try:
            out.append(_f(getter(f)))
        except (KeyError, TypeError, AttributeError):
            out.append(math.nan)
    return out


def _flap_tracking(frames: list[dict], corner: str) -> dict | None:
    """Steady-state error and response time of one flap with MG996R feedback (the rear pair on the
    current PCB; any channel whose measured value differs from its target is treated as fed back)."""
    ts = _series(frames, lambda f: f["timestamp"])
    meas = _series(frames, lambda f: f["wing"][corner])
    tgt = _series(frames, lambda f: f["wingTarget"][corner])
    if sum(1 for x in tgt if math.isfinite(x)) < 10:
        return None
    # if measured == target everywhere, there is no feedback on this channel (LoRa carries both)
    if all(abs(m - t) < 1e-6 for m, t in zip(meas, tgt) if math.isfinite(m) and math.isfinite(t)):
        return None
    steady_err, responses = [], []
    i = 1
    while i < len(tgt):
        if math.isfinite(tgt[i]) and math.isfinite(tgt[i - 1]) and abs(tgt[i] - tgt[i - 1]) > 8.0:
            t0, goal = ts[i], tgt[i]
            for j in range(i, min(len(tgt), i + 40)):
                if math.isfinite(meas[j]) and abs(meas[j] - goal) < 2.0:
                    responses.append((ts[j] - t0) / 1000.0)
                    break
            i += 3
            continue
        if i >= 3 and all(math.isfinite(tgt[k]) and abs(tgt[k] - tgt[i]) < 0.5 for k in range(i - 3, i + 1)):
            if math.isfinite(meas[i]):
                steady_err.append(abs(meas[i] - tgt[i]))
        i += 1
    return {"steadyErrDeg": round(statistics.mean(steady_err), 2) if steady_err else None,
            "responseS": round(statistics.median(responses), 3) if responses else None,
            "samples": len(steady_err), "transitions": len(responses)}


def analyse(session_id: str | None) -> dict:
    frames = recent_frames(session_id)
    settings = station_settings()
    if not frames:
        return {"available": False, "reason": "no telemetry in this session yet", "insights": [], "proposals": [],
                "settings": settings}
    last = frames[-1]
    cfg, pending = _cfg_from(frames)
    t = _track_of(last, cfg)
    insights: list[dict] = []
    proposals: list[dict] = []

    def insight(iid, sev, cat, title, detail, **metrics):
        insights.append({"id": iid, "severity": sev, "category": cat, "title": title, "detail": detail,
                         "metrics": metrics})

    def propose(kind, title, rationale, changes, expected, risk, evidence, priority=5):
        clean = []
        for name, to in changes:
            k = vcu_config.BY_NAME[name]
            to = int(round(to))
            if to == cfg[name]:
                continue
            clean.append({"key": k.key, "name": name, "label": k.label, "unit": k.unit,
                          "from": k.display(cfg[name]), "to": k.display(to), "wire": to, "apply": k.apply})
        if clean:
            proposals.append({"kind": kind, "title": title, "rationale": rationale, "changes": clean,
                              "expectedEffect": expected, "risk": risk, "evidence": evidence, "priority": priority,
                              "trackId": t["id"]})

    running = run_active(last)
    mode = drive_mode(last)
    vcu = last.get("vcu") if isinstance(last.get("vcu"), dict) else {}
    tr = last.get("track") if isinstance(last.get("track"), dict) else {}
    pos = _f(tr.get("pos"), 0.0)

    # ---------------------------------------------------------- mode + hardware (FW 2.2)
    hw = vcu.get("hwProfile") or {0: "prototype", 1: "pcb_rev_b"}.get(cfg.get("hwProfile"))
    hw_label = {"prototype": "prototype (hobby modules)", "pcb_rev_b": "PCB rev B"}.get(hw, "unknown hardware")
    if mode == "track":
        insight("mode", "info", "mode", f"TRACK mode on the {hw_label}",
                "The car is on the floor: only a driver holding ARM can move it. Flaps switch straight/corner from "
                "the IMU and steering, traction control "
                + ("is on" if cfg.get("tcSlipPermille") else "is OFF") + "; circuit runs are refused by the VCU.",
                hardware=hw)
    elif mode == "dyno":
        link = vcu.get("groundLinkOk")
        insight("mode", "warning" if link is False else "info", "mode", f"DYNO mode on the {hw_label}",
                "The car must be on the rollers: circuit runs and dyno calibration can be started from the dashboard "
                "or the console. "
                + ("The VCU does NOT see the ground link, so it will refuse a dashboard run (the link is the deadman)."
                   if link is False else "A dashboard run stops by itself if the ground link drops for 1 s."),
                hardware=hw)
    if hw == "pcb_rev_b":
        dead = [f for f in frames[-40:] if _f(f.get("throttle"), 0) > 5 and
                isinstance(f.get("vcu"), dict) and f["vcu"].get("motorPower") is False]
        if len(dead) > 10:
            insight("kill_switch", "warning", "powertrain", "Motor duty commanded with the kill switch off",
                    "The VCU reports duty but the BTS7004 is not enabled. Check GPIO19 -> R4 -> BTS7004 IN and that "
                    "the firmware build is the PCB rev B profile.", frames=len(dead))

    # ---------------------------------------------------------- where is the car
    if mode == "track":
        sl = _series(frames, lambda f: f["vcu"]["aeroStraight"] if f.get("vcu", {}).get("aeroStraight") is not None
                     else f["track"]["straightMode"])
        mv = [x for x, f in zip(sl, frames) if math.isfinite(x) and _f(f.get("speed"), 0) > 1.0]
        share = (sum(mv) / len(mv) * 100) if mv else 0.0
        insight("position", "info", "track", "Free driving (track mode) — no circuit map position",
                f"Straight mode was open {share:.0f}% of the moving time in the last {ANALYSIS_WINDOW_S:.0f} s. "
                "Position on a stored circuit is a dyno feature.", straightSharePct=round(share))
    elif running:
        smp = tracks.sample(t, pos)
        corner = tracks.corner_at(t, pos)
        ev = tracks.next_event(t, pos, cfg["straightOpenDelayM"], cfg["straightCloseEarlyM"])
        where = (f"T{corner['n']} ({'left' if corner['dir'] == 'L' else 'right'}, R{corner['radiusM']:.0f} m)"
                 if corner else ("a straight-mode zone" if smp["straight"] else "a corner-mode section"))
        insight("position", "info", "track", f"{t['name']}: lap {tr.get('lap', 0) + 1}, {pos * 100:.0f}% round, in {where}",
                f"Full-scale model here: {smp['speedKmh']:.0f} km/h, {smp['latG']:+.1f} g lateral, "
                f"{smp['longG']:+.1f} g longitudinal. "
                + (f"Next: {ev['type'].replace('_', ' ')} in {ev['inM']:.0f} m." if ev else ""),
                pos=pos, sector=smp["sector"] + 1, twinSpeedKmh=smp["speedKmh"])
    else:
        insight("position", "info", "track", f"Car not running a circuit (run state: {last.get('runState', 'off')})",
                f"Selected circuit: {t['name']} ({t['lengthM'] / 1000:.3f} km). Start a run from the dashboard "
                "(Track Map → Start run), with `lap run` on the VCU console, or hold ARM with track run enabled.")
    if pending:
        insight("pending", "info", "config", "Settings staged on the VCU",
                "Approved changes are waiting for the next start/finish crossing.")

    # ---------------------------------------------------------- electrical
    cur = _series(frames, lambda f: f["current"])
    volt = _series(frames, lambda f: f["voltage"])
    p90_i = _pct(cur, 0.9)
    max_i = _pct(cur, 1.0)
    min_v, med_v = _pct(volt, 0.0), _pct(volt, 0.5)
    if settings["powerSource"] == "adapter" and math.isfinite(p90_i):
        lim = float(settings["adapterLimitA"])
        head = p90_i / lim * 100.0
        sev = "critical" if max_i >= lim else ("warning" if head > 85 else "info")
        insight("supply", sev, "power", f"Supply at {head:.0f}% of the {lim:.1f} A adapter (90th percentile)",
                f"Peak {max_i:.2f} A, median voltage {med_v:.2f} V, lowest {min_v:.2f} V over the last "
                f"{ANALYSIS_WINDOW_S:.0f} s.", p90A=round(p90_i, 3), peakA=round(max_i, 3), minV=round(min_v, 3))
        if (head > 88 or (math.isfinite(min_v) and min_v < 10.8)) and running:
            new_cap = max(cfg["dutyFloorPermille"] + 100, int(cfg["dutyCapPermille"] * 0.88 / 10) * 10)
            propose("duty_cap_supply", "Lower the motor duty cap to stay inside the adapter",
                    f"The 90th-percentile current is {p90_i:.2f} A against a {lim:.1f} A supply"
                    + (f" and the voltage dipped to {min_v:.2f} V" if min_v < 10.8 else "")
                    + ". Motor current rises roughly with duty, so trimming the top of the speed profile removes the "
                      "peaks without touching the corners.",
                    [("dutyCapPermille", new_cap)],
                    f"≈{(1 - new_cap / cfg['dutyCapPermille']) * 100:.0f}% lower peak motor current; slower top "
                    "speed on the dyno (the circuit shape is unchanged).",
                    "Dyno lap time increases. Does not change aero behaviour.",
                    {"p90A": round(p90_i, 3), "minV": round(min_v, 3), "limitA": lim}, priority=2)
    # supply resistance from load steps
    pairs = [(i, v) for i, v in zip(cur, volt) if math.isfinite(i) and math.isfinite(v)]
    if len(pairs) > 20:
        i_vals = [p[0] for p in pairs]
        if max(i_vals) - min(i_vals) > 0.4:
            mi, mv = statistics.mean(i_vals), statistics.mean(p[1] for p in pairs)
            cov = sum((i - mi) * (v - mv) for i, v in pairs)
            var = sum((i - mi) ** 2 for i in i_vals)
            r = -cov / var if var > 0 else math.nan
            if math.isfinite(r) and 0.0 < r < 5.0:
                insight("supply_r", "warning" if r > 0.6 else "info", "power",
                        f"Supply + wiring resistance ≈ {r:.2f} Ω",
                        "From the slope of voltage against current (V = V0 − I·R). "
                        + ("High: check the adapter lead, fuse holder and the INA260 terminals." if r > 0.6
                           else "Normal for a bench adapter and fused wiring."), ohm=round(r, 3))

    # ---------------------------------------------------------- thermal
    ts = _series(frames, lambda f: f["timestamp"])
    mt = _series(frames, lambda f: f["motorTemp"])
    good = [(t_, m) for t_, m in zip(ts, mt) if math.isfinite(t_) and math.isfinite(m) and m > 1.0]
    if len(good) > 10 and good[-1][0] - good[0][0] > 8000:
        rate = (good[-1][1] - good[0][1]) / ((good[-1][0] - good[0][0]) / 1000.0)
        temp = good[-1][1]
        tt = (MOTOR_LIMIT_C - temp) / rate if rate > 0.002 else math.inf
        if tt < math.inf:
            sev = "critical" if tt < 180 else ("warning" if tt < 600 else "info")
            insight("thermal", sev, "thermal", f"Motor {temp:.1f} °C, rising {rate * 60:.1f} °C/min",
                    f"At this trend it reaches the {MOTOR_LIMIT_C:.0f} °C limit in about {tt / 60:.1f} min.",
                    tempC=temp, rateCPerMin=round(rate * 60, 2), secondsToLimit=round(tt))
            if tt < 600 and running:
                new_cap = max(cfg["dutyFloorPermille"] + 100, int(cfg["dutyCapPermille"] * 0.85 / 10) * 10)
                propose("duty_cap_thermal", "Reduce motor duty cap — motor heating too fast",
                        f"Motor temperature is rising {rate * 60:.1f} °C/min and is projected to hit "
                        f"{MOTOR_LIMIT_C:.0f} °C in {tt / 60:.1f} min, which would fault the VCU mid-run.",
                        [("dutyCapPermille", new_cap)],
                        "Lower copper losses (∝ current²); the temperature trend should flatten within a lap.",
                        "Slower dyno laps.", {"rateCPerMin": round(rate * 60, 2), "secondsToLimit": round(tt)},
                        priority=1)
        else:
            insight("thermal", "info", "thermal", f"Motor {temp:.1f} °C, stable", "No warming trend in the window.",
                    tempC=temp)

    # ---------------------------------------------------------- flaps
    slowest = None
    for corner_name, label in (("rearLeft", "RL"), ("rearRight", "RR"), ("frontLeft", "FL"), ("frontRight", "FR")):
        tk = _flap_tracking(frames, corner_name)
        if tk is None:
            continue
        err, resp = tk["steadyErrDeg"], tk["responseS"]
        sev = "warning" if (err is not None and err > 3.0) or (resp is not None and resp > 0.45) else "info"
        insight(f"flap_{label}", sev, "aero", f"Flap {label}: steady error {err if err is not None else '—'}°, "
                f"response {resp if resp is not None else '—'} s",
                "Measured by the MG996R pot feedback against the VCU's rate-limited target. "
                + ("A steady error above 3° points at a binding linkage or a stale feedback calibration "
                   "(re-run `cal`)." if err is not None and err > 3.0 else ""), **tk)
        if resp is not None:
            slowest = max(slowest or 0.0, resp)
    if running and t["straightZones"]:
        delta = abs(cfg["cornerFrontCdeg"] - cfg["straightFrontCdeg"]) / 100.0
        t_move = slowest if slowest is not None else delta / max(1.0, cfg["wingSlewDps"])
        # full-scale speed at the end of each straight-mode zone (= braking point), for the explanation
        sim = twin.simulate_config(t["id"], cfg)
        n = len(sim["speedKmh"])
        v_brake = max(sim["speedKmh"][int(z["end"] * n) % n] for z in t["straightZones"]) / 3.6
        # What matters physically is how far the MAP advances while the servos move: on the
        # dyno the car covers v_dyno * t_move metres of a lap_len-metre lap, i.e. that
        # fraction of the circuit. At the braking points the profile is at the duty cap, so
        # v_dyno is the top rear-wheel speed (dyno cal value, else the fastest seen).
        rears = [x for x in _series(frames, lambda f: (f["wheels"]["rl"] + f["wheels"]["rr"]) / 2) if math.isfinite(x)]
        v_dyno = (cfg["dynoVmaxCkmh"] / 100.0 / 3.6) if cfg["dynoVmaxCkmh"] else (max(rears) / 3.6 if rears else 0.0)
        need_m = v_dyno * t_move / max(1.0, cfg["lapLengthCm"] / 100.0) * t["lengthM"]
        need_m = int(math.ceil(need_m / 5.0) * 5)
        if v_dyno > 0 and need_m > cfg["straightCloseEarlyM"] + 10 and need_m <= 400:
            propose("close_early_servo_lag", "Close straight mode earlier to beat servo travel time",
                    f"The flaps need {t_move:.2f} s to travel {delta:.0f}° "
                    + ("(measured on the rear MG996R pair)" if slowest is not None else f"(at {cfg['wingSlewDps']} °/s)")
                    + f". At the braking points the car is at top speed ({v_dyno * 3.6:.2f} km/h on the dyno, "
                    f"{v_brake * 3.6:.0f} km/h at full scale), so during that time the map advances ≈{need_m} m of the "
                    "circuit. Closing that much earlier puts the flaps in corner mode when braking starts.",
                    [("straightCloseEarlyM", need_m)],
                    f"Flaps in corner mode at every braking point. Twin cost: "
                    f"{twin.simulate(t['id'], cfg['straightFrontCdeg'] / 100, cfg['straightRearCdeg'] / 100, cfg['cornerFrontCdeg'] / 100, cfg['cornerRearCdeg'] / 100, float(cfg['straightOpenDelayM']), float(need_m))['lapTimeS'] - sim['lapTimeS']:+.3f} s/lap.",
                    "Slightly shorter low-drag zones.", {"transitionS": round(t_move, 3), "vDynoKmh": round(v_dyno * 3.6, 2),
                                                          "vBrakeKmh": round(v_brake * 3.6), "neededM": need_m},
                    priority=3)

    # ---------------------------------------------------------- aero strategy (twin)
    if mode != "track" and (running or last.get("vcuConfig")):
        opt = twin.optimise_aero(t["id"], cfg)
        if opt and opt["gainS"] >= 0.1:
            propose("aero_optimise", f"Retune the flap angles for {t['name']}",
                    f"The digital twin re-ran the lap for {7 * 7 * 4} flap combinations. Downforce saturates as a flap "
                    f"stalls but drag keeps rising, so the best corner-mode angle depends on the circuit: here "
                    f"{opt['cornerFrontDeg']:.0f}°/{opt['cornerRearDeg']:.0f}° corner and {opt['straightDeg']:.1f}° "
                    f"straight beats the current {cfg['cornerFrontCdeg'] / 100:.0f}°/{cfg['cornerRearCdeg'] / 100:.0f}° "
                    f"and {cfg['straightFrontCdeg'] / 100:.1f}°.",
                    [("straightFrontCdeg", opt["straightDeg"] * 100), ("straightRearCdeg", opt["straightDeg"] * 100),
                     ("cornerFrontCdeg", opt["cornerFrontDeg"] * 100), ("cornerRearCdeg", opt["cornerRearDeg"] * 100)],
                    f"Twin lap {opt['baselineLapS']:.2f} s → {opt['bestLapS']:.2f} s ({-opt['gainS']:+.2f} s, full scale). "
                    "The dyno lap time will NOT change — the rollers do not feel downforce; the flaps will visibly "
                    "take the new angles at every zone.",
                    "Larger flap travel per switch = more servo current at each transition.",
                    {"twinBaselineS": opt["baselineLapS"], "twinBestS": opt["bestLapS"]}, priority=4)

    # ---------------------------------------------------------- drivetrain
    wl = _series(frames, lambda f: f["wheels"]["rl"])
    wr = _series(frames, lambda f: f["wheels"]["rr"])
    wf = _series(frames, lambda f: (f["wheels"]["fl"] + f["wheels"]["fr"]) / 2)
    moving = [(a, b) for a, b in zip(wl, wr) if math.isfinite(a) and math.isfinite(b) and (a + b) / 2 > 0.8]
    if len(moving) > 10:
        rl_m, rr_m = statistics.mean(a for a, _ in moving), statistics.mean(b for _, b in moving)
        imb = (rl_m - rr_m) / ((rl_m + rr_m) / 2) * 100
        if abs(imb) > 6:
            slow = "RL" if imb < 0 else "RR"
            insight("rear_balance", "warning", "drivetrain", f"Rear wheels unbalanced: {slow} {abs(imb):.0f}% slower",
                    "Both Johnson motors share one PWM (ENA+ENB tied), so a steady difference means one motor or its "
                    "roller has more drag — check brushes, bearings, the roller contact and the motor wiring.",
                    rlKmh=round(rl_m, 2), rrKmh=round(rr_m, 2), imbalancePct=round(imb, 1))
        front_m = statistics.mean(x for x in wf if math.isfinite(x)) if any(math.isfinite(x) for x in wf) else 0
        if front_m < 0.2 and mode != "track":
            insight("dyno_fronts", "info", "dyno", "Front wheels are not turning on the dyno",
                    "Vehicle speed is taken from the driven rear pair; wheel slip cannot be measured on this rig.")
    stalled = any((f.get("track") or {}).get("stalled") for f in frames[-15:])
    if stalled and mode == "track":
        insight("stall_track", "warning", "powertrain", "Motor stall while driving",
                "Throttle was applied but the rear wheels did not turn for 2.5 s (blocked wheel, wall contact or a "
                "drivetrain jam). The VCU locked the motor out until the throttle was released.")
    elif stalled or cfg["dynoStartPermille"] and cfg["dutyFloorPermille"] < cfg["dynoStartPermille"] + 30:
        start = cfg["dynoStartPermille"] or cfg["dutyFloorPermille"]
        new_floor = min(800, max(cfg["dutyFloorPermille"] + 50, start + 50))
        if new_floor < cfg["dutyCapPermille"]:
            propose("duty_floor", "Raise the motor duty floor so the slow corners never stall",
                    ("The stall watchdog tripped: the rear wheels stopped while duty was applied." if stalled else
                     f"`dyno cal` found the wheels only break away at {start / 10:.0f}% duty, but the floor is "
                     f"{cfg['dutyFloorPermille'] / 10:.0f}%."),
                    [("dutyFloorPermille", new_floor)],
                    "The car keeps rolling through the hairpins; the speed trace keeps the circuit's shape.",
                    "Slow corners are relatively faster on the dyno.", {"startPermille": start}, priority=2)

    # ---------------------------------------------------------- traction + measured cornering (track mode)
    if mode == "track":
        drive = [f for f in frames if f.get("vcuState") == "DRIVE" and _f(f.get("speed"), 0) > 1.0]
        if len(drive) > 15:
            tc_on = [f for f in drive if isinstance(f.get("vcu"), dict) and f["vcu"].get("tcActive")]
            slips = [_f((f.get("derived") or {}).get("slipRatio")) for f in drive]
            slips = [x for x in slips if math.isfinite(x)]
            p90_slip = _pct(slips, 0.9) if slips else math.nan
            share = len(tc_on) / len(drive) * 100
            target = cfg.get("tcSlipPermille", 250)
            if target and share > 25:
                insight("traction", "warning", "drivetrain", f"Traction control cutting power {share:.0f}% of the time",
                        f"The rear wheels spin more than {target / 10:.0f}% over the fronts often. Less power is "
                        "faster here: the car is grip-limited, not power-limited.", tcSharePct=round(share),
                        p90Slip=round(p90_slip, 3) if math.isfinite(p90_slip) else None)
                new_cap = max(cfg["dutyFloorPermille"] + 100, int(cfg["dutyCapPermille"] * 0.9 / 10) * 10)
                propose("tc_power", "Lower the duty cap — the rear wheels are grip-limited",
                        f"Traction control intervened in {share:.0f}% of the driving frames. Each cut costs drive; a "
                        "10 % lower duty cap keeps the wheels just below the slip target instead.",
                        [("dutyCapPermille", new_cap)], "Fewer TC interventions, smoother drive out of corners.",
                        "Lower top speed on long straights.", {"tcSharePct": round(share)}, priority=3)
            elif not target and math.isfinite(p90_slip) and p90_slip > 0.3:
                insight("traction", "warning", "drivetrain", f"Wheelspin with traction control OFF (90th pct slip "
                        f"{p90_slip * 100:.0f}%)", "The rears regularly turn much faster than the fronts.",
                        p90Slip=round(p90_slip, 3))
                propose("tc_enable", "Turn traction control on", "Measured wheelspin is high and TC is off.",
                        [("tcSlipPermille", 250)], "Duty is cut while the rears spin > 25 % faster than the fronts.",
                        "None on the dyno (TC needs the front wheels turning).", {"p90Slip": round(p90_slip, 3)},
                        priority=2)
            else:
                insight("traction", "info", "drivetrain", f"Traction control {'on' if target else 'off'}, intervening "
                        f"{share:.0f}% of driving", "No sustained wheelspin.", tcSharePct=round(share))
            glat = [abs(_f(f.get("gForceLat"))) for f in drive]
            glat = [x for x in glat if math.isfinite(x)]
            p90_g = _pct(glat, 0.9) if glat else math.nan
            if math.isfinite(p90_g) and p90_g > 0.2:
                # aim: the 90th-percentile corner uses ~70 % of the ±10° asymmetric range
                gain = int(round(min(500, max(50, 7.0 / p90_g * 100)) / 10) * 10)
                insight("cornering_g", "info", "aero", f"Measured cornering load: 90th percentile {p90_g:.2f} g",
                        f"With the asymmetric gain at {cfg['asymGainCdegPerG'] / 100:.1f}°/g the split reaches "
                        f"{min(10, p90_g * cfg['asymGainCdegPerG'] / 100):.1f}° of the ±10° available.", p90G=round(p90_g, 3))
                if abs(gain - cfg["asymGainCdegPerG"]) >= 50:
                    propose("asym_gain_measured", "Match the asymmetric flap gain to the measured cornering load",
                            f"In track mode the left/right split comes from MEASURED lateral g. The car corners at up to "
                            f"≈{p90_g:.2f} g (90th percentile), so {gain / 100:.1f}°/g uses ~70 % of the ±10° range "
                            "in the hard corners without saturating.",
                            [("asymGainCdegPerG", gain)], "More (or less) load on the outside flaps in corners.",
                            "Bigger flap moves in corners = more servo current.", {"p90G": round(p90_g, 3)}, priority=4)

    # ---------------------------------------------------------- vibration
    vib = _series(frames, lambda f: f["vibrationMg"])
    spd = _series(frames, lambda f: f["speed"])
    vp = [(s, v) for s, v in zip(spd, vib) if math.isfinite(s) and math.isfinite(v)]
    if len(vp) > 15:
        v_hi = [v for s, v in vp if s > _pct([s for s, _ in vp], 0.7)]
        v_lo = [v for s, v in vp if s <= _pct([s for s, _ in vp], 0.3)]
        if v_hi and v_lo:
            hi, lo = statistics.mean(v_hi), statistics.mean(v_lo)
            if hi > 120 and hi > 1.8 * max(lo, 1.0):
                insight("vibration", "warning", "chassis", f"Vibration rises with speed: {lo:.0f} → {hi:.0f} mg RMS",
                        "Speed-dependent vibration on a dyno is usually a wheel/roller imbalance or a loose motor mount. "
                        "It also corrupts the IMU — fix it before trusting the g readings.", lowMg=round(lo), highMg=round(hi))
            else:
                insight("vibration", "info", "chassis", f"Vibration {statistics.mean(v for _, v in vp):.0f} mg RMS",
                        "No speed-dependent growth.")

    # ---------------------------------------------------------- dyno lap length
    last_lap = _f(tr.get("lastLapMs"), 0.0) / 1000.0
    if mode != "track" and (last_lap > 150 or 0 < last_lap < 30):
        target = 75.0
        new_len = int(round(cfg["lapLengthCm"] * target / last_lap / 100.0)) * 100
        new_len = max(500, min(100000, new_len))
        propose("lap_length", "Resize the dyno lap for a watchable demo",
                f"The last dyno lap took {last_lap:.0f} s. A {target:.0f} s lap keeps the whole circuit on screen during "
                "a demo without the car sitting on one straight.", [("lapLengthCm", new_len)],
                f"≈{target:.0f} s per lap at the current speed profile.",
                "Applies only when the car is stopped (the VCU rejects it mid-run).", {"lastLapS": last_lap}, priority=6)

    # ---------------------------------------------------------- link + AI acceptance
    diag = last.get("_diag") or {}
    loss = _f(diag.get("packetLoss"))
    rssi = _f((diag.get("loraLink") or {}).get("rssi"))
    if math.isfinite(loss) and diag.get("lora") is not None:
        sev = "warning" if loss > 10 or (math.isfinite(rssi) and rssi < -110 and rssi != 0) else "info"
        insight("link", sev, "comms", f"LoRa: {loss:.0f}% packet loss" + (f", RSSI {rssi:.0f} dBm" if rssi else ""),
                "Above ~10 % the AI's look-ahead messages arrive late and the VCU drops them as stale; lower "
                "--lora-uplink-hz on the Pi or move the antennas." if sev == "warning" else "Link healthy.",
                lossPct=loss, rssi=rssi)
    reasons = [f.get("lastAiReason") for f in frames if f.get("lastAiReason")]
    if reasons:
        acc = sum(1 for r in reasons if r == "accepted") / len(reasons) * 100
        top = max(set(reasons), key=reasons.count)
        insight("ai_accept", "info" if acc > 50 or top in ("ai_mode_disabled", "none_received") else "warning", "ai",
                f"VCU accepted {acc:.0f}% of AI aero messages (most common: {top.replace('_', ' ')})",
                "The VCU is the authority: 'ai mode disabled' means aero mode is baseline; 'stale' means the radio "
                "hop took longer than 500 ms.", acceptedPct=round(acc), mostCommon=top)

    return {"available": True, "sessionId": session_id, "trackId": t["id"], "trackCode": t["code"],
            "running": running, "driveMode": mode, "hwProfile": hw, "config": cfg, "configPending": pending, "settings": settings,
            "insights": insights, "proposals": sorted(proposals, key=lambda p: p["priority"]),
            "frameCount": len(frames)}


# =============================================================================
# suggestion store
# =============================================================================
def _row(r) -> dict:
    d = dict(r)
    d["body"] = loads(d.pop("body_json"))
    d["outcome"] = loads(d.pop("outcome_json"))
    d["commands"] = vehicle_commands.for_suggestion(d["id"])
    return d


def list_suggestions(limit: int = 40) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM ai_suggestions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def get_suggestion(sid: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM ai_suggestions WHERE id = ?", (sid,)).fetchone()
    return _row(r) if r else None


def _metrics_snapshot(session_id: str | None, cfg: dict, track_id: int) -> dict:
    frames = recent_frames(session_id, seconds=20)
    cur = _series(frames, lambda f: f["current"])
    mt = _series(frames, lambda f: f["motorTemp"])
    lap = next(((f.get("track") or {}).get("lastLapMs") for f in reversed(frames) if f.get("track")), None)
    laps = next(((f.get("track") or {}).get("lap") for f in reversed(frames) if f.get("track")), None)
    return {"t": time.time(), "p90CurrentA": round(_pct(cur, 0.9), 3) if frames else None,
            "motorTempC": round(_pct(mt, 1.0), 2) if frames else None,
            "dynoLapS": round(lap / 1000.0, 2) if lap else None, "lap": laps,
            "twinLapS": twin.simulate_config(track_id, cfg)["lapTimeS"]}


def sync_proposals(analysis: dict) -> None:
    """Persist new proposals (one open per kind), expire stale ones."""
    if not analysis.get("available"):
        return
    kinds_now = {p["kind"] for p in analysis["proposals"]}
    t_now = now()
    with get_conn() as conn:
        open_rows = {r["kind"]: r for r in conn.execute(
            "SELECT * FROM ai_suggestions WHERE status = 'proposed'").fetchall()}
        for kind, r in open_rows.items():
            if kind not in kinds_now and t_now - r["created_at"] > EXPIRE_PROPOSED_S:
                conn.execute("UPDATE ai_suggestions SET status = 'expired', updated_at = ? WHERE id = ?", (t_now, r["id"]))
        for p in analysis["proposals"]:
            if p["kind"] in open_rows:
                row = open_rows[p["kind"]]
                body = loads(row["body_json"])
                if [c["wire"] for c in body["changes"]] != [c["wire"] for c in p["changes"]]:
                    conn.execute("UPDATE ai_suggestions SET body_json = ?, title = ?, updated_at = ? WHERE id = ?",
                                 (dumps(p), p["title"], t_now, row["id"]))
                continue
            recent = conn.execute(
                "SELECT status, updated_at, body_json FROM ai_suggestions WHERE kind = ? ORDER BY id DESC LIMIT 1",
                (p["kind"],)).fetchone()
            if recent is not None:
                same = [c["wire"] for c in loads(recent["body_json"])["changes"]] == [c["wire"] for c in p["changes"]]
                if recent["status"] == "rejected" and t_now - recent["updated_at"] < REJECT_COOLDOWN_S:
                    continue
                if recent["status"] in ("approved", "applied") and same:
                    continue  # already done / in flight
            conn.execute("INSERT INTO ai_suggestions (kind, created_at, updated_at, status, session_id, track_id, title, "
                         "body_json) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)",
                         (p["kind"], t_now, t_now, analysis.get("sessionId"), p["trackId"], p["title"], dumps(p)))


def approve(sid: int) -> dict:
    s = get_suggestion(sid)
    if s is None:
        raise KeyError(sid)
    if s["status"] != "proposed":
        raise ValueError(f"suggestion is {s['status']}, not proposed")
    latest = recent_frames(s["session_id"], seconds=5)
    cfg, _ = _cfg_from(latest) if latest else (vcu_config.effective_config(None), False)
    trial = dict(cfg)
    for c in s["body"]["changes"]:
        trial[c["name"]] = c["wire"]
    for c in s["body"]["changes"]:
        err = vcu_config.validate(c["key"], c["wire"], trial)
        if err:
            raise ValueError(err)
    before = _metrics_snapshot(s["session_id"], cfg, s["track_id"] if s["track_id"] is not None else cfg["trackId"])
    for c in s["body"]["changes"]:
        vehicle_commands.queue(c["key"], c["wire"], origin=f"ai:{sid}", suggestion_id=sid)
    with get_conn() as conn:
        conn.execute("UPDATE ai_suggestions SET status = 'approved', updated_at = ?, outcome_json = ? WHERE id = ?",
                     (now(), dumps({"before": before}), sid))
    return get_suggestion(sid)


def reject(sid: int) -> dict:
    s = get_suggestion(sid)
    if s is None:
        raise KeyError(sid)
    if s["status"] != "proposed":
        raise ValueError(f"suggestion is {s['status']}, not proposed")
    with get_conn() as conn:
        conn.execute("UPDATE ai_suggestions SET status = 'rejected', updated_at = ? WHERE id = ?", (now(), sid))
    return get_suggestion(sid)


def update_lifecycle(session_id: str | None) -> None:
    """approved -> applied (every command ACKed/confirmed) | failed; then evaluate."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM ai_suggestions WHERE status IN ('approved','applied')").fetchall()
    for r in rows:
        s = _row(r)
        cmds = s["commands"]
        outcome = s["outcome"] or {}
        if s["status"] == "approved" and cmds:
            if any(c["status"].startswith("rejected") or c["status"] == "expired" for c in cmds):
                bad = [c for c in cmds if c["status"].startswith("rejected") or c["status"] == "expired"]
                outcome["failure"] = "; ".join(f"{c['label']}: {c['status'].replace('_', ' ')}" for c in bad)
                with get_conn() as conn:
                    conn.execute("UPDATE ai_suggestions SET status = 'failed', updated_at = ?, outcome_json = ? "
                                 "WHERE id = ?", (now(), dumps(outcome), s["id"]))
            elif all(c["status"] == "applied" for c in cmds):
                outcome["appliedAt"] = time.time()
                with get_conn() as conn:
                    conn.execute("UPDATE ai_suggestions SET status = 'applied', updated_at = ?, outcome_json = ? "
                                 "WHERE id = ?", (now(), dumps(outcome), s["id"]))
        elif s["status"] == "applied" and "after" not in outcome and outcome.get("appliedAt"):
            frames = recent_frames(session_id, seconds=20)
            cfg, _ = _cfg_from(frames)
            before = outcome.get("before") or {}
            lap_now = next(((f.get("track") or {}).get("lap") for f in reversed(frames) if f.get("track")), None)
            waited = time.time() - outcome["appliedAt"]
            lap_done = before.get("lap") is not None and lap_now is not None and lap_now > before["lap"] + 1
            if lap_done or waited > 45:
                after = _metrics_snapshot(session_id, cfg, s["track_id"] if s["track_id"] is not None else cfg["trackId"])
                outcome["after"] = after
                outcome["verdict"] = _verdict(s["kind"], before, after)
                with get_conn() as conn:
                    conn.execute("UPDATE ai_suggestions SET outcome_json = ?, updated_at = ? WHERE id = ?",
                                 (dumps(outcome), now(), s["id"]))


def _verdict(kind: str, before: dict, after: dict) -> str:
    def d(k):
        a, b = after.get(k), before.get(k)
        return None if a is None or b is None else a - b
    if kind == "aero_optimise":
        x = d("twinLapS")
        return f"Twin lap {x:+.2f} s (full scale). Flap angles now follow the new settings." if x is not None else "Applied."
    if kind in ("duty_cap_supply", "duty_cap_thermal"):
        parts = []
        if d("p90CurrentA") is not None:
            parts.append(f"90th-percentile current {d('p90CurrentA'):+.2f} A")
        if d("motorTempC") is not None:
            parts.append(f"motor temperature {d('motorTempC'):+.1f} °C")
        if d("dynoLapS") is not None:
            parts.append(f"dyno lap {d('dynoLapS'):+.1f} s")
        ok = (d("p90CurrentA") or 0) < 0
        return ("Helped: " if ok else "No clear improvement yet: ") + ", ".join(parts) if parts else "Applied."
    if kind == "lap_length" and d("dynoLapS") is not None:
        return f"Dyno lap {before.get('dynoLapS')} s → {after.get('dynoLapS')} s."
    return "Applied; no before/after metric defined for this change."
