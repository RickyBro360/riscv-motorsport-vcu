"""
vehicle.py — Stage 2d endpoints for the circuit maps, the digital twin, the VCU's
tunables and the outbox of operator-approved changes.

  GET  /api/tracks                     circuit list (2026 calendar)
  GET  /api/tracks/{id}                one circuit, full series (polyline, speed, g, zones)
  GET  /api/vehicle/config             VCU tunables: reported value, bounds, apply rule
  POST /api/vehicle/config             operator write {key|name, value (display units)}
  POST /api/vehicle/track              select a circuit {trackId}  (VCU accepts only when stopped)
  GET  /api/vehicle/commands           recent commands and their fate
  GET  /api/vehicle/commands/next      [gateway] what to transmit now
  GET  /api/twin                       twin lap + modelled forces at the car's position
  GET  /api/laps                       per-lap table for the session
  GET/POST /api/station/settings       power source (adapter / LiPo), adapter limit, AI look-ahead
  FW 2.2 — the car's operating controls. Each is a REQUEST queued as a CONFIG_WRITE; the VCU
  decides against its live state and answers CONFIG_ACK (rejected_state = not allowed now):
  GET  /api/vehicle/status             drive mode, hardware, state, run, link — one object for the UI
  POST /api/vehicle/mode               {"mode": "dyno" | "track"}       only with the car stopped
  POST /api/vehicle/run                {"action": "start" | "stop" | "dyno_cal", "laps"?: 0..99}
  POST /api/vehicle/estop              {"action": "trigger" | "reset"}
  FW 2.3:
  POST /api/vehicle/motor              {"dutyPct": 0..100}   0 = stop. Any drive mode; IDLE, no fault, link up
  POST /api/vehicle/steering/center    knob (steering wheel) back to straight ahead
  POST /api/vehicle/demo               {"action": "start" | "stop"}   (FW 2.3.4)
  POST /api/vehicle/drs                {"action": "open" | "cancel"}  (FW 2.3.3)
  GET  /api/vehicle/calibration        servo end points, flap law, knob — reported values + vcu_calibration.h text
  POST /api/vehicle/calibration        {"action": "save" | "revert"}   (values: POST /api/vehicle/config)
"""
from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import ai_engineer, tracks, twin, vcu_config
from app.db import get_conn, loads
from app.routers.telemetry import get_latest_telemetry
from app.services import vehicle_commands

router = APIRouter(prefix="/api", tags=["vehicle", "tracks", "twin"])


@router.get("/tracks")
def list_tracks() -> dict:
    doc = tracks.load()
    return {"defaultTrackId": doc["defaultTrackId"], "attribution": doc["attribution"],
            "vehicleModel": doc["vehicleModel"], "tracks": tracks.summary()}


@router.get("/tracks/{track_id}")
def get_track(track_id: int) -> dict:
    if not (0 <= track_id < len(tracks.all_tracks())):
        raise HTTPException(status_code=404, detail="no such track")
    t = dict(tracks.get(track_id))
    t.pop("vcuTable", None)
    return t


def _reported() -> dict | None:
    latest = get_latest_telemetry()
    if not latest:
        return None
    rep = (latest.get("frame") or {}).get("vcuConfig")
    return rep if isinstance(rep, dict) else None


@router.get("/vehicle/config")
def vehicle_config() -> dict:
    rep = _reported()
    return {"reported": rep is not None, "pending": bool((rep or {}).get("pending")),
            "keys": vcu_config.describe(rep)}


class ConfigWriteIn(BaseModel):
    key: int | None = None
    name: str | None = None
    value: float          # display units (degrees, %, m, A ...)


@router.post("/vehicle/config")
def write_config(body: ConfigWriteIn) -> dict:
    k = vcu_config.KEYS.get(body.key) if body.key is not None else vcu_config.BY_NAME.get(body.name or "")
    if k is None:
        raise HTTPException(status_code=404, detail="unknown key")
    wire = int(round(body.value / k.scale))
    err = vcu_config.validate(k.key, wire, vcu_config.effective_config(_reported()))
    if err:
        raise HTTPException(status_code=422, detail=err)
    return vehicle_commands.queue(k.key, wire, origin="operator")


