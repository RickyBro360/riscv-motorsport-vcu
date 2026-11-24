"""
Racing Engineer AI layer — deterministic simulated engine.

============================================================================
ARCHITECTURE (per project brief: "Racing Engineer AI layer")
============================================================================
This module is the AI. It lives at the Racing Engineer Station (this
FastAPI backend), NOT on the Raspberry Pi. That is a deliberate,
explicit architectural choice:

    Racing Engineer Station (telemetry + live phone video + track context)
        -> AI (THIS MODULE)
        -> recommendation
        -> temporary Wi-Fi/Bluetooth radio substitute (see
           HARDWARE/Ground Station/bridge.py's GroundRadio)
        -> Raspberry Pi 4B (HARDWARE/raspberry_pi/main.py, --ground-link lora)
        -> temporary CAN substitute (WifiCanInterface)
        -> ESP32 (HARDWARE/esp32/main_updated_corrected.ino)
        -> VCU validation (aiRecommendationIsValid(); mirrored for
           hardware-free testing in HARDWARE/tests/vcu_sim.py)
        -> actuator (only if the VCU accepts the recommendation)

The Pi never runs this module and never decides anything — it only
relays telemetry up and a recommendation down. The AI never touches an
actuator directly; the VCU is always the final authority (see
HARDWARE/tests/vcu_sim.py / main_updated_corrected.ino's aiRecommendationIsValid()).

============================================================================
CURRENT DEVELOPMENT STATE
============================================================================
This is intentionally NOT a trained ML model. It is a small set of
deterministic, explainable, threshold-based rules over telemetry. No
PyTorch/CUDA/ML framework is introduced here — the point right now is
proving the architecture (AI at the Engineer Station, VCU-validated,
fail-safe), not model quality. Swapping this module's internals for a
real model later does not require changing anything downstream: the
recommendation *shape* (see Recommendation below) is the stable
contract every other layer (bridge.py, dashboard, tests) already
speaks.

Video: if a phone is connected (see app/camera_signal.py), the engine
is *told* that video is available, but no real computer-vision
pipeline exists yet, so video never changes the recommendation output
today. This is deliberate and explicit — see `videoAvailable` /
`videoUsed` below. `videoUsed` must stay False until a real CV
pipeline is wired in; a fake AI prediction must never be presented as
if it came from vision.
============================================================================
"""

from __future__ import annotations

from typing import Any, Literal

RecommendationType = Literal["DRIVER_ADVICE", "AERO_ADVICE", "SAFETY_ADVICE"]

# Mirrors HARDWARE/esp32/main_updated_corrected.ino's
# CFG_AI_MAX_STALENESS_MS and HARDWARE/raspberry_pi/main.py's
# AIConfig.recommendation_validity_ms. Kept as the same 500ms here so a
# recommendation that is fresh enough to be worth issuing is also, by
# construction, fresh enough for the VCU to still accept by the time it
# arrives (radio + CAN transit time is on the order of tens of ms, not
# hundreds). If any of these three numbers changes, the other two should
# be reviewed too.
AI_RECOMMENDATION_VALIDITY_MS = 500

# Mirrors CFG_AI_MIN_CONFIDENCE on the ESP32 / AIConfig.min_confidence on
# the Pi. A recommendation below this confidence is still returned (the
# dashboard should be able to show "the AI looked at this and wasn't
# sure"), but callers forwarding it toward the VCU should not expect it
# to be accepted.
AI_MIN_CONFIDENCE = 0.55

WING_ANGLE_MIN_DEG = 0.0
WING_ANGLE_MAX_DEG = 45.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _classify_corner(g_lat: float, steering_deg: float) -> str:
    """Deterministic corner-phase classification from telemetry only.

    No vision input. Thresholds are simple magnitude buckets over lateral
    g — there is no claim of track-relative accuracy, just a repeatable
    label the rest of the engine (and the dashboard) can reason about.
    """
    mag = abs(g_lat)
    if mag < 0.15:
        return "Straight"
    if mag < 0.35:
        return "Slow Corner"
    if mag < 0.6:
        return "Medium Corner"
    if mag < 0.9:
        return "Fast Corner"
    if abs(steering_deg) > 22:
        return "Chicane"
    return "Fast Corner"


