"""
vcu_config.py — the VCU's tunable parameters as the ground station sees them.

Mirrors HARDWARE/esp32/main_updated_corrected__1_/vcu_logic.h (ConfigKey, Tunables,
ConfigStore::write bounds) and the Python key tables in raspberry_pi/main.py and
Ground Station/bridge.py. A test (tests/test_stage2d.py) checks the three agree.

The backend validates against the same bounds BEFORE queueing a change so the
operator gets an immediate, readable refusal — but the VCU remains the authority:
it re-validates every write and answers with CONFIG_ACK. The dashboard only shows
"applied on car" once that ACK (or the VCU's own CONFIG_REPORT) confirms it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigKeyDef:
    key: int
    name: str
    label: str
    unit: str
    scale: float          # wire value * scale = display value
    lo: int
    hi: int
    default: int
    apply: str            # "staged" (next start/finish line) | "immediate" | "idle_only" | "readonly"
                          # | "command" (FW 2.2: run / e-stop requests, decided by the VCU at that moment)
                          # | "calibration" (FW 2.3: servo end points etc., immediate, any state)
    ai_tunable: bool      # may the AI engineer propose a change to it?
    description: str

    def display(self, wire: int | None) -> float | None:
        return None if wire is None else round(wire * self.scale, 4)


KEYS: dict[int, ConfigKeyDef] = {k.key: k for k in [
    ConfigKeyDef(1, "aeroMode", "Aero mode", "", 1, 0, 2, 1, "idle_only", False,
                 "0 baseline, 1 AI-assist (VCU blends AI within ±20°), 2 failsafe hold"),
    ConfigKeyDef(2, "trackId", "Circuit", "", 1, 0, 23, 12, "idle_only", False,
                 "Which stored circuit map the car drives"),
    ConfigKeyDef(3, "lapLengthCm", "Dyno lap length", "m", 0.01, 500, 100000, 6000, "idle_only", True,
                 "Wheel travel on the dyno that counts as one lap"),
    ConfigKeyDef(4, "trackRun", "Track run in DRIVE", "", 1, 0, 1, 0, "idle_only", False,
                 "1 = in DRIVE the car follows the map (driver throttle is a ceiling). Dyno mode only"),
    # FW 2.2 — drive modes and dashboard run control
    ConfigKeyDef(5, "driveMode", "Drive mode", "", 1, 0, 1, 1, "idle_only", False,
                 "0 = DYNO (car on the rollers: circuit runs and dyno cal allowed), 1 = TRACK (on the floor: "
                 "only a driver holding ARM moves it). Changes only with the car stopped"),
    ConfigKeyDef(6, "runCmd", "Run", "", 1, 0, 2, 0, "command", False,
                 "COMMAND: 0 stop, 1 start a circuit run, 2 dyno calibration. DYNO mode, IDLE, no fault, ground link up"),
    ConfigKeyDef(7, "runLaps", "Laps per dashboard run", "laps", 1, 0, 99, 0, "immediate", False,
                 "How many laps a run started from the dashboard drives (0 = until stopped)"),
    ConfigKeyDef(8, "estop", "Emergency stop", "", 1, 1, 2, 0, "command", False,
                 "COMMAND: 1 = emergency stop (latches), 2 = reset request (re-runs the self-test)"),
    # FW 2.3 — motor from the dashboard, knob = steering wheel, servo calibration
    ConfigKeyDef(9, "motorCmd", "Motor", "%", 0.1, 0, 1000, 0, "command", False,
                 "COMMAND: motor duty from the dashboard, 0 = stop. Any drive mode; needs IDLE, no fault and the "
                 "ground link (the link is the deadman). A new value while running changes the duty at once"),
    ConfigKeyDef(23, "knobCmd", "Centre steering", "", 1, 1, 1, 0, "command", False,
                 "COMMAND: 1 = put the knob (steering wheel) back to straight ahead"),
    ConfigKeyDef(24, "calCmd", "Calibration", "", 1, 1, 2, 0, "command", False,
                 "COMMAND: 1 = save the servo calibration on the car, 2 = revert to vcu_calibration.h"),
    ConfigKeyDef(25, "drsCmd", "DRS", "", 1, 0, 1, 0, "command", False,
                 "COMMAND: 1 = DRS — all flaps FLAT for 5 s, all MAX for 1 s, then back to following the steering; "
                 "0 = cancel. Reported value = phase (0 off, 1 flat, 2 max hold)"),
    ConfigKeyDef(26, "demoCmd", "Demo sequence", "", 1, 0, 199, 0, "command", False,
                 "COMMAND (FW 2.3.4): 1 = run the 21 s demo corner sequence (accelerate, brake, left, right, full "
                 "speed + DRS, stop), 0 = stop it. Same conditions as the dashboard motor. Reported: 1 while running"),
    ConfigKeyDef(27, "aiSteerCmd", "AI steering", "°", 0.1, -450, 450, 0, "command", False,
                 "COMMAND (FW 2.3.12): steering angle from the AI Model page, + = left, clamped to the steering lock. "
                 "Replaces the knob while new values keep arriving (1.5 s); turning the knob takes steering back. "
                 "Reported: the angle in force, 0 when the knob steers"),
    ConfigKeyDef(10, "straightFrontCdeg", "Straight-mode flap, front", "°", 0.01, 0, 4500, 500, "staged", True,
                 "Low-drag flap angle on the straights, front pair"),
    ConfigKeyDef(11, "straightRearCdeg", "Straight-mode flap, rear", "°", 0.01, 0, 4500, 500, "staged", True,
                 "Low-drag flap angle on the straights, rear pair"),
    ConfigKeyDef(12, "cornerFrontCdeg", "Corner-mode flap, front", "°", 0.01, 0, 4500, 3000, "staged", True,
                 "High-downforce flap angle everywhere else, front pair"),
    ConfigKeyDef(13, "cornerRearCdeg", "Corner-mode flap, rear", "°", 0.01, 0, 4500, 3500, "staged", True,
                 "High-downforce flap angle everywhere else, rear pair"),
    ConfigKeyDef(14, "asymGainCdegPerG", "Asymmetric gain", "°/g", 0.01, 0, 500, 200, "staged", True,
                 "Left/right flap split per modelled lateral g in corners (capped ±10°)"),
    ConfigKeyDef(15, "straightOpenDelayM", "Straight mode opens later by", "m", 1, -100, 300, 0, "staged", True,
                 "Full-scale metres after the zone start before the flaps open"),
    ConfigKeyDef(16, "straightCloseEarlyM", "Straight mode closes early by", "m", 1, 0, 400, 0, "staged", True,
                 "Full-scale metres before the zone end (braking point) the flaps close"),
    ConfigKeyDef(17, "dutyCapPermille", "Motor duty cap", "%", 0.1, 200, 1000, 1000, "staged", True,
                 "Motor duty at the circuit's top speed"),
    ConfigKeyDef(18, "dutyFloorPermille", "Motor duty floor", "%", 0.1, 0, 800, 350, "staged", True,
                 "Motor duty at the circuit's slowest corner (must turn the wheels)"),
    ConfigKeyDef(19, "wingSlewDps", "Flap slew rate", "°/s", 1, 20, 300, 120, "staged", True,
                 "Maximum flap speed the VCU commands"),
    ConfigKeyDef(20, "steerGainPermille", "Steering gain", "×", 0.001, 500, 3000, 1000, "staged", True,
                 "1.0 = true geometry of the circuit (atan(L·κ))"),
    ConfigKeyDef(21, "currentLimitCa", "Supply current limit", "A", 0.01, 50, 1200, 200, "immediate", True,
                 "Motor duty is derated above this supply current (2.0 A for the 12 V / 2.2 A adapter)"),
    ConfigKeyDef(22, "tcSlipPermille", "Traction control slip target", "%", 0.1, 0, 500, 250, "immediate", True,
                 "Track mode: motor duty is cut when the rear wheels turn this much faster than the fronts "
                 "(0 = off, else 5..50 %)"),
    # FW 2.3 servo calibration — immediate, any state. A pulse change moves that servo there for 2.5 s.
    ConfigKeyDef(30, "steerLeftUs", "Steering: full LEFT", "µs", 1, 500, 2500, 1150, "calibration", False,
                 "Steering servo pulse at full left lock"),
    ConfigKeyDef(31, "steerCenterUs", "Steering: straight", "µs", 1, 500, 2500, 1600, "calibration", False,
                 "Steering servo pulse with the front wheels straight"),
    ConfigKeyDef(32, "steerRightUs", "Steering: full RIGHT", "µs", 1, 500, 2500, 2200, "calibration", False,
                 "Steering servo pulse at full right lock"),
    ConfigKeyDef(33, "flapFlFlatUs", "Front-left flap: flat (0°)", "µs", 1, 500, 2500, 1500, "calibration", False,
                 "Pulse with the flap at its lowest-downforce position"),
    ConfigKeyDef(34, "flapFlMaxUs", "Front-left flap: max (45°)", "µs", 1, 500, 2500, 650, "calibration", False,
                 "Pulse with the flap at its highest-downforce position"),
    ConfigKeyDef(35, "flapFrFlatUs", "Front-right flap: flat (0°)", "µs", 1, 500, 2500, 1500, "calibration", False, ""),
    ConfigKeyDef(36, "flapFrMaxUs", "Front-right flap: max (45°)", "µs", 1, 500, 2500, 1000, "calibration", False, ""),
    ConfigKeyDef(37, "flapRlFlatUs", "Rear-left flap: flat (0°)", "µs", 1, 500, 2500, 1750, "calibration", False,
                 "MG996R: the pot reading here is taken as the feedback's 0° end"),
    ConfigKeyDef(38, "flapRlMaxUs", "Rear-left flap: max (45°)", "µs", 1, 500, 2500, 2000, "calibration", False,
                 "MG996R: the pot reading here is taken as the feedback's 45° end"),
    ConfigKeyDef(39, "flapRrFlatUs", "Rear-right flap: flat (0°)", "µs", 1, 500, 2500, 1420, "calibration", False, ""),
    ConfigKeyDef(40, "flapRrMaxUs", "Rear-right flap: max (45°)", "µs", 1, 500, 2500, 900, "calibration", False, ""),
    ConfigKeyDef(41, "steerLockCdeg", "Steering lock", "°", 0.01, 500, 4500, 3000, "calibration", False,
                 "Road-wheel angle at full lock (knob range and flap law scale with it)"),
    ConfigKeyDef(42, "flapNeutralCdeg", "Flaps, wheels straight", "°", 0.01, 0, 4500, 4500, "calibration", False,
                 "All four flaps with the steering straight"),
    ConfigKeyDef(43, "flapSwingCdeg", "Flap swing at full lock", "°", 0.01, 0, 9000, 4500, "calibration", False,
                 "Turning: the outside pair rises by up to this, the inside pair drops by up to this (above 45° the "
                 "inside pair is flat before full lock). Neutral 45 + swing 45 = straight all MAX, inside to FLAT"),
    ConfigKeyDef(44, "knobCdegPerClick", "Steering per knob click", "°", 0.01, 50, 1000, 300, "calibration", False,
                 "Road-wheel degrees per KY-040 detent"),
    ConfigKeyDef(45, "knobReverse", "Knob reversed", "", 1, 0, 1, 0, "calibration", False,
                 "1 = clockwise steers left"),
    ConfigKeyDef(100, "dynoVmaxCkmh", "Dyno top wheel speed", "km/h", 0.01, 0, 0, 0, "readonly", False,
                 "Measured by `dyno cal` at the duty cap"),
    ConfigKeyDef(101, "dynoStartPermille", "Dyno start duty", "%", 0.1, 0, 0, 0, "readonly", False,
                 "Lowest duty that turned the wheels in `dyno cal`"),
    ConfigKeyDef(102, "trackCount", "Stored circuits", "", 1, 0, 0, 24, "readonly", False, ""),
    ConfigKeyDef(103, "wheelDiameterUm", "Wheel diameter", "mm", 0.001, 0, 0, 70000, "readonly", False,
                 "What the firmware uses for speed and distance"),
    ConfigKeyDef(104, "hwProfile", "Hardware", "", 1, 0, 0, 0, "readonly", False,
                 "0 = prototype (hobby modules), 1 = PCB rev B"),
    ConfigKeyDef(106, "speedSource", "Wheel-speed source", "", 1, 0, 1, 0, "readonly", False,
                 "FW 2.3.4: 0 = wheel sensors, 1 = ESTIMATED from the motor duty (not measured)"),
    ConfigKeyDef(105, "wheelPpr", "Wheel-sensor pulses per rev", "", 1, 0, 0, 1, "readonly", False,
                 "Magnets (prototype) or target-wheel teeth / pole pairs (PCB rev B)"),
]}
COMMAND_KEYS = {k.key for k in KEYS.values() if k.apply == "command"}
DRIVE_MODES = {"dyno": 0, "track": 1}
RUN_ACTIONS = {"stop": 0, "start": 1, "dyno_cal": 2}
CAL_ACTIONS = {"save": 1, "revert": 2}
CALIBRATION_KEYS = [k.key for k in KEYS.values() if k.apply == "calibration"]
ESTOP_ACTIONS = {"trigger": 1, "reset": 2}
BY_NAME = {k.name: k for k in KEYS.values()}

STATUS_NAMES = ["applied", "staged_next_lap", "rejected_range", "rejected_state", "rejected_unknown_key"]

# The same defaults as vcu_logic.h Tunables{} — used when the car has not reported yet.
DEFAULTS = {k.name: k.default for k in KEYS.values()}


def validate(key: int, value: int, current: dict | None = None) -> str | None:
    """Same bounds as ConfigStore::write. Returns an error string or None."""
    k = KEYS.get(key)
    if k is None:
        return f"unknown key {key}"
    if k.apply == "readonly":
        return f"{k.label} is read-only (measured by the VCU)"
    if not (k.lo <= value <= k.hi):
        return f"{k.label}: {k.display(value)} {k.unit} is outside {k.display(k.lo)}..{k.display(k.hi)}"
    if k.name == "tcSlipPermille" and 0 < value < 50:
        return "traction control slip target: 0 (off) or 5..50 %"
    if k.apply == "command":
        return None
    cur = dict(DEFAULTS)
    cur.update({n: v for n, v in (current or {}).items() if n in BY_NAME})
    cur[k.name] = value
    if cur["dutyFloorPermille"] >= cur["dutyCapPermille"]:
        return "the duty floor must stay below the duty cap"
    return None


def effective_config(reported: dict | None) -> dict:
    """VCU-reported values where known, firmware defaults otherwise."""
    out = dict(DEFAULTS)
    for name, v in (reported or {}).items():
        if name in BY_NAME and v is not None:
            out[name] = int(v)
    return out


def describe(reported: dict | None) -> list[dict]:
    eff = effective_config(reported)
    return [{"key": k.key, "name": k.name, "label": k.label, "unit": k.unit,
             "value": k.display(eff[k.name]), "wire": eff[k.name],
             "reported": reported is not None and k.name in (reported or {}),
             "min": k.display(k.lo), "max": k.display(k.hi), "apply": k.apply,
             "aiTunable": k.ai_tunable, "description": k.description}
            for k in KEYS.values()]