class TrackSelectIn(BaseModel):
    trackId: int


@router.post("/vehicle/track")
def select_track(body: TrackSelectIn) -> dict:
    if not (0 <= body.trackId < len(tracks.all_tracks())):
        raise HTTPException(status_code=422, detail="no such track")
    return vehicle_commands.queue(2, body.trackId, origin="operator")


@router.get("/vehicle/commands")
def commands(limit: int = 30) -> list[dict]:
    return vehicle_commands.recent(limit)


@router.get("/vehicle/commands/next")
def commands_next() -> dict:
    return {"commands": vehicle_commands.next_batch()}


@router.get("/twin")
def twin_state() -> dict:
    latest = get_latest_telemetry()
    frame = (latest or {}).get("frame") or {}
    cfg = vcu_config.effective_config(frame.get("vcuConfig"))
    tr = frame.get("track") if isinstance(frame.get("track"), dict) else {}
    tid = tr.get("id", cfg["trackId"])
    t = tracks.get(tid)
    pos = float(tr.get("pos") or 0.0)
    sim = twin.simulate_config(t["id"], cfg)
    base = twin.simulate_config(t["id"], vcu_config.DEFAULTS)
    wing = frame.get("wing") or {}
    front = (float(wing.get("frontLeft", 0)) + float(wing.get("frontRight", 0))) / 2
    rear = (float(wing.get("rearLeft", 0)) + float(wing.get("rearRight", 0))) / 2
    forces = twin.forces_at(t["id"], pos, cfg, front, rear)
    return {"trackId": t["id"], "trackCode": t["code"], "pos": pos, "lapTimeS": sim["lapTimeS"],
            "sectorsS": sim["sectorsS"], "defaultsLapTimeS": base["lapTimeS"], "vmaxKmh": sim["vmaxKmh"],
            "speedKmh": sim["speedKmh"], "forces": forces, "sample": tracks.sample(t, pos),
            "corner": tracks.corner_at(t, pos),
            "nextEvent": tracks.next_event(t, pos, cfg["straightOpenDelayM"], cfg["straightCloseEarlyM"]),
            "modelled": True,
            "note": "Full-scale point-mass model with illustrative 2026-style aero; see app/twin.py."}