def _recommended_wing(speed_kmh: float, g_lat: float) -> dict[str, float]:
    """Simple deterministic aero target: more angle at speed, biased
    toward the outside-of-corner flaps under lateral load. This is the
    same shape of formula HARDWARE/Ground Station/bridge.py used to use
    locally (compute_wing_recommendation_ground) — it now lives here,
    at the Engineer Station, which is the one place it belongs per the
    target architecture."""
    base = _clamp(15.0 + 0.35 * speed_kmh, WING_ANGLE_MIN_DEG, WING_ANGLE_MAX_DEG)
    lat_term = _clamp(g_lat * 12.0, -15.0, 15.0)
    return {
        "frontLeft": _clamp(base - lat_term, WING_ANGLE_MIN_DEG, WING_ANGLE_MAX_DEG),
        "frontRight": _clamp(base + lat_term, WING_ANGLE_MIN_DEG, WING_ANGLE_MAX_DEG),
        "rearLeft": _clamp(base * 0.8 - lat_term, WING_ANGLE_MIN_DEG, WING_ANGLE_MAX_DEG),
        "rearRight": _clamp(base * 0.8 + lat_term, WING_ANGLE_MIN_DEG, WING_ANGLE_MAX_DEG),
    }


def generate_recommendation(
    frame: dict[str, Any],
    *,
    video_available: bool = False,
    now_ms: float | None = None,
    energy_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce one deterministic Recommendation from a telemetry frame.

    `frame` is the same TelemetryFrame-shaped dict the rest of the
    system already uses (see app/models.py's TelemetryFramePayload /
    dashboard/types/telemetry.ts's TelemetryFrame) — speed, motorTemp,
    batteryTemp, battery, gForceLat, gForceLong, steering, imu, etc.
    Missing fields default to a neutral value rather than raising, so a
    partial/older frame still produces a (low-confidence) recommendation
    instead of taking the AI down.

    Returns a dict combining:
      - the NEW generic contract (type/timestamp/confidence/reason/
        target/validity) the project brief asks for, and
      - the EXISTING AiRecommendation contract (corner/grip/
        recommendedWing/... — dashboard/types/telemetry.ts) so nothing
        downstream needs a schema migration to keep working.
    """
    speed = float(frame.get("speed", 0.0) or 0.0)
    motor_temp = float(frame.get("motorTemp", 0.0) or 0.0)
    battery_temp = float(frame.get("batteryTemp", 0.0) or 0.0)
    battery_pct = float(frame.get("battery", 100.0) or 0.0)
    # Stage 2c: no power sensor fitted (no INA260/ADS1115) reports 0 V. That is
    # "unknown", not "empty" — don't turn it into a permanent battery-low advice.
    voltage_raw = frame.get("voltage")
    power_sensor_present = voltage_raw is None or float(voltage_raw or 0.0) > 1.0
    g_lat = float(frame.get("gForceLat", 0.0) or 0.0)
    steering = float(frame.get("steering", 0.0) or 0.0)
    timestamp = float(frame.get("timestamp", now_ms if now_ms is not None else 0.0) or 0.0)

    corner = _classify_corner(g_lat, steering)
    grip = round(_clamp(1.0 - abs(g_lat) / 1.2, 0.0, 1.0), 3)
    wing = _recommended_wing(speed, g_lat)

    rtype: RecommendationType
    reason: str
    target: str
    confidence: float
    racing_line = "hold centre"
    warnings: list[str] = []

    # Priority order, most safety-critical first — deterministic, first
    # matching rule wins. This mirrors the VCU's own "safety overrides
    # everything else" posture without duplicating VCU authority: this
    # is still only a recommendation, never a command.
    if motor_temp >= 90.0:
        rtype = "SAFETY_ADVICE"
        target = "driver.throttle"
        reason = f"Motor temperature {motor_temp:.1f}\u00b0C is at/above the 90\u00b0C caution threshold."
        confidence = round(_clamp(0.75 + (motor_temp - 90.0) / 40.0, 0.0, 0.97), 3)
        warnings.append("motor_overtemp")
    elif battery_temp >= 55.0:
        rtype = "SAFETY_ADVICE"
        target = "driver.throttle"
        reason = f"Battery pack temperature {battery_temp:.1f}\u00b0C is at/above the 55\u00b0C caution threshold."
        confidence = round(_clamp(0.75 + (battery_temp - 55.0) / 30.0, 0.0, 0.97), 3)
        warnings.append("battery_overtemp")
    elif power_sensor_present and battery_pct <= 15.0:
        rtype = "SAFETY_ADVICE"
        target = "driver.energy"
        reason = f"Battery state of charge is {battery_pct:.0f}%, at/below the 15% reserve threshold."
        confidence = round(_clamp(0.7 + (15.0 - battery_pct) / 30.0, 0.0, 0.95), 3)
        warnings.append("battery_low")
    elif abs(g_lat) >= 0.9 and speed >= 20.0:
        rtype = "AERO_ADVICE"
        target = "aero.rearWing"
        reason = (
            f"Lateral load {abs(g_lat):.2f}g at {speed:.0f} km/h — increasing rear "
            "downforce trades straight-line efficiency for corner grip here."
        )
        confidence = round(_clamp(0.55 + abs(g_lat) * 0.3, 0.0, 0.9), 3)
        racing_line = "carry rotation, sacrifice entry angle for a straighter exit"
    elif abs(g_lat) >= 0.5 and speed >= 40.0:
        rtype = "DRIVER_ADVICE"
        target = "driver.entrySpeed"
        reason = f"Cornering load {abs(g_lat):.2f}g at {speed:.0f} km/h is approaching the grip limit for this corner."
        confidence = round(_clamp(0.55 + abs(g_lat) * 0.25, 0.0, 0.9), 3)
        racing_line = "reduce entry speed slightly, prioritise a clean rotation"
    else:
        rtype = "DRIVER_ADVICE"
        target = "driver.pace"
        reason = f"Telemetry nominal: {speed:.0f} km/h, {abs(g_lat):.2f}g lateral, {motor_temp:.0f}\u00b0C motor."
        confidence = 0.65

    note = (
        "SIMULATED deterministic recommendation (no trained model). "
        + (
            "Phone video is connected but no computer-vision pipeline exists yet — "
            "this recommendation is telemetry-only; video is NOT used."
            if video_available
            else "No phone video connected — telemetry-only."
        )
    )

    energy = energy_context or {}
    if not energy:
        warnings.append("energy_unmeasured")

    valid_until = timestamp + AI_RECOMMENDATION_VALIDITY_MS

    return {
        # --- new generic Recommendation contract ---
        "type": rtype,
        "timestamp": timestamp,
        "confidence": confidence,
        "reason": reason,
        "target": target,
        "validityMs": AI_RECOMMENDATION_VALIDITY_MS,
        "validUntil": valid_until,
        # --- existing AiRecommendation contract (dashboard/types/telemetry.ts) ---
        "corner": corner,
        "grip": grip,
        "recommendedWing": wing,
        "recommendedSpeed": speed,
        "efficiency": round(_clamp(60.0 + grip * 35.0, 0.0, 100.0), 1),
        "racingLine": racing_line,
        "note": note,
        # MEASURED (Stage 2.1): integrated from recorded pack voltage x current
        # by app/services/energy.py. Until a session has enough recorded
        # movement these are None and `energyMeasured` is false — the earlier
        # placeholder formulas (35 + 0.15 x speed, battery% / 4) were not
        # derived from anything the car measured and have been removed.
        "energyPerLap": energy.get("energyPerLap"),
        "predictedRangeLaps": energy.get("predictedRangeLaps"),
        "whPerKm": energy.get("whPerKm"),
        "energyMeasured": bool(energy.get("measured", False)),
        "warnings": warnings,
        # --- provenance ---
        "source": "engineer_station_deterministic_v1",
        "videoAvailable": video_available,
        "videoUsed": False,
    }


def ai_unavailable_response() -> dict[str, Any]:
    """What the AI layer looks like when it has nothing to say — no
    telemetry yet, or the engine itself is down. Per the project brief:
    'If AI stops: vehicle remains operational.' This is that contract's
    API shape — a clear, non-crashing 'nothing to recommend right now',
    never a 500 and never a stale recommendation silently reused."""
    return {
        "available": False,
        "type": None,
        "confidence": 0.0,
        "reason": "No telemetry available yet — AI has nothing to evaluate.",
        "note": "AI layer is up but idle. The VCU and vehicle are unaffected by this.",
    }
