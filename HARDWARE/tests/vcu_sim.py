#!/usr/bin/env python3
"""
=============================================================================
vcu_sim.py — Python-only VCU AI-recommendation validation simulation
=============================================================================

*** THIS IS NOT FIRMWARE. NO CLAIM OF PHYSICAL HARDWARE VALIDATION. ***

HARDWARE/esp32/main_updated_corrected.ino is the real firmware and the only
thing with actual safety authority; it is written in C++ for the real
ESP32-C6 VCU. This file exists because that firmware cannot run inside a
pytest process, and the project brief's target architecture explicitly
routes AI recommendations through "ESP32 simulation -> VCU validation ->
actuator simulation" so the whole chain can be proven end-to-end (see
HARDWARE/tests/test_ai_recommendation_chain.py) without physical hardware.

Every rule below is copied byte-for-byte/value-for-value from the real
firmware's aiRecommendationIsValid() / activeAeroCompute() (see the line
references in each docstring). If the firmware's thresholds ever change,
this file must change with them — it is a mirror, not an independent
implementation, and the two are expected to be kept in sync by hand
(same convention this project already uses for the LoRa/CAN codecs
duplicated across HARDWARE/raspberry_pi/main.py and
HARDWARE/Ground Station/bridge.py).

VCU AUTHORITY (unchanged from the existing safety architecture):
    AI recommendation -> Pi / comms layer -> ESP32 VCU -> VCU validation
    -> accept/reject -> actuator
The AI never controls an actuator directly; this module never accepts a
recommendation "because it looks reasonable" — only because it passes
the same objective checks the real firmware applies.
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass

# Mirrors vcu_logic.h CFG_AI_* (Stage 2; was main_updated_corrected.ino).
CFG_AI_MIN_CONFIDENCE = 0.55
CFG_AI_MAX_STALENESS_MS = 500

# Mirrors vcu_logic.h CFG_WING_*/CFG_AERO_* (Stage 2).
CFG_WING_ANGLE_MIN_DEG = 0.0
CFG_WING_ANGLE_MAX_DEG = 45.0
CFG_WING_FAILSAFE_ANGLE_DEG = 0.0
CFG_AERO_MIN_SPEED_KMH = 8.0
CFG_AI_MAX_DELTA_DEG = 20.0


@dataclass(frozen=True)
class AiWingRecommendation:
    """Mirrors main_updated_corrected.ino's AiWingRecommendation struct."""

    lf: float
    rf: float
    lr: float
    rr: float
    confidence: float
    timestamp_ms: float
    valid: bool = True  # False simulates a malformed/never-populated recommendation


def _is_finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))  # NaN != NaN


def ai_recommendation_is_valid(rec: AiWingRecommendation, now_ms: float) -> tuple[bool, str]:
    """Byte-for-byte mirror of aiRecommendationIsValid() (main_updated_corrected.ino,
    ~line 687-697). Returns (accepted, reason) — the real firmware only
    returns bool; the reason string is this simulation's addition so
    tests/dashboards can explain *why* the VCU would reject something,
    without changing what gets accepted."""
    if not rec.valid:
        return False, "recommendation marked invalid/unpopulated"
    if not _is_finite(rec.confidence) or rec.confidence < CFG_AI_MIN_CONFIDENCE:  # Stage 2: NaN rejected, as firmware
        return False, f"confidence {rec.confidence:.2f} below CFG_AI_MIN_CONFIDENCE {CFG_AI_MIN_CONFIDENCE}"
    if now_ms < rec.timestamp_ms:
        return False, "recommendation timestamped in the future"
    age_ms = now_ms - rec.timestamp_ms
    if age_ms > CFG_AI_MAX_STALENESS_MS:
        return False, f"stale: age {age_ms:.0f}ms exceeds CFG_AI_MAX_STALENESS_MS {CFG_AI_MAX_STALENESS_MS}"
    for name, v in (("lf", rec.lf), ("rf", rec.rf), ("lr", rec.lr), ("rr", rec.rr)):
        if not _is_finite(v):
            return False, f"{name} is NaN/Inf"
        if v < CFG_WING_ANGLE_MIN_DEG or v > CFG_WING_ANGLE_MAX_DEG:
            return False, f"{name}={v:.1f} out of range [{CFG_WING_ANGLE_MIN_DEG}, {CFG_WING_ANGLE_MAX_DEG}]"
    return True, "accepted"


def _clampf(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def simulate_actuator_target(speed_kmh: float, rec: AiWingRecommendation, now_ms: float,
                              vehicle_state: str = "DRIVE") -> dict:
    """Simplified mirror of activeAeroCompute() (main_updated_corrected.ino,
    ~line 701-742): physics-baseline wing target, nudged toward the AI's
    recommendation by up to CFG_AI_MAX_DELTA_DEG *only if the VCU accepts
    the recommendation*. Deliberately simplified vs. firmware: no
    per-channel lateral-g/yaw shaping and no dt-based rate limiting (this
    is a single-shot simulation, not a continuous control loop) — this is
    a SIMULATION for proving the accept/reject/actuate chain in tests, not
    a claim of matching physical actuator dynamics.

    SAFETY (mirrors ActiveAeroController::compute in vcu_logic.h, FW 2.3): in
    "FAULT" or "EMERGENCY_STOP" the AI recommendation is never applied,
    regardless of ai_recommendation_is_valid(). In EMERGENCY_STOP the wings
    are forced to CFG_WING_FAILSAFE_ANGLE_DEG; in FAULT (motor off) they keep
    the baseline — FW 2.3 no longer parks the flaps on a fault. The safe
    vehicle state always wins over the AI recommendation.
    """
    ai_blocked = vehicle_state in ("FAULT", "EMERGENCY_STOP")
    in_safe_override_state = vehicle_state == "EMERGENCY_STOP"
    accepted, reason = (
        (False, f"vehicle_state={vehicle_state} overrides AI recommendation (safe state wins)")
        if ai_blocked
        else ai_recommendation_is_valid(rec, now_ms)
    )

    if speed_kmh < CFG_AERO_MIN_SPEED_KMH:
        baseline = CFG_WING_FAILSAFE_ANGLE_DEG
    else:
        baseline = _clampf(speed_kmh - CFG_AERO_MIN_SPEED_KMH, CFG_WING_ANGLE_MIN_DEG, CFG_WING_ANGLE_MAX_DEG)

    target = {"lf": baseline, "rf": baseline, "lr": baseline, "rr": baseline}
    if in_safe_override_state:
        target = {"lf": CFG_WING_FAILSAFE_ANGLE_DEG, "rf": CFG_WING_FAILSAFE_ANGLE_DEG,
                  "lr": CFG_WING_FAILSAFE_ANGLE_DEG, "rr": CFG_WING_FAILSAFE_ANGLE_DEG}
    elif accepted:
        for ch, ai_val in (("lf", rec.lf), ("rf", rec.rf), ("lr", rec.lr), ("rr", rec.rr)):
            delta = _clampf(ai_val - target[ch], -CFG_AI_MAX_DELTA_DEG, CFG_AI_MAX_DELTA_DEG)
            target[ch] = _clampf(target[ch] + delta, CFG_WING_ANGLE_MIN_DEG, CFG_WING_ANGLE_MAX_DEG)

    return {
        "accepted": accepted,
        "reason": reason,
        "target": target,
        # AI never drives the actuator directly — this dict is what the
        # VCU's own control loop would command, not what the AI asked for.
        "aiRequested": {"lf": rec.lf, "rf": rec.rf, "lr": rec.lr, "rr": rec.rr},
    }