@router.get("/laps")
def laps(session_id: str | None = None) -> dict:
    """One row per completed dyno lap, reconstructed from recorded frames."""
    if session_id is None:
        latest = get_latest_telemetry()
        session_id = latest.get("sessionId") if latest else None
    if not session_id:
        return {"sessionId": None, "laps": []}
    with get_conn() as conn:
        rows = conn.execute("SELECT frame_json FROM telemetry_frames WHERE session_id = ? ORDER BY seq",
                            (session_id,)).fetchall()
    out, cur = [], None
    prev_lap, prev_t = None, None
    for r in rows:
        f = loads(r["frame_json"]) or {}
        tr = f.get("track") if isinstance(f.get("track"), dict) else None
        if not tr or not tr.get("runActive"):
            prev_lap = None
            continue
        lap = tr.get("lap")
        t_ms = f.get("timestamp")
        if cur is None or lap != prev_lap:
            if cur is not None and prev_lap is not None and lap == prev_lap + 1 and tr.get("lastLapMs"):
                cur["lapTimeS"] = round(tr["lastLapMs"] / 1000.0, 2)
                cfg = vcu_config.effective_config(f.get("vcuConfig"))
                cur["twinLapS"] = twin.simulate_config(tr.get("id", cfg["trackId"]), cfg)["lapTimeS"]
                cur["config"] = {k: cfg[k] for k in ("cornerFrontCdeg", "cornerRearCdeg", "straightFrontCdeg",
                                                     "dutyCapPermille", "dutyFloorPermille")}
                out.append(cur)
            cur = {"lap": (lap or 0) + 1, "trackId": tr.get("id"), "energyWh": 0.0, "peakCurrentA": 0.0,
                   "minVoltageV": None, "maxMotorTempC": None, "samples": 0}
        prev_lap = lap
        v, i = f.get("voltage"), f.get("current")
        if isinstance(v, (int, float)) and isinstance(i, (int, float)) and math.isfinite(v) and math.isfinite(i):
            if prev_t is not None and isinstance(t_ms, (int, float)) and 0 < t_ms - prev_t < 1000:
                cur["energyWh"] += v * i * (t_ms - prev_t) / 3.6e6
            cur["peakCurrentA"] = max(cur["peakCurrentA"], i)
            cur["minVoltageV"] = v if cur["minVoltageV"] is None else min(cur["minVoltageV"], v)
        mt = f.get("motorTemp")
        if isinstance(mt, (int, float)) and mt > 0:
            cur["maxMotorTempC"] = mt if cur["maxMotorTempC"] is None else max(cur["maxMotorTempC"], mt)
        cur["samples"] += 1
        prev_t = t_ms if isinstance(t_ms, (int, float)) else prev_t
    for row in out:
        row["energyWh"] = round(row["energyWh"], 4)
        row["peakCurrentA"] = round(row["peakCurrentA"], 3)
    best = min((r["lapTimeS"] for r in out if r.get("lapTimeS")), default=None)
    return {"sessionId": session_id, "laps": out, "bestLapS": best}


class StationSettingsIn(BaseModel):
    powerSource: str | None = None
    adapterLimitA: float | None = None
    lookaheadS: float | None = None


@router.get("/station/settings")
def get_station() -> dict:
    return ai_engineer.station_settings()


@router.post("/station/settings")
def set_station(body: StationSettingsIn) -> dict:
    try:
        return ai_engineer.save_station_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# =============================================================================
# FW 2.2 — operating controls (drive mode, runs, e-stop)
# =============================================================================
class ModeIn(BaseModel):
    mode: str


class RunIn(BaseModel):
    action: str
    laps: int | None = None


class EstopIn(BaseModel):
    action: str = "trigger"


class MotorIn(BaseModel):
    dutyPct: float


class CalActionIn(BaseModel):
    action: str


class DrsIn(BaseModel):
    action: str = "open"


class DemoIn(BaseModel):
    action: str = "start"
    trackId: int | None = None      # FW 2.3.10: demo lap on this circuit (None = the one selected on the car)


def _last_command(key: int) -> dict | None:
    for c in vehicle_commands.recent(60):
        if c["key"] == key:
            return c
    return None


@router.get("/vehicle/status")
def vehicle_status() -> dict:
    latest = get_latest_telemetry()
    frame = (latest or {}).get("frame") or {}
    vcu = frame.get("vcu") if isinstance(frame.get("vcu"), dict) else {}
    cfg = frame.get("vcuConfig") if isinstance(frame.get("vcuConfig"), dict) else {}
    tr = frame.get("track") if isinstance(frame.get("track"), dict) else {}
    mode = vcu.get("driveMode")
    if mode is None and "driveMode" in cfg:
        mode = "track" if int(cfg["driveMode"]) == 1 else "dyno"
    hw = vcu.get("hwProfile")
    if hw is None and "hwProfile" in cfg:
        hw = "pcb_rev_b" if int(cfg["hwProfile"]) == 1 else "prototype"
    state = frame.get("vcuState")
    run_state = frame.get("runState", "off")
    reasons = []
    if mode != "dyno":
        reasons.append("switch to DYNO mode (car on the rollers)")
    if state not in ("IDLE",):
        reasons.append(f"the VCU must be IDLE (now {state or 'unknown'})")
    if run_state not in ("off", None):
        reasons.append(f"a run is already active ({run_state})")
    if vcu.get("groundLinkOk") is False:
        reasons.append("the VCU does not see the ground link")
    # FW 2.3: the dashboard motor works in either drive mode
    motor_reasons = []
    if state not in ("IDLE",):
        motor_reasons.append(f"the VCU must be IDLE (now {state or 'unknown'})")
    if run_state not in ("off", "bench_motor", "demo", None):
        motor_reasons.append(f"a circuit run is active ({run_state})")
    if vcu.get("groundLinkOk") is False:
        motor_reasons.append("the VCU does not see the ground link")
    motor_cmd = cfg.get("motorCmd")
    return {
        "reporting": bool(frame), "demoFeed": bool(frame.get("demoFeed")),
        "driveMode": mode, "hwProfile": hw, "vcuState": state, "runState": run_state,
        "remoteRun": vcu.get("remoteRun"), "motorPower": vcu.get("motorPower"),
        "killSwitchFitted": vcu.get("killSwitchFitted"), "tcActive": vcu.get("tcActive"),
        "runLaps": vcu.get("runLaps", cfg.get("runLaps")), "lap": tr.get("lap"), "runActive": bool(tr.get("runActive")),
        "canStartRun": not reasons, "startBlockedBy": reasons,
        "motorRunning": run_state == "bench_motor", "motorDutyPct": frame.get("throttle"),
        "motorCommandPct": None if motor_cmd is None else round(int(motor_cmd) / 10.0, 1),
        "canRunMotor": not motor_reasons or run_state == "bench_motor", "motorBlockedBy": motor_reasons,
        "steeringDeg": frame.get("steering"), "knob": vcu.get("knob"),
        "demoRunning": run_state == "demo" or cfg.get("demoCmd") == 2, "speedEstimated": bool(cfg.get("speedSource")),
        "drsPhase": cfg.get("drsCmd"), "drsOpen": vcu.get("aeroStraight"),
        "lastCommands": {name: _last_command(key) for name, key in
                         (("mode", 5), ("run", 6), ("estop", 8), ("motor", 9), ("knob", 23), ("calibration", 24),
                          ("drs", 25), ("demo", 26))},
    }


@router.post("/vehicle/mode")
def set_mode(body: ModeIn) -> dict:
    v = vcu_config.DRIVE_MODES.get(body.mode.lower())
    if v is None:
        raise HTTPException(status_code=422, detail="mode must be 'dyno' or 'track'")
    return vehicle_commands.queue(5, v, origin="operator")


@router.post("/vehicle/run")
def run_command(body: RunIn) -> dict:
    v = vcu_config.RUN_ACTIONS.get(body.action.lower())
    if v is None:
        raise HTTPException(status_code=422, detail="action must be 'start', 'stop' or 'dyno_cal'")
    out = {}
    if body.laps is not None and v == 1:
        if not 0 <= body.laps <= 99:
            raise HTTPException(status_code=422, detail="laps must be 0..99 (0 = until stopped)")
        out["laps"] = vehicle_commands.queue(7, body.laps, origin="operator")
    out["run"] = vehicle_commands.queue(6, v, origin="operator")
    return out["run"] | ({"lapsCommand": out["laps"]} if "laps" in out else {})


@router.post("/vehicle/estop")
def estop(body: EstopIn | None = None) -> dict:
    action = (body.action if body else "trigger").lower()
    v = vcu_config.ESTOP_ACTIONS.get(action)
    if v is None:
        raise HTTPException(status_code=422, detail="action must be 'trigger' or 'reset'")
    return vehicle_commands.queue(8, v, origin="operator")


# =============================================================================
# FW 2.3 — motor from the dashboard, steering centre, servo calibration
# =============================================================================
@router.post("/vehicle/motor")
def motor(body: MotorIn) -> dict:
    """Run the rear motor at dutyPct (0 = stop). The VCU decides: IDLE, no fault, ground link up."""
    if not 0.0 <= body.dutyPct <= 100.0:
        raise HTTPException(status_code=422, detail="dutyPct must be 0..100")
    return vehicle_commands.queue(9, int(round(body.dutyPct * 10)), origin="operator")


@router.post("/vehicle/drs")
def drs(body: DrsIn | None = None) -> dict:
    """FW 2.3.3 DRS: all flaps FLAT for 5 s, all MAX for 1 s, then the steering law. action 'cancel' ends it."""
    action = (body.action if body else "open").lower()
    if action not in ("open", "cancel"):
        raise HTTPException(status_code=422, detail="action must be 'open' or 'cancel'")
    return vehicle_commands.queue(25, 1 if action == "open" else 0, origin="operator")


@router.post("/vehicle/demo")
def demo(body: DemoIn | None = None) -> dict:
    """FW 2.3.4 demo corner sequence, run by the VCU (21 s). action 'start' (default) or 'stop'."""
    action = (body.action if body else "start").lower()
    if action == "open":
        action = "start"
    if action not in ("start", "stop", "lap"):
        raise HTTPException(status_code=422, detail="action must be 'start', 'lap' or 'stop'")
    # 'lap' = demo 2: one auto-driven lap of a circuit scaled to under 40 s (FW 2.3.10: any circuit, 100 + id)
    if action == "lap" and body and body.trackId is not None:
        if not (0 <= body.trackId < len(tracks.all_tracks())):
            raise HTTPException(status_code=422, detail="no such track")
        return vehicle_commands.queue(26, 100 + body.trackId, origin="operator")
    return vehicle_commands.queue(26, {"start": 1, "lap": 2, "stop": 0}[action], origin="operator")


@router.post("/vehicle/steering/center")
def steering_center() -> dict:
    return vehicle_commands.queue(23, 1, origin="operator")


def calibration_header(values: dict) -> str:
    """The vcu_calibration.h #define block for these values (paste it into the file to make them the defaults)."""
    def deg(n):
        v = values[n] / 100.0
        return f"{v:g}"
    lines = [
        f"#define VCU_CAL_STEER_LEFT_US       {values['steerLeftUs']}",
        f"#define VCU_CAL_STEER_CENTER_US     {values['steerCenterUs']}",
        f"#define VCU_CAL_STEER_RIGHT_US      {values['steerRightUs']}",
        f"#define VCU_CAL_STEER_LOCK_DEG      {deg('steerLockCdeg')}",
    ]
    for code, name in (("FL", "Fl"), ("FR", "Fr"), ("RL", "Rl"), ("RR", "Rr")):
        lines.append(f"#define VCU_CAL_FLAP_{code}_FLAT_US     {values[f'flap{name}FlatUs']}")
        lines.append(f"#define VCU_CAL_FLAP_{code}_MAX_US      {values[f'flap{name}MaxUs']}")
    lines += [
        f"#define VCU_CAL_FLAP_NEUTRAL_DEG    {deg('flapNeutralCdeg')}",
        f"#define VCU_CAL_FLAP_SWING_DEG      {deg('flapSwingCdeg')}",
        f"#define VCU_CAL_KNOB_DEG_PER_CLICK  {deg('knobCdegPerClick')}",
        f"#define VCU_CAL_KNOB_REVERSE        {values['knobReverse']}",
    ]
    return "\n".join(lines) + "\n"


@router.get("/vehicle/calibration")
def calibration() -> dict:
    rep = _reported()
    eff = vcu_config.effective_config(rep)
    keys = [d for d in vcu_config.describe(rep) if d["apply"] == "calibration"]
    return {"reported": rep is not None, "keys": keys, "header": calibration_header(eff),
            "lastCommand": _last_command(24)}


@router.post("/vehicle/calibration")
def calibration_action(body: CalActionIn) -> dict:
    v = vcu_config.CAL_ACTIONS.get(body.action.lower())
    if v is None:
        raise HTTPException(status_code=422, detail="action must be 'save' or 'revert'")
    return vehicle_commands.queue(24, v, origin="operator")
