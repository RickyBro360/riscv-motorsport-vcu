#!/usr/bin/env python3
"""
=============================================================================
main.py — Raspberry Pi Vehicle Gateway
=============================================================================

Formula-Student-style RC car project — HARDWARE layer, Raspberry Pi side.

This is a SELF-CONTAINED script: one file, no other hardware source files.
It mirrors the logic that previously lived across RaspberryPi/{can,lora,
camera,telemetry,ai,opencv,config}/... in this project's earlier, more
granular tree. That tree has been consolidated into this single file per
the final packaging architecture. The CAN byte layout, LoRa framing, and
telemetry field names/units below are copied unchanged from that tree's
Documentation/CANProtocol.md, Documentation/LoRaProtocol.md and
Documentation/TelemetryProtocol.md so this script stays wire-compatible
with the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h) and with the existing SOFTWARE backend
(FastAPI's /ws/telemetry) without either side needing a shared library.

Conceptual flow:
    CAN / LoRa input
        -> decode vehicle information
        -> build canonical VehicleTelemetry
        -> send telemetry to SOFTWARE (FastAPI /ws/telemetry, unchanged)
        -> ask the Racing Engineer Station backend (SOFTWARE/backend/app/
           ai_engine.py, via GET /api/ai/recommendation) for its current
           recommendation — THIS GATEWAY NEVER COMPUTES ONE ITSELF
        -> send recommendation to ESP32 over CAN
        -> repeat

This script is a VEHICLE TELEMETRY / COMMUNICATIONS GATEWAY, not an AI
engine. All AI/perception/analysis lives at the Racing Engineer Station
(SOFTWARE/backend/app/ai_engine.py) — see SECTION 4 below.

STAGE 2 UPDATE (see SOFTWARE/Documentation/ProjectAudit_Stage2.md and
ChangeLog_Stage2.md): real hardware backends now exist alongside the
original substitutes, which are all KEPT for bench testing:
    CAN : SocketCanInterface (python-can, Pi CAN HAT / can0)   [NEW, real]
          WifiCanInterface (UDP substitute), MockCanInterface   [kept]
    LoRa: SX1278SpiRadio (LoRaRF, Ra-02/SX1278 at 433 MHz)      [NEW, real]
          SerialSlipRadio (UART/USB LoRa modem, SLIP framed)    [NEW, real]
          UdpLoRaRadio (UDP substitute)                          [kept]
CAN protocol is now v2: aero angles in centi-degrees (v1 milli-degree
int16 silently clamped anything above 32.767 deg on a 0-45 deg flap) and
currents in centi-amps (v1 milli-amp int16 clamped above 32.767 A).
Real hardware backends are NOT HARDWARE VALIDATED until bench-tested.

(Stage-1 note, retained) PHYSICAL CAN/LoRa HARDWARE WAS NOT CONNECTED. Every hardware-
facing piece below is a clearly marked SAMPLE implementation. The parts
that are NOT samples — the CAN/LoRa codec, the telemetry adapter, and the
WebSocket message shape sent to SOFTWARE — are the real, already-tested
protocol and do not change when real hardware arrives; only the marked
sample functions get replaced.

Usage:
    cd HARDWARE/raspberry_pi
    pip install websockets --break-system-packages   # optional, only for sending to backend

    # STAGE 2 default: real CAN via the Pi CAN HAT (bring the link up first:
    # sudo ./setup/can_up.sh) -> laptop backend direct over Wi-Fi.
    python3 main.py --laptop <LAPTOP_IP> -v
    #   (or set LAPTOP_IP in the environment; --url still works as before)
    #   add --can-channel can1 if candump shows the VCU on can1 (see can_up.sh)

    # Production target (MODE B): CAN in, SX1278 LoRa to the ground station
    python main.py --ground-link lora --lora-backend sx1278

    # Stage-1 bench path (ESP32 over the temporary WiFi transport):
    python main.py --can-interface wifi

    # Dev/demo without an ESP32 attached (self-generated sample waveform):
    python main.py --source sample --can-interface mock

    # run fully offline (no backend needed), just print what would be sent:
    python main.py --no-backend -v
=============================================================================
"""
from __future__ import annotations

import argparse
import os
import asyncio
import json
import logging
import math
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace

logger = logging.getLogger("gateway")

try:
    # Optional dependency (only required when --no-backend is not set, same
    # as the `websockets` import in TelemetryGatewayClient.connect()). Bound
    # here too so the reconnect loop in run() can catch backend-initiated
    # disconnects (e.g. ping timeout, restart) alongside plain socket errors
    # — without this, such disconnects crash the gateway instead of
    # triggering the existing backoff/reconnect path (see project brief
    # Test 6: backend failure/reconnect).
    from websockets.exceptions import ConnectionClosed
except ImportError:
    class ConnectionClosed(Exception):
        pass

# =============================================================================
# NETWORK CONFIGURATION — TEMPORARY TEST VALUES (project brief Section 2)
# Wi-Fi is the development-stage stand-in for physical CAN (ESP32<->Pi) and
# is also the transport this Pi already uses to reach the laptop backend.
# WIFI_SSID/WIFI_PASSWORD are informational here (this Pi's OS network
# connection, e.g. wpa_supplicant/NetworkManager, is out of scope for this
# script) but are named explicitly so the whole test network is documented
# in one place, matching the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h)'s WIFI_* config block.
# CHANGE THESE VALUES if the test network changes.
# =============================================================================
WIFI_SSID = "CTRL"
WIFI_PASSWORD = "SET_ME"  # Stage 2: real password removed from source — informational only, never used by this script
THIS_PI_IP = "10.49.218.178"
# The laptop's address changes whenever the hotspot hands out a new lease, so
# it is NOT hard-coded: pass --laptop <ip> (or export LAPTOP_IP=<ip>).
LAPTOP_BACKEND_IP = os.environ.get("LAPTOP_IP", "SET_LAPTOP_IP")
LAPTOP_BACKEND_PORT = 8000
BACKEND_WS_URL = f"ws://{LAPTOP_BACKEND_IP}:{LAPTOP_BACKEND_PORT}/ws/telemetry"
PI_LISTEN_PORT = 5005          # ESP32 -> Pi telemetry; must match ESP32's PI_PORT
ESP32_LISTEN_PORT = 5006       # Pi -> ESP32 AI recommendation; must match ESP32's ESP32_LISTEN_PORT
TELEMETRY_RATE_HZ = 20.0
ESP32_LINK_TIMEOUT_S = 1.0     # no ESP32 packet for this long -> withhold telemetry from backend (see Test 5)

# =============================================================================
# SECTION 1: CONFIGURATION
# Mirrors the old RaspberryPi/config/settings.py — same tags:
#   [DEFAULT] reasonable placeholder, safe to run in simulation
#   [PLACEHOLDER] a number that MUST be revisited before it means anything physical
# =============================================================================


@dataclass(frozen=True)
class AIConfig:
    min_confidence: float = 0.55              # [DEFAULT] mirrors ESP32 CFG_AI_MIN_CONFIDENCE
    recommendation_validity_ms: int = 500      # [DEFAULT] mirrors ESP32 CFG_AI_MAX_STALENESS_MS


@dataclass(frozen=True)
class CanConfig:
    interface: str = "socketcan"  # "socketcan" | "wifi" | "mock"
    channel: str = "can0"         # Pi CAN HAT (MCP2515 overlay) interface name
    bitrate: int = 500000         # must match the ESP32-C6 TWAI_TIMING_CONFIG_500KBITS()


@dataclass(frozen=True)
class LoRaConfig:
    interface: str = "sx1278"          # "sx1278" | "serial" | "udp" (substitute)
    frequency_hz: int = 433_000_000    # Ra-02 / SX1278 covers 410-525 MHz. 433 MHz is licence-exempt
                                       # in India. [PLACEHOLDER] the antenna must be a 433 MHz one.
    spreading_factor: int = 7          # SF7 / BW250 / CR4-5: ~49 ms air time for a 48 B packet
    bandwidth_hz: int = 250_000
    coding_rate: int = 5               # denominator, 4/5
    tx_power_dbm: int = 10             # 10 dBm = 10 mW conducted. India de-licenses 433-434.79 MHz only at
                                       # very low power — VERIFY the current notification before raising
                                       # this; the ground bridge uses the same value.
    sync_word: int = 0x12              # SX127x has a single sync-word register; 0x12 = private network,
                                       # the same value RadioLib writes on the ground bridge.
    preamble: int = 8
    uplink_rate_hz: float = 5.0        # LoRa cannot carry 20 Hz telemetry; full rate is logged on the Pi


@dataclass(frozen=True)
class TelemetryConfig:
    backend_ws_url: str = BACKEND_WS_URL  # matches backend/app/websocket.py
    publish_rate_hz: float = TELEMETRY_RATE_HZ


@dataclass(frozen=True)
class Settings:
    ai: AIConfig = AIConfig()
    can: CanConfig = CanConfig()
    lora: LoRaConfig = LoRaConfig()
    telemetry: TelemetryConfig = TelemetryConfig()


DEFAULT_SETTINGS = Settings()

# =============================================================================
# SECTION 2: CAN
# Byte-identical to Documentation/CANProtocol.md and to the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h).
# =============================================================================

CAN_PROTOCOL_VERSION = 2  # v2: centi-degree aero, centi-amp current (see module docstring)

CAN_ID_VEHICLE_STATE = 0x100
CAN_ID_WHEEL_SPEED = 0x110
CAN_ID_DRIVER_INPUTS = 0x115  # [NEW] steering(i16 milli-deg), throttle(u8 pct), brake(u8 pct) —
                               # see the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h) Section 2 comment / final report
CAN_ID_IMU_ACCEL = 0x120
CAN_ID_IMU_GYRO = 0x121
CAN_ID_BATTERY_POWER = 0x130
CAN_ID_TEMPERATURE = 0x140  # [NOW ENCODED] motorTemp(i16 deci-C), batteryTemp(i16 deci-C),
                             # motorCurrent(i16 mA) — previously reserved/STUB, see final report
CAN_ID_VEHICLE_DERIVED = 0x141  # [NEW] slip i16 permille, seconds-to-motor-limit u16, seconds-to-battery-limit u16
CAN_ID_AERO_MEASURED = 0x150   # [NEW v2] 4x i16 centi-deg, from MG996R potentiometer feedback
CAN_ID_AERO_TARGET = 0x151     # 4x i16 centi-deg (v1 was milli-deg — overflowed above 32.767 deg)
CAN_ID_AI_RECOMMENDATION_A = 0x160
CAN_ID_AI_RECOMMENDATION_B = 0x161
CAN_ID_DRIVER_COMMAND = 0x118  # [NEW v2] Pi->VCU seq u8, steer i16 permille, throttle i16 permille, flags u8
CAN_ID_IMU_ATTITUDE = 0x122    # [NEW v2] roll, pitch, yaw i16 deci-deg
CAN_ID_FAULT_EVENT = 0x170     # [NEW v2] flags u32, severity u8, state u8 (on change)
CAN_ID_VCU_STATUS = 0x171      # [NEW v2] aero_mode, last_ai_reason, driver_link_ok, fb_cal_mask, uptime u16, overruns, proto
CAN_ID_CONFIG_WRITE = 0x180    # [NEW v2] Pi->VCU key u8, value i32 (VCU refuses while DRIVE)
CAN_ID_PI_HEARTBEAT = 0x190    # [NEW v2] Pi->VCU counter u8, flags u8 (bit0 lora ok, bit1 backend ok)
# Stage 2d — additive frames (CAN protocol stays v2; old receivers ignore them)
CAN_ID_TRACK_POSITION = 0x145  # pos u16 (/65535) · lap u16 · track u8 · flags u8 · profile duty u8 % · model lat g i8 (0.05 g)
CAN_ID_LAP_TIMING = 0x146      # last lap ms u32 · current lap ms u32
CAN_ID_DYNO = 0x147            # odometer cm u32 · vibration RMS mg u16 · run state u8 · dyno-cal phase u8
CAN_ID_CONFIG_ACK = 0x181      # key u8 · status u8 · value i32
CAN_ID_CONFIG_REPORT = 0x182   # key u8 · value i32 · pending u8
CAN_ID_VCU_MODE = 0x148        # [FW 2.2] drive mode u8 · hw profile u8 · flags u8 · run laps u8 · TC cut % u8 · knob i16

DRIVER_FLAG_ARM = 0x01
DRIVER_FLAG_ESTOP = 0x02
DRIVER_FLAG_ESTOP_RESET = 0x04
CONFIG_KEY_AERO_MODE = 1       # 0 baseline, 1 AI-assist, 2 failsafe-hold

# Stage 2d tunables — identical to vcu_logic.h ConfigKey (checked by HARDWARE/tests)
CONFIG_KEY_NAMES = {
    1: "aeroMode", 2: "trackId", 3: "lapLengthCm", 4: "trackRun",
    10: "straightFrontCdeg", 11: "straightRearCdeg", 12: "cornerFrontCdeg", 13: "cornerRearCdeg",
    14: "asymGainCdegPerG", 15: "straightOpenDelayM", 16: "straightCloseEarlyM",
    17: "dutyCapPermille", 18: "dutyFloorPermille", 19: "wingSlewDps", 20: "steerGainPermille",
    21: "currentLimitCa",
    100: "dynoVmaxCkmh", 101: "dynoStartPermille", 102: "trackCount", 103: "wheelDiameterUm",
    # FW 2.2
    5: "driveMode", 6: "runCmd", 7: "runLaps", 8: "estop", 22: "tcSlipPermille",
    104: "hwProfile", 105: "wheelPpr", 106: "speedSource",   # 106 (FW 2.3.4): 1 = speed estimated from duty
    # FW 2.3 — motor from the dashboard, knob = steering, servo calibration
    9: "motorCmd", 23: "knobCmd", 24: "calCmd", 25: "drsCmd", 26: "demoCmd", 27: "aiSteerCmd",
    30: "steerLeftUs", 31: "steerCenterUs", 32: "steerRightUs",
    33: "flapFlFlatUs", 34: "flapFlMaxUs", 35: "flapFrFlatUs", 36: "flapFrMaxUs",
    37: "flapRlFlatUs", 38: "flapRlMaxUs", 39: "flapRrFlatUs", 40: "flapRrMaxUs",
    41: "steerLockCdeg", 42: "flapNeutralCdeg", 43: "flapSwingCdeg", 44: "knobCdegPerClick", 45: "knobReverse",
}
CONFIG_KEY_DRIVE_MODE, CONFIG_KEY_RUN_CMD, CONFIG_KEY_RUN_LAPS, CONFIG_KEY_ESTOP = 5, 6, 7, 8
DRIVE_MODE_NAMES = ["dyno", "track"]
HW_PROFILE_NAMES = ["prototype", "pcb_rev_b"]
# 0x148 flags (vcu_logic.h ModeFlags)
MF_MOTOR_POWER, MF_KILL_SWITCH_FITTED, MF_REMOTE_RUN, MF_TC_ACTIVE = 0x01, 0x02, 0x04, 0x08
MF_GROUND_LINK_OK, MF_AERO_STRAIGHT, MF_ENCODER_FITTED = 0x10, 0x20, 0x40
CONFIG_STATUS_NAMES = ["applied", "staged_next_lap", "rejected_range", "rejected_state", "rejected_unknown_key"]
RUN_STATE_NAMES = ["off", "track_drive", "bench_laps", "bench_motor", "dyno_cal", "demo"]
DYNO_CAL_PHASE_NAMES = ["idle", "find_start", "hold_cap", "done", "failed"]
TF_RUN_ACTIVE, TF_STRAIGHT_MODE, TF_BRAKING_ZONE, TF_CONFIG_PENDING = 0x01, 0x02, 0x04, 0x08
TF_BENCH_RUN, TF_SECTOR_SHIFT, TF_STALLED = 0x10, 5, 0x80

AI_REASON_NAMES = ["accepted", "not_populated", "low_confidence", "future_timestamp", "stale",
                   "non_finite", "out_of_range", "safe_state_override", "ai_mode_disabled", "none_received"]


@dataclass
class CanFrame:
    can_id: int
    data: bytes


def _round_half_away(v: float) -> int:
    """Same rounding as C++ std::lround() in vcu_logic.h clampI16/clampU16.
    (Stage 2: replaced int() truncation — 12.3*100 is 1229.999... in IEEE
    double, so truncation silently lost 0.01 deg and disagreed with float32.)"""
    if not math.isfinite(v):
        return 0
    return math.floor(v + 0.5) if v >= 0 else math.ceil(v - 0.5)


def _clamp_i16(v: float) -> int:
    return max(-32768, min(32767, _round_half_away(v)))


def _clamp_u16(v: float) -> int:
    return max(0, min(65535, _round_half_away(v)))


def can_decode_vehicle_state(frame: CanFrame):
    if frame.can_id != CAN_ID_VEHICLE_STATE or len(frame.data) < 5:
        return None
    d = frame.data
    state = d[0]
    fault_flags = d[1] | (d[2] << 8) | (d[3] << 16) | (d[4] << 24)
    return state, fault_flags


def can_decode_wheel_speed(frame: CanFrame):
    if frame.can_id != CAN_ID_WHEEL_SPEED or len(frame.data) < 8:
        return None
    fl, fr, rl, rr = struct.unpack("<hhhh", frame.data[:8])
    return fl / 100.0, fr / 100.0, rl / 100.0, rr / 100.0


def can_decode_imu_accel(frame: CanFrame):
    if frame.can_id != CAN_ID_IMU_ACCEL or len(frame.data) < 6:
        return None
    x, y, z = struct.unpack("<hhh", frame.data[:6])
    return x / 1000.0, y / 1000.0, z / 1000.0


def can_decode_imu_gyro(frame: CanFrame):
    if frame.can_id != CAN_ID_IMU_GYRO or len(frame.data) < 6:
        return None
    x, y, z = struct.unpack("<hhh", frame.data[:6])
    return x / 10.0, y / 10.0, z / 10.0


def can_decode_battery(frame: CanFrame):
    if frame.can_id != CAN_ID_BATTERY_POWER or len(frame.data) < 4:
        return None
    voltage_mv, current_ca = struct.unpack("<Hh", frame.data[:4])
    return voltage_mv / 1000.0, current_ca / 100.0  # v2: centi-amps


def can_decode_aero_target(frame: CanFrame):
    if frame.can_id != CAN_ID_AERO_TARGET or len(frame.data) < 8:
        return None
    lf, rf, lr, rr = struct.unpack("<hhhh", frame.data[:8])
    return lf / 100.0, rf / 100.0, lr / 100.0, rr / 100.0  # v2: centi-degrees


def can_decode_driver_inputs(frame: CanFrame):
    if frame.can_id != CAN_ID_DRIVER_INPUTS or len(frame.data) < 4:
        return None
    steering_milli_deg, throttle_pct, brake_pct = struct.unpack("<hBB", frame.data[:4])
    return steering_milli_deg / 1000.0, float(throttle_pct), float(brake_pct)


def can_decode_temperature(frame: CanFrame):
    if frame.can_id != CAN_ID_TEMPERATURE or len(frame.data) < 6:
        return None
    motor_deci_c, battery_deci_c, motor_current_ca = struct.unpack("<hhh", frame.data[:6])
    return motor_deci_c / 10.0, battery_deci_c / 10.0, motor_current_ca / 100.0  # v2: centi-amps


def can_encode_ai_recommendation(sequence: int, lf: float, rf: float, lr: float, rr: float,
                                  confidence: float) -> tuple[CanFrame, CanFrame]:
    seq = sequence & 0xFF
    frame_a = CanFrame(
        can_id=CAN_ID_AI_RECOMMENDATION_A,
        data=struct.pack("<Bhh", seq, _clamp_i16(lf * 100), _clamp_i16(rf * 100)) + b"\x00",
    )
    conf_byte = max(0, min(255, int(confidence * 255)))  # truncate, not round — matches firmware cast
    frame_b = CanFrame(
        can_id=CAN_ID_AI_RECOMMENDATION_B,
        data=struct.pack("<BhhB", seq, _clamp_i16(lr * 100), _clamp_i16(rr * 100), conf_byte),
    )
    return frame_a, frame_b


THERMAL_NO_LIMIT = 0xFFFF


def can_decode_vehicle_derived(frame: CanFrame):
    """0x141 — computed on the VCU (see vcu_logic.h slipRatioPermille /
    ThermalEstimator). Returns (slip_ratio, seconds_to_motor_limit,
    seconds_to_battery_limit); the two times are None when the temperature is
    not trending toward its limit. Stage 2d appends vibration RMS (mg) as bytes
    6-7; can_decode_vibration() reads it."""
    if frame.can_id != CAN_ID_VEHICLE_DERIVED or len(frame.data) < 6:
        return None
    slip, t_motor, t_batt = struct.unpack("<hHH", frame.data[:6])
    return (slip / 1000.0,
            None if t_motor == THERMAL_NO_LIMIT else t_motor,
            None if t_batt == THERMAL_NO_LIMIT else t_batt)


# --- Stage 2d decoders -----------------------------------------------------------
def can_decode_track_position(frame: CanFrame):
    if frame.can_id != CAN_ID_TRACK_POSITION or len(frame.data) < 8:
        return None
    pos, lap, track, flags, duty, latg = struct.unpack("<HHBBBb", frame.data[:8])
    return {"pos": pos / 65535.0, "lap": lap, "trackId": track, "flags": flags,
            "profileDutyPct": float(duty), "latGModel": latg * 0.05}


def can_decode_lap_timing(frame: CanFrame):
    if frame.can_id != CAN_ID_LAP_TIMING or len(frame.data) < 8:
        return None
    last_ms, cur_ms = struct.unpack("<II", frame.data[:8])
    return last_ms, cur_ms


def can_decode_dyno(frame: CanFrame):
    if frame.can_id != CAN_ID_DYNO or len(frame.data) < 8:
        return None
    odo_cm, vib_mg, run_state, cal_phase = struct.unpack("<IHBB", frame.data[:8])
    return {"odometerM": odo_cm / 100.0, "vibrationMg": float(vib_mg), "runState": run_state,
            "calPhase": cal_phase}


def can_decode_config_ack(frame: CanFrame):
    if frame.can_id != CAN_ID_CONFIG_ACK or len(frame.data) < 6:
        return None
    key, status, value = struct.unpack("<BBi", frame.data[:6])
    return {"key": key, "status": status, "value": value}


def can_decode_config_report(frame: CanFrame):
    if frame.can_id != CAN_ID_CONFIG_REPORT or len(frame.data) < 6:
        return None
    key, value, pending = struct.unpack("<BiB", frame.data[:6])
    return {"key": key, "value": value, "pending": bool(pending)}


def can_decode_vcu_mode(frame: CanFrame):
    """[FW 2.2] 0x148 -> dict, or None."""
    if frame.can_id != CAN_ID_VCU_MODE or len(frame.data) < 7:
        return None
    mode, hw, flags, laps, tc_cut, knob = struct.unpack("<BBBBBh", frame.data[:7])
    return {"driveMode": mode, "hwProfile": hw, "flags": flags, "runLaps": laps, "tcCutPct": tc_cut, "knob": knob}


def vcu_mode_fields(drive_mode: int, hw_profile: int, flags: int | None, run_laps: int | None,
                    tc_cut_pct: int | None, knob: int | None) -> dict:
    """[FW 2.2] dashboard `vcu` object. Shared shape with Ground Station/bridge.py (LoRa carries the
    drive mode and the motor-power bit only; the rest is None there)."""
    ks = None if flags is None else bool(flags & MF_KILL_SWITCH_FITTED)
    if ks is None and hw_profile in (0, 1):
        ks = hw_profile == 1
    return {
        "driveMode": DRIVE_MODE_NAMES[drive_mode] if 0 <= drive_mode < len(DRIVE_MODE_NAMES) else None,
        "hwProfile": HW_PROFILE_NAMES[hw_profile] if 0 <= hw_profile < len(HW_PROFILE_NAMES) else None,
        "killSwitchFitted": ks,
        "motorPower": None if flags is None or ks is False else bool(flags & MF_MOTOR_POWER),
        "remoteRun": None if flags is None else bool(flags & MF_REMOTE_RUN),
        "tcActive": None if flags is None else bool(flags & MF_TC_ACTIVE),
        "tcCutPct": tc_cut_pct,
        "groundLinkOk": None if flags is None else bool(flags & MF_GROUND_LINK_OK),
        "aeroStraight": None if flags is None else bool(flags & MF_AERO_STRAIGHT),
        "encoderFitted": None if flags is None else bool(flags & MF_ENCODER_FITTED),
        "knob": knob,
        "runLaps": run_laps,
    }


def track_flags_to_dict(flags: int) -> dict:
    return {"runActive": bool(flags & TF_RUN_ACTIVE), "straightMode": bool(flags & TF_STRAIGHT_MODE),
            "brakingZone": bool(flags & TF_BRAKING_ZONE), "configPending": bool(flags & TF_CONFIG_PENDING),
            "benchRun": bool(flags & TF_BENCH_RUN), "sector": (flags >> TF_SECTOR_SHIFT) & 0x03,
            "stalled": bool(flags & TF_STALLED)}


def can_decode_aero_measured(frame: CanFrame):
    if frame.can_id != CAN_ID_AERO_MEASURED or len(frame.data) < 8:
        return None
    lf, rf, lr, rr = struct.unpack("<hhhh", frame.data[:8])
    return lf / 100.0, rf / 100.0, lr / 100.0, rr / 100.0


def can_decode_imu_attitude(frame: CanFrame):
    if frame.can_id != CAN_ID_IMU_ATTITUDE or len(frame.data) < 6:
        return None
    r, p, y = struct.unpack("<hhh", frame.data[:6])
    return r / 10.0, p / 10.0, y / 10.0


def can_decode_fault_event(frame: CanFrame):
    if frame.can_id != CAN_ID_FAULT_EVENT or len(frame.data) < 6:
        return None
    flags, severity, state = struct.unpack("<IBB", frame.data[:6])
    return flags, severity, state


def can_decode_vcu_status(frame: CanFrame):
    if frame.can_id != CAN_ID_VCU_STATUS or len(frame.data) < 8:
        return None
    mode, reason, drv_ok, cal_mask, uptime_s, overruns, proto = struct.unpack("<BBBBHBB", frame.data[:8])
    return {
        "aeroMode": mode, "lastAiReason": AI_REASON_NAMES[reason] if reason < len(AI_REASON_NAMES) else str(reason),
        "driverLinkOk": bool(drv_ok), "servoFeedbackCalibratedMask": cal_mask, "uptimeS": uptime_s,
        "loopOverruns": overruns, "protocolVersion": proto,
    }


def can_encode_driver_command(sequence: int, steering_permille: int, throttle_permille: int,
                               flags: int) -> CanFrame:
    """Pi -> VCU. The VCU drops it if not refreshed within 250 ms, so this is
    only ever sent when a fresh command actually arrives — never replayed."""
    steer = max(-1000, min(1000, int(steering_permille)))
    thr = max(-1000, min(1000, int(throttle_permille)))
    return CanFrame(can_id=CAN_ID_DRIVER_COMMAND,
                    data=struct.pack("<BhhB", sequence & 0xFF, steer, thr, flags & 0xFF))


def can_encode_config_write(key: int, value: int) -> CanFrame:
    return CanFrame(can_id=CAN_ID_CONFIG_WRITE, data=struct.pack("<Bi", key & 0xFF, int(value)))


def can_encode_pi_heartbeat(counter: int, lora_ok: bool, backend_ok: bool) -> CanFrame:
    return CanFrame(can_id=CAN_ID_PI_HEARTBEAT,
                    data=bytes([counter & 0xFF, (0x01 if lora_ok else 0) | (0x02 if backend_ok else 0)]))


class CanInterface:
    """Common interface both the mock and (future) real backends implement:
    send(frame) / poll() -> list[CanFrame]. main() only ever talks to this
    interface, never to mock/real details directly."""

    def send(self, frame: CanFrame) -> None:
        raise NotImplementedError

    def poll(self) -> list[CanFrame]:
        raise NotImplementedError

    def seconds_since_last_rx(self) -> float | None:
        """None = nothing ever received. Used for the link-freshness gate."""
        return None


class MockCanInterface(CanInterface):
    """SAMPLE CAN TRANSPORT. No MCP2515 controller is wired up yet, so this
    is an in-process loopback queue standing in for the physical bus. The
    encode/decode functions above are the real protocol and do not change
    when a real SocketCAN/MCP2515 backend replaces this class."""

    def __init__(self):
        self._queue: list[CanFrame] = []

    def send(self, frame: CanFrame) -> None:
        self._queue.append(frame)

    def poll(self) -> list[CanFrame]:
        out, self._queue = self._queue, []
        return out

    def seconds_since_last_rx(self) -> float | None:
        return 0.0  # in-process loopback is always "fresh" (unchanged Stage-1 behaviour)


class SocketCanInterface(CanInterface):
    """REAL transport (Stage 2): python-can over Linux SocketCAN. On the Pi 4B
    the CAN HAT's MCP2515 is exposed as `can0` by the device-tree overlay
    (see BringUpGuide.md: dtoverlay=mcp2515-can0,...; `ip link set can0 up
    type can bitrate 500000`). The bitrate is configured by `ip link`, not
    here — python-can's socketcan backend ignores it — it is kept only for
    logging/documentation.

    `bustype="virtual"` runs the identical code against python-can's
    in-process virtual bus, which is how HARDWARE/tests exercise this class
    without a CAN HAT. HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED on can0."""

    def __init__(self, channel: str, bitrate: int, bustype: str = "socketcan"):
        try:
            import can  # python-can, lazy so the rest of this file runs without it
        except ImportError as exc:
            raise SystemExit("python-can is required for --can-interface socketcan: "
                             "pip install python-can --break-system-packages") from exc
        self._can = can
        self.channel = channel
        self.bitrate = bitrate
        try:
            self._bus = can.Bus(channel=channel, interface=bustype, receive_own_messages=False)
        except (OSError, can.CanError) as exc:
            raise SystemExit(f"Could not open CAN interface '{channel}' ({exc}). Is the CAN HAT overlay "
                             f"loaded and the link up? `sudo ip link set {channel} up type can bitrate "
                             f"{bitrate}` — or use --can-interface wifi for the Stage-1 bench transport.") from exc
        self._last_rx_monotonic: float | None = None
        self.tx_errors = 0
        self.rx_frames = 0

    def send(self, frame: CanFrame) -> None:
        msg = self._can.Message(arbitration_id=frame.can_id, data=frame.data, is_extended_id=False)
        try:
            self._bus.send(msg, timeout=0.005)
        except self._can.CanError:
            self.tx_errors += 1  # e.g. TX buffer full / no ACK — never crash the gateway

    def poll(self) -> list[CanFrame]:
        frames: list[CanFrame] = []
        while True:
            msg = self._bus.recv(timeout=0.0)
            if msg is None:
                break
            if msg.is_extended_id or msg.is_remote_frame or msg.is_error_frame:
                continue
            self._last_rx_monotonic = time.monotonic()
            self.rx_frames += 1
            frames.append(CanFrame(can_id=msg.arbitration_id, data=bytes(msg.data)))
        return frames

    def seconds_since_last_rx(self) -> float | None:
        if self._last_rx_monotonic is None:
            return None
        return time.monotonic() - self._last_rx_monotonic

    def close(self) -> None:
        self._bus.shutdown()


class WifiCanInterface(CanInterface):
    """TEMPORARY transport: WiFi UDP standing in for the physical CAN bus
    between this gateway and the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h) (see project brief
    Section 11). Wire format is byte-identical on both ends:
    [id_lo, id_hi, dlc, data(8 bytes)] as one UDP datagram per CAN frame —
    see wifiTransmitCanFrame()/pollAiRecommendationFromWifi() in the VCU firmware.

    The ESP32's source address is learned automatically from the first
    packet received (DHCP gives it a dynamic IP), so no static ESP32 IP is
    required; --esp32-ip/--esp32-port only seed a fallback used if we need
    to send before anything has been received yet."""

    def __init__(self, listen_port: int, esp32_ip: str | None = None, esp32_port: int = 5006):
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.bind(("0.0.0.0", listen_port))
        self._esp32_addr = (esp32_ip, esp32_port) if esp32_ip else None
        self._listen_port = listen_port
        self._last_rx_monotonic: float | None = None  # None = nothing received yet

    def send(self, frame: CanFrame) -> None:
        if self._esp32_addr is None:
            return  # haven't heard from an ESP32 yet and no fallback IP configured
        payload = frame.data + b"\x00" * (8 - len(frame.data))
        pkt = bytes([frame.can_id & 0xFF, (frame.can_id >> 8) & 0xFF, len(frame.data)]) + payload
        self._sock.sendto(pkt, self._esp32_addr)

    def poll(self) -> list[CanFrame]:
        frames: list[CanFrame] = []
        while True:
            try:
                data, addr = self._sock.recvfrom(64)
            except BlockingIOError:
                break
            except OSError:
                break
            self._esp32_addr = (addr[0], self._esp32_addr[1] if self._esp32_addr else 5006)
            self._last_rx_monotonic = time.monotonic()
            if len(data) < 3:
                continue
            can_id = data[0] | (data[1] << 8)
            dlc = min(data[2], 8)
            frames.append(CanFrame(can_id=can_id, data=data[3:3 + dlc]))
        return frames

    def seconds_since_last_rx(self) -> float | None:
        """None if no ESP32 packet has ever been received."""
        if self._last_rx_monotonic is None:
            return None
        return time.monotonic() - self._last_rx_monotonic


# =============================================================================
# SECTION 3: LoRa
# Byte-identical framing/CRC to Documentation/LoRaProtocol.md and to
# the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h). Ground-side stays read-only by design: nothing
# here can encode an actuator command.
# =============================================================================

LORA_MAGIC = 0xA5
LORA_PROTOCOL_VERSION = 3  # v3 (Stage 2d): TELEMETRY_COMPACT carries track position, lap timing, wheel speeds,
                           # flap targets, vibration and one rotating VCU config value; new CONFIG_ACK (9).
                           # v2 packets are rejected (both ends must be updated together).
LORA_HEADER_LEN = 9
LORA_CRC_LEN = 2
LORA_MAX_PAYLOAD = 64


class LoRaMsgType:
    TELEMETRY_COMPACT = 1
    FAULT_EVENT = 2
    CONFIG_READ_REQ = 3
    CONFIG_READ_RESP = 4
    HEARTBEAT = 5
    # [SIMULATION-ONLY — NOT IN Documentation/LoRaProtocol.md] Ground-station
    # wing-angle recommendation, vehicle-bound. Documentation/LoRaProtocol.md's
    # "Safety boundary (Phase 5, non-negotiable)" section currently states
    # ground->vehicle traffic is restricted to CONFIG_READ_REQ (read-only) BY
    # DESIGN, with no message type defined for anything actuator-adjacent.
    # This value exists only to let the temporary UDP transport (UdpLoRaRadio,
    # below) exercise the "ground -> Pi -> CAN -> VCU" leg end-to-end for
    # testing. It is deliberately NOT added to LoRaProtocol.md's message-type
    # table. Before this is treated as anything other than a test fixture,
    # someone needs to explicitly reconcile it with that safety boundary
    # (e.g. formalize it as a recommendation-only, VCU-validated type there).
    # The existing safety invariant is unaffected either way: as today, the
    # VCU's own safety/limits stage — not this transport, and not this
    # gateway — decides whether a recommendation is ever applied (see
    # tests/vcu_sim.py's ai_recommendation_is_valid()).
    AI_RECOMMENDATION_SIM = 6
    # STAGE 2: formalized in Documentation/LoRaProtocol.md as a
    # RECOMMENDATION-ONLY message (the VCU validates it exactly like any other
    # AI recommendation). Same value, second name — the old name is kept so
    # nothing that already uses it breaks.
    AI_RECOMMENDATION = 6
    DRIVER_COMMAND = 7   # [NEW] ground->vehicle, forwarded to CAN 0x118 (VCU 250 ms timeout applies)
    CONFIG_WRITE = 8     # [NEW] ground->vehicle, forwarded to CAN 0x180 (VCU validates, answers 0x181)
    CONFIG_ACK = 9       # [Stage 2d] vehicle->ground, CAN 0x181 relayed: key u8, status u8, value i32


def lora_crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


@dataclass
class LoRaPacket:
    version: int
    msg_type: int
    sequence: int
    timestamp_ms: int
    payload: bytes = field(default_factory=bytes)


def lora_encode(pkt: LoRaPacket) -> bytes:
    if len(pkt.payload) > LORA_MAX_PAYLOAD:
        raise ValueError("payload exceeds LORA_MAX_PAYLOAD")
    header = struct.pack("<BBBBIB", LORA_MAGIC, pkt.version, pkt.msg_type, pkt.sequence & 0xFF,
                          pkt.timestamp_ms & 0xFFFFFFFF, len(pkt.payload))
    body = header + pkt.payload
    crc = lora_crc16_ccitt(body)
    return body + struct.pack("<H", crc)


def lora_decode(buf: bytes) -> LoRaPacket | None:
    if len(buf) < LORA_HEADER_LEN + LORA_CRC_LEN or buf[0] != LORA_MAGIC:
        return None
    version = buf[1]
    if version != LORA_PROTOCOL_VERSION:
        return None
    payload_len = buf[8]
    total = LORA_HEADER_LEN + payload_len + LORA_CRC_LEN
    if payload_len > LORA_MAX_PAYLOAD or len(buf) < total:
        return None
    body = buf[:LORA_HEADER_LEN + payload_len]
    expected_crc = lora_crc16_ccitt(body)
    (actual_crc,) = struct.unpack("<H", buf[LORA_HEADER_LEN + payload_len:total])
    if expected_crc != actual_crc:
        return None
    _, _, msg_type, sequence, timestamp_ms, _ = struct.unpack("<BBBBIB", body[:LORA_HEADER_LEN])
    return LoRaPacket(version=version, msg_type=msg_type, sequence=sequence,
                       timestamp_ms=timestamp_ms, payload=body[LORA_HEADER_LEN:])


# Stage-1 heartbeat sample (the real radio is SX1278 — see SX1278SpiRadio). Not used by run() any more.
class MockLoRaInterface:
    """SAMPLE LoRa TRANSPORT. Stage-1 stub, superseded by SX1278SpiRadio — this
    just encodes/decodes in-process. transmit()/receive() are the
    replacement points for a real radio.transmit()/radio.receive()."""

    def __init__(self):
        self._sequence = 0
        self._last_rx_ms = 0

    def send_heartbeat(self, now_ms: int) -> bytes:
        pkt = LoRaPacket(version=LORA_PROTOCOL_VERSION, msg_type=LoRaMsgType.HEARTBEAT,
                          sequence=self._sequence, timestamp_ms=now_ms)
        self._sequence += 1
        buf = lora_encode(pkt)
        # (Stage-1 note) a real radio would call
        # radio.transmit(buf) here.
        self._last_rx_ms = now_ms  # sample assumes ground station promptly acks
        return buf


# =============================================================================
# SECTION 3b: TELEMETRY_COMPACT / AI_RECOMMENDATION_SIM payload codecs
# These pack a VehicleTelemetry sample (vehicle->ground) and a wing-angle
# recommendation (ground->vehicle, see AI_RECOMMENDATION_SIM above) into the
# LoRaPacket.payload bytes, matching the *envelope* Documentation/LoRaProtocol.md
# defines (magic/version/msg_type/sequence/timestamp/len/crc) exactly, but —
# per that doc — "there is still no defined, implemented payload *schema* for
# TELEMETRY_COMPACT" -> this IS that schema's first implementation, scoped to
# what the ground bridge/dashboard actually need, kept well under
# LORA_MAX_PAYLOAD (64B) to stay honest about real LoRa bandwidth. Field
# order/scaling is local to this payload only (independent of the CAN
# byte layout) and is duplicated byte-for-byte in HARDWARE/ground_station/
# bridge.py per this project's existing "self-contained, no shared library"
# convention (see module docstring above).
# =============================================================================

# v3 layout (64 bytes = LORA_MAX_PAYLOAD). Table: SOFTWARE/Documentation/LoRaProtocol.md §4.
_TELEMETRY_COMPACT_FMT = "<BHhhBBhhhhhhHhhhBBBBBBBBhhhhHBBBBHHBiHh"
_AI_RECOMMENDATION_FMT = "<hhhhB"
_WING_Q = 5.0   # flap angles as u8 in 0.2 deg steps (0..51 deg)


def _u8(v: float) -> int:
    return max(0, min(255, _round_half_away(v)))


def lora_encode_telemetry_compact(vt: "VehicleTelemetry") -> bytes:
    """vehicle -> ground, 64 bytes. vt.config_report_key/value carry ONE VCU config
    value per packet (the VCU's own round-robin), so the ground side rebuilds the full
    table every few seconds without a request/response exchange."""
    flags2 = ((1 if vt.brake_pct > 0 else 0) | ((max(0, vt.aero_mode) & 0x03) << 1)
              | ((vt.last_ai_reason_index & 0x0F) << 3))
    # [FW 2.2] bits 6/7 of run_cal: drive mode (1 = track) and motor power (kill switch on)
    run_cal = ((vt.run_state & 0x07) | ((vt.cal_phase & 0x07) << 3) | ((1 if vt.drive_mode == 1 else 0) << 6)
               | ((1 if (vt.mode_flags or 0) & MF_MOTOR_POWER else 0) << 7))
    return struct.pack(
        _TELEMETRY_COMPACT_FMT,
        vt.vehicle_state_index & 0xFF,
        vt.fault_flags & 0xFFFF,
        _clamp_i16(vt.speed_kmh * 100),                       # v3: centi-km/h (dyno speeds are small)
        _clamp_i16(vt.steering_angle_deg * 10),
        max(0, min(255, int(vt.throttle_pct))),
        flags2 & 0xFF,
        _clamp_i16(vt.accel_x_g * 1000), _clamp_i16(vt.accel_y_g * 1000), _clamp_i16(vt.accel_z_g * 1000),
        _clamp_i16(vt.roll_deg * 10), _clamp_i16(vt.pitch_deg * 10), _clamp_i16(vt.yaw_deg * 10),
        _clamp_u16(vt.battery_voltage_v * 1000),
        _clamp_i16(vt.battery_current_a * 100),
        _clamp_i16(vt.motor_temperature_c * 10),
        _clamp_i16(vt.battery_temperature_c * 10),
        _u8(vt.wing_lf_deg * _WING_Q), _u8(vt.wing_rf_deg * _WING_Q),
        _u8(vt.wing_lr_deg * _WING_Q), _u8(vt.wing_rr_deg * _WING_Q),
        _u8(vt.wing_target_lf_deg * _WING_Q), _u8(vt.wing_target_rf_deg * _WING_Q),
        _u8(vt.wing_target_lr_deg * _WING_Q), _u8(vt.wing_target_rr_deg * _WING_Q),
        _clamp_i16(vt.wheel_speed_fl_kmh * 100), _clamp_i16(vt.wheel_speed_fr_kmh * 100),
        _clamp_i16(vt.wheel_speed_rl_kmh * 100), _clamp_i16(vt.wheel_speed_rr_kmh * 100),
        _clamp_u16(vt.track_pos * 65535), vt.lap & 0xFF, vt.track_id & 0xFF, vt.track_flags & 0xFF,
        run_cal & 0xFF,
        min(65535, max(0, int(vt.current_lap_ms // 10))), min(65535, max(0, int(vt.last_lap_ms // 10))),
        vt.config_report_key & 0xFF, int(vt.config_report_value),
        _clamp_u16(vt.vibration_mg),
        _clamp_i16(vt.gyro_z_dps * 10),
    )


def lora_decode_telemetry_compact(payload: bytes) -> dict | None:
    """ground side of lora_encode_telemetry_compact. Returns a flat dict of
    already-unscaled values (matching VehicleTelemetry field names) or None
    if the payload is the wrong size (e.g. a stale/other-version sender)."""
    size = struct.calcsize(_TELEMETRY_COMPACT_FMT)
    if len(payload) < size:
        return None
    (state_index, fault_flags, speed_x100, steer_x10, throttle, flags2,
     ax, ay, az, roll_x10, pitch_x10, yaw_x10, volt_mv, batt_ca, motor_t_x10, batt_t_x10,
     wlf, wrf, wlr, wrr, tlf, trf, tlr, trr, fl, fr, rl, rr,
     pos, lap, track, tflags, run_cal, cur_cs, last_cs, cfg_key, cfg_val, vib, gz_x10
     ) = struct.unpack(_TELEMETRY_COMPACT_FMT, payload[:size])
    return {
        "state_index": state_index, "fault_flags": fault_flags,
        "speed_kmh": speed_x100 / 100.0, "steering_angle_deg": steer_x10 / 10.0,
        "throttle_pct": float(throttle), "brake_pct": 100.0 if flags2 & 0x01 else 0.0,
        "aero_mode": (flags2 >> 1) & 0x03, "last_ai_reason_index": (flags2 >> 3) & 0x0F,
        "accel_x_g": ax / 1000.0, "accel_y_g": ay / 1000.0, "accel_z_g": az / 1000.0,
        "roll_deg": roll_x10 / 10.0, "pitch_deg": pitch_x10 / 10.0, "yaw_deg": yaw_x10 / 10.0,
        "battery_voltage_v": volt_mv / 1000.0, "battery_current_a": batt_ca / 100.0,
        "motor_current_a": batt_ca / 100.0,   # no motor current sensor in the BOM: pack current
        "motor_temperature_c": motor_t_x10 / 10.0, "battery_temperature_c": batt_t_x10 / 10.0,
        "wing_lf_deg": wlf / _WING_Q, "wing_rf_deg": wrf / _WING_Q,
        "wing_lr_deg": wlr / _WING_Q, "wing_rr_deg": wrr / _WING_Q,
        "wing_target_lf_deg": tlf / _WING_Q, "wing_target_rf_deg": trf / _WING_Q,
        "wing_target_lr_deg": tlr / _WING_Q, "wing_target_rr_deg": trr / _WING_Q,
        "wheel_speed_fl_kmh": fl / 100.0, "wheel_speed_fr_kmh": fr / 100.0,
        "wheel_speed_rl_kmh": rl / 100.0, "wheel_speed_rr_kmh": rr / 100.0,
        "track_pos": pos / 65535.0, "lap": lap, "track_id": track, "track_flags": tflags,
        "run_state": run_cal & 0x07, "cal_phase": (run_cal >> 3) & 0x07,
        "drive_mode": (run_cal >> 6) & 0x01, "motor_power": bool(run_cal & 0x80),
        "current_lap_ms": cur_cs * 10, "last_lap_ms": last_cs * 10,
        "config_report_key": cfg_key, "config_report_value": cfg_val,
        "vibration_mg": float(vib), "gyro_z_dps": gz_x10 / 10.0,
    }


def lora_encode_config_ack(key: int, status: int, value: int) -> bytes:
    return struct.pack("<BBi", key & 0xFF, status & 0xFF, int(value))


def lora_decode_config_ack(payload: bytes) -> dict | None:
    if len(payload) < 6:
        return None
    key, status, value = struct.unpack("<BBi", payload[:6])
    return {"key": key, "status": status, "value": value}


def lora_encode_ai_recommendation(lf: float, rf: float, lr: float, rr: float, confidence: float) -> bytes:
    """ground -> vehicle. See AI_RECOMMENDATION_SIM's caveat above — this is
    a recommendation, not a command; the VCU still validates it, unchanged."""
    conf_byte = max(0, min(255, int(confidence * 255)))
    return struct.pack(_AI_RECOMMENDATION_FMT, _clamp_i16(lf * 10), _clamp_i16(rf * 10),
                        _clamp_i16(lr * 10), _clamp_i16(rr * 10), conf_byte)


def lora_decode_ai_recommendation(payload: bytes) -> dict | None:
    size = struct.calcsize(_AI_RECOMMENDATION_FMT)
    if len(payload) < size:
        return None
    lf_x10, rf_x10, lr_x10, rr_x10, conf_byte = struct.unpack(_AI_RECOMMENDATION_FMT, payload[:size])
    return {"lf_deg": lf_x10 / 10.0, "rf_deg": rf_x10 / 10.0, "lr_deg": lr_x10 / 10.0,
            "rr_deg": rr_x10 / 10.0, "confidence": conf_byte / 255.0}


def lora_encode_driver_command(steering_permille: int, throttle_permille: int, flags: int) -> bytes:
    return struct.pack("<hhB", max(-1000, min(1000, int(steering_permille))),
                       max(-1000, min(1000, int(throttle_permille))), flags & 0xFF)


def lora_decode_driver_command(payload: bytes) -> dict | None:
    if len(payload) < 5:
        return None
    steer, thr, flags = struct.unpack("<hhB", payload[:5])
    if not (-1000 <= steer <= 1000 and -1000 <= thr <= 1000):
        return None
    return {"steering_permille": steer, "throttle_permille": thr, "flags": flags}


def lora_encode_config_write(key: int, value: int) -> bytes:
    return struct.pack("<Bi", key & 0xFF, int(value))


def lora_decode_config_write(payload: bytes) -> dict | None:
    if len(payload) < 5:
        return None
    key, value = struct.unpack("<Bi", payload[:5])
    return {"key": key, "value": value}


def lora_time_on_air_ms(payload_len: int, sf: int = 7, bw_hz: int = 250_000, cr_denom: int = 5,
                        preamble: int = 8, explicit_header: bool = True, crc: bool = True) -> float:
    """Semtech SX126x datasheet time-on-air formula. Used to sanity-check the
    uplink rate so the half-duplex link still has room for the downlink."""
    t_sym = (2 ** sf) / bw_hz
    de = 1 if (sf >= 11 and bw_hz <= 125_000) else 0
    ih = 0 if explicit_header else 1
    num = 8 * payload_len - 4 * sf + 28 + (16 if crc else 0) - 20 * ih
    n_payload = 8 + max(math.ceil(num / (4 * (sf - 2 * de))) * cr_denom, 0)
    return ((preamble + 4.25) + n_payload) * t_sym * 1000.0


# --- SLIP (RFC 1055) framing for UART/USB LoRa modems ---------------------------
SLIP_END, SLIP_ESC, SLIP_ESC_END, SLIP_ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
LINK_STATS_MAGIC = 0xB5  # frame from the ground radio bridge firmware: [0xB5, rssi i16 (x10 dBm), snr i8 (x4 dB)]


def slip_encode(data: bytes) -> bytes:
    out = bytearray([SLIP_END])
    for b in data:
        if b == SLIP_END:
            out += bytes([SLIP_ESC, SLIP_ESC_END])
        elif b == SLIP_ESC:
            out += bytes([SLIP_ESC, SLIP_ESC_ESC])
        else:
            out.append(b)
    out.append(SLIP_END)
    return bytes(out)


class SlipDecoder:
    """Incremental decoder: feed() arbitrary chunks, get back complete frames."""

    def __init__(self, max_len: int = 512):
        self._buf = bytearray()
        self._esc = False
        self._max = max_len

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for b in chunk:
            if b == SLIP_END:
                if self._buf:
                    frames.append(bytes(self._buf))
                self._buf.clear()
                self._esc = False
            elif self._esc:
                self._buf.append(SLIP_END if b == SLIP_ESC_END else SLIP_ESC if b == SLIP_ESC_ESC else b)
                self._esc = False
            elif b == SLIP_ESC:
                self._esc = True
            else:
                self._buf.append(b)
                if len(self._buf) > self._max:  # garbage / lost END: resync
                    self._buf.clear()
        return frames


class _VehicleRadioBase:
    """Vehicle-side LoRa logic shared by every backend: packet envelope,
    sequence numbers, duplicate drop, downlink decode. Backends implement
    _tx(bytes) and _rx() -> list[bytes]."""

    def __init__(self):
        self._sequence = 0
        self._last_downlink_monotonic: float | None = None
        self._last_downlink_sequence: int | None = None
        self.rssi_dbm: float | None = None
        self.snr_db: float | None = None
        self.packets_sent = 0
        self.packets_received = 0
        self.decode_errors = 0
        self._pending_driver: list[dict] = []
        self._pending_config: list[dict] = []

    # --- backend hooks ---
    def _tx(self, data: bytes) -> None:
        raise NotImplementedError

    def _rx(self) -> list[bytes]:
        raise NotImplementedError

    # --- public API (unchanged names from Stage 1) ---
    def _send(self, msg_type: int, payload: bytes, now_ms: int) -> None:
        pkt = LoRaPacket(version=LORA_PROTOCOL_VERSION, msg_type=msg_type, sequence=self._sequence,
                         timestamp_ms=now_ms, payload=payload)
        self._sequence = (self._sequence + 1) & 0xFF
        self._tx(lora_encode(pkt))
        self.packets_sent += 1

    def send_telemetry(self, vt: "VehicleTelemetry", now_ms: int) -> None:
        self._send(LoRaMsgType.TELEMETRY_COMPACT, lora_encode_telemetry_compact(vt), now_ms)

    def send_fault_event(self, fault_flags: int, now_ms: int) -> None:
        self._send(LoRaMsgType.FAULT_EVENT, struct.pack("<H", fault_flags & 0xFFFF), now_ms)

    def send_config_ack(self, key: int, status: int, value: int, now_ms: int) -> None:
        self._send(LoRaMsgType.CONFIG_ACK, lora_encode_config_ack(key, status, value), now_ms)

    def _pump(self) -> list[dict]:
        recs: list[dict] = []
        for data in self._rx():
            pkt = lora_decode(data)
            if pkt is None:
                self.decode_errors += 1
                continue
            if pkt.msg_type not in (LoRaMsgType.AI_RECOMMENDATION, LoRaMsgType.DRIVER_COMMAND,
                                    LoRaMsgType.CONFIG_WRITE, LoRaMsgType.HEARTBEAT):
                continue
            if self._last_downlink_sequence == pkt.sequence:
                continue  # duplicate — drop
            self._last_downlink_sequence = pkt.sequence
            self._last_downlink_monotonic = time.monotonic()
            self.packets_received += 1
            if pkt.msg_type == LoRaMsgType.HEARTBEAT:
                continue  # ground keep-alive: only proves the downlink works (lora_ok bit)
            if pkt.msg_type == LoRaMsgType.AI_RECOMMENDATION:
                rec = lora_decode_ai_recommendation(pkt.payload)
                if rec is not None:
                    recs.append(rec)
            elif pkt.msg_type == LoRaMsgType.DRIVER_COMMAND:
                d = lora_decode_driver_command(pkt.payload)
                if d is not None:
                    self._pending_driver.append(d)
            else:
                c = lora_decode_config_write(pkt.payload)
                if c is not None:
                    self._pending_config.append(c)
        return recs

    def poll_recommendations(self) -> list[dict]:
        return self._pump()

    def take_driver_commands(self) -> list[dict]:
        out, self._pending_driver = self._pending_driver, []
        return out

    def take_config_writes(self) -> list[dict]:
        out, self._pending_config = self._pending_config, []
        return out

    def seconds_since_last_downlink(self) -> float | None:
        if self._last_downlink_monotonic is None:
            return None
        return time.monotonic() - self._last_downlink_monotonic

    def link_stats(self) -> dict:
        return {"rssi": self.rssi_dbm, "snr": self.snr_db, "packetsSent": self.packets_sent,
                "packetsReceived": self.packets_received, "decodeErrors": self.decode_errors}


SX1278_SPI_HZ = 1_000_000  # 1 MHz: robust over 10-20 cm jumper wires (Ra-02 max is 10 MHz)


class SX1278SpiRadio(_VehicleRadioBase):
    """REAL radio: Semtech SX1278 (Ai-Thinker Ra-02, 433 MHz) on the Pi's SPI
    bus via LoRaRF (pip install LoRaRF spidev RPi.GPIO).

    Wiring is forced by the CAN HAT, which owns SPI0 with BOTH chip selects
    plus GPIO23/25, so the radio lives on **SPI1**:
        NSS  GPIO18 (SPI1 CE0, /dev/spidev1.0 — needs dtoverlay=spi1-1cs)
        SCK  GPIO21   MOSI GPIO20   MISO GPIO19
        RST  GPIO17   DIO0 GPIO27 (RxDone/TxDone)
    Change them with the --sx1278-* flags; see PinoutAndWiring.md.

    The SX1278 has no BUSY line, no TCXO control and no TXEN/RXEN pins — it
    switches its own RF path — so this class is simpler than the SX1262 driver it
    replaces. DIO0 is wired but RX is polled through the IRQ flags register, so
    the gateway never blocks waiting for an edge.

    Half-duplex: stays in RX-continuous; each _tx() transmits, waits for
    TX_DONE (bounded), then returns to RX.

    Air settings MUST match the ground bridge firmware. NOT HARDWARE VALIDATED.
    """

    def __init__(self, cfg: "LoRaConfig", spi_bus: int = 1, spi_cs: int = 0, reset_pin: int = 17,
                 dio0_pin: int = 27, tx_power_pa_boost: bool = True):
        super().__init__()
        try:
            from LoRaRF import SX127x  # lazy: only on the Pi
        except ImportError as exc:
            raise SystemExit("LoRaRF is required for --lora-backend sx1278: "
                             "pip install LoRaRF spidev RPi.GPIO --break-system-packages") from exc
        self._cfg = cfg
        r = SX127x()
        # LoRaRF's default SPI clock is 7.8 MHz — too fast for jumper wires to a
        # Ra-02. begin() calls self.setSpi(bus, cs) with that default, so shadow
        # it on this instance with a slower clock before begin() runs.
        _orig_set_spi = r.setSpi
        r.setSpi = lambda bus, cs, speed=SX1278_SPI_HZ: _orig_set_spi(bus, cs, speed)
        # irq=-1 keeps LoRaRF in polled mode; DIO0 is still wired for future use.
        if not r.begin(spi_bus, spi_cs, reset_pin, -1, -1, -1):
            raise SystemExit("SX1278 did not respond on SPI — check wiring, dtoverlay=spi1-1cs, "
                             "and that the module is powered from 3.3 V (see PinoutAndWiring.md)")
        r.setFrequency(cfg.frequency_hz)
        # Ra-02 routes its output through the PA_BOOST pin, not RFO.
        r.setTxPower(cfg.tx_power_dbm, r.TX_POWER_PA_BOOST if tx_power_pa_boost else r.TX_POWER_RFO)
        r.setRxGain(r.RX_GAIN_BOOSTED, r.RX_GAIN_AUTO)
        r.setLoRaModulation(cfg.spreading_factor, cfg.bandwidth_hz, cfg.coding_rate, False)
        r.setLoRaPacket(r.HEADER_EXPLICIT, cfg.preamble, LORA_HEADER_LEN + LORA_MAX_PAYLOAD + LORA_CRC_LEN,
                        True, False)
        r.setSyncWord(cfg.sync_word)
        # FIX (Stage 2d): LoRaRF's begin() only pulses RESET and never reads the chip,
        # so a miswired Ra-02 still reported "SX1278 up". RegVersion (0x42) must read
        # 0x12 on an SX1276/77/78/79; 0x00 or 0xFF means no SPI conversation at all.
        version = r.readRegister(0x42)
        if version != 0x12:
            raise SystemExit(
                f"SX1278 RegVersion(0x42) read 0x{version:02X}, expected 0x12 — the radio is not talking on SPI. "
                "Check: 3.3 V (NEVER 5 V) on VCC, NSS->GPIO18 (pin 12), SCK->GPIO21 (pin 40), MOSI->GPIO20 "
                "(pin 38), MISO->GPIO19 (pin 35), RST->GPIO17 (pin 11), GND, and dtoverlay=spi1-1cs in "
                "/boot/firmware/config.txt (see VCU_final_connections.txt §7).")
        self._r = r
        self._dio0 = dio0_pin
        r.request(r.RX_CONTINUOUS)
        logger.info("SX1278 up: %.3f MHz SF%d BW%dk CR4/%d %d dBm (DIO0 on GPIO%d)",
                    cfg.frequency_hz / 1e6, cfg.spreading_factor, cfg.bandwidth_hz // 1000,
                    cfg.coding_rate, cfg.tx_power_dbm, dio0_pin)

    def _tx(self, data: bytes) -> None:
        r = self._r
        # The radio sits in RX-continuous. Writing the FIFO and switching to TX
        # from there lets an arriving packet overwrite the TX buffer, and the
        # SX127x expects mode changes via STANDBY — so go to standby first and
        # point the TX base at the start of the FIFO.
        r.standby()
        r.writeRegister(r.REG_FIFO_ADDR_PTR, 0x00)
        r.beginPacket()
        r.put(data)
        r.endPacket()
        toa_s = lora_time_on_air_ms(len(data), self._cfg.spreading_factor, self._cfg.bandwidth_hz,
                                    self._cfg.coding_rate, self._cfg.preamble) / 1000.0
        r.wait(max(0.2, 3 * toa_s))  # bounded: never hang the gateway on a radio fault
        r.request(r.RX_CONTINUOUS)

    def _rx(self) -> list[bytes]:
        """Polled read straight from the registers. LoRaRF's own wait() spins
        without a timeout when no IRQ pin is configured, which would stall the
        gateway's other tasks — so the FIFO is read here instead."""
        r = self._r
        flags = r.readRegister(r.REG_IRQ_FLAGS)
        if not flags & r.IRQ_RX_DONE:
            return []
        r.writeRegister(r.REG_IRQ_FLAGS, 0xFF)  # clear all flags
        if flags & r.IRQ_CRC_ERR:
            self.decode_errors += 1
            return []
        r.writeRegister(r.REG_FIFO_ADDR_PTR, r.readRegister(r.REG_FIFO_RX_CURRENT_ADDR))
        length = r.readRegister(r.REG_RX_NB_BYTES)
        data = bytes(r.readRegister(r.REG_FIFO) for _ in range(length)) if length else b""
        self.rssi_dbm = r.packetRssi()
        snr_raw = r.readRegister(r.REG_PKT_SNR_VALUE)  # two's complement, 0.25 dB/LSB
        self.snr_db = (snr_raw - 256 if snr_raw > 127 else snr_raw) / 4.0  # LoRaRF.snr() treats it as unsigned
        return [data] if data else []


class SerialSlipRadio(_VehicleRadioBase):
    """REAL radio (Stage 2): a UART/USB LoRa modem carrying SLIP-framed
    packets — e.g. a transparent-mode UART LoRa module (on /dev/serial0) or
    the provided ESP32+SX1278 bridge firmware
    (HARDWARE/Ground Station/ground_radio_bridge). Both ends must use the
    SAME modem family; see LoRaProtocol.md 'compatibility matrix'.
    HARDWARE-DEPENDENT / NOT HARDWARE VALIDATED."""

    def __init__(self, port: str, baud: int = 115200):
        super().__init__()
        try:
            import serial  # pyserial
        except ImportError as exc:
            raise SystemExit("pyserial is required for --lora-backend serial: pip install pyserial") from exc
        self._ser = serial.Serial(port, baud, timeout=0)
        self._dec = SlipDecoder()

    def _tx(self, data: bytes) -> None:
        self._ser.write(slip_encode(data))

    def _rx(self) -> list[bytes]:
        n = self._ser.in_waiting
        frames = self._dec.feed(self._ser.read(n)) if n else []
        out = []
        for f in frames:
            if f and f[0] == LINK_STATS_MAGIC and len(f) >= 4:
                rssi_x10, snr_x4 = struct.unpack("<hb", f[1:4])
                self.rssi_dbm, self.snr_db = rssi_x10 / 10.0, snr_x4 / 4.0
            elif f and f[:1] == b"#":
                # human-readable status line from ground_radio_bridge.ino, not a packet
                logger.debug("radio modem: %s", f.decode("ascii", "replace").strip())
            else:
                out.append(f)
        return out


# Test substitute, kept deliberately (Stage 2: the real paths are SX1278SpiRadio / SerialSlipRadio)
class UdpLoRaRadio(_VehicleRadioBase):
    """*** TEST SUBSTITUTE — NOT THE PRODUCTION RADIO LINK ***
    Stands in for the SX1278 LoRa module on the
    'Raspberry Pi 4B -- SX1278 LoRa -- Racing Engineer Station' leg, the same
    way WifiCanInterface stands in for the physical CAN bus. Carries the
    exact same LoRaPacket envelope over plain UDP. Stage 2: now shares all
    envelope/sequence/duplicate/downlink logic with the real SX1278SpiRadio
    and SerialSlipRadio via _VehicleRadioBase — only _tx()/_rx() differ, so
    a bench run over UDP exercises the same code the real radios use."""

    def __init__(self, listen_port: int, ground_host: str, ground_port: int):
        super().__init__()
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.bind(("0.0.0.0", listen_port))
        self._ground_addr = (ground_host, ground_port)

    def _tx(self, data: bytes) -> None:
        self._sock.sendto(data, self._ground_addr)

    def _rx(self) -> list[bytes]:
        out: list[bytes] = []
        while True:
            try:
                data, _addr = self._sock.recvfrom(256)
            except (BlockingIOError, OSError):
                break
            out.append(data)
        return out


# =============================================================================
# SECTION 4: RACING ENGINEER STATION AI — fetched from the EXISTING backend
# =============================================================================
# AI/perception/analysis lives at the Racing Engineer Station — the SOFTWARE
# backend (app/ai_engine.py) — NOT here, and NOT anywhere on this gateway.
# This gateway just asks it for its current recommendation and forwards that
# answer to the ESP32/VCU over the temporary CAN substitute, exactly like it
# already forwards telemetry up. compute_wing_recommendation() used to be a
# local SAMPLE/PLACEHOLDER that closed this loop before the Engineer Station
# had a real (deterministic-simulated) AI of its own; now that app/ai_engine.py
# exists, this gateway defers to it instead of guessing locally — see
# fetch_engineer_station_recommendation() below. This is a deliberate,
# byte-for-byte mirror of HARDWARE/Ground Station/bridge.py's
# identically-named function (same endpoint, same shape, same fail-safe
# contract) — MODE A and MODE B both resolve to the one authoritative AI
# generator.
#
# FAILURE BEHAVIOUR: if the backend is unreachable or has no recommendation
# yet (AI unavailable / no telemetry seen yet), this returns None and the
# caller simply skips the CAN recommendation send for that cycle — the
# vehicle keeps running on its own control loop either way (see project
# brief: "If AI stops: vehicle remains operational.").
# =============================================================================

_AI_RECOMMENDATION_TIMEOUT_S = 1.0


def fetch_engineer_station_recommendation(http_base: str) -> dict | None:
    """GET /api/ai/recommendation from the EXISTING SOFTWARE backend — the
    Racing Engineer Station's own AI, computed from the telemetry this
    gateway just forwarded it. Returns None (not a fabricated fallback) on
    any failure, unavailability, or malformed response."""
    try:
        req = urllib.request.Request(f"{http_base}/api/ai/recommendation", method="GET")
        with urllib.request.urlopen(req, timeout=_AI_RECOMMENDATION_TIMEOUT_S) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if body.get("available") is False:
        # explicit {"available": False, ...} shape from ai_engine.ai_unavailable_response()
        return None
    wing = body.get("recommendedWing") or {}
    if not wing or body.get("confidence") is None:
        return None
    return {
        "lf_deg": wing.get("frontLeft", 0.0), "rf_deg": wing.get("frontRight", 0.0),
        "lr_deg": wing.get("rearLeft", 0.0), "rr_deg": wing.get("rearRight", 0.0),
        "confidence": body.get("confidence", 0.0),
        "corner": body.get("corner", "Straight"), "grip": body.get("grip", 0.0),
        "recommendedSpeed": body.get("recommendedSpeed", 0.0),
        "efficiency": body.get("efficiency", 0.0), "racingLine": body.get("racingLine", ""),
        "note": body.get("note", ""), "energyPerLap": body.get("energyPerLap", 0.0),
        "predictedRangeLaps": body.get("predictedRangeLaps", 0.0), "warnings": body.get("warnings", []),
        # new generic contract fields, passed through for logging/inspection —
        # the CAN wire format only budgets for lf/rf/lr/rr/confidence, same as before.
        "type": body.get("type"), "reason": body.get("reason"), "target": body.get("target"),
        "validUntil": body.get("validUntil"),
    }


def fetch_vehicle_commands(http_base: str) -> list[dict]:
    """[Stage 2d] Approved AI-engineer changes waiting to go to the car:
    GET /api/vehicle/commands/next -> [{"id", "key", "value"}]. The backend re-offers a
    command until the VCU's CONFIG_ACK comes back in telemetry, so a lost packet only
    delays it. Returns [] on any failure (the car is unaffected)."""
    try:
        req = urllib.request.Request(f"{http_base}/api/vehicle/commands/next", method="GET")
        with urllib.request.urlopen(req, timeout=_AI_RECOMMENDATION_TIMEOUT_S) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    out = []
    for c in body.get("commands", []) if isinstance(body, dict) else []:
        try:
            out.append({"id": c.get("id"), "key": int(c["key"]), "value": int(c["value"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


# =============================================================================
# SECTION 6: TELEMETRY
# Canonical struct (mirrors the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h)'s VehicleTelemetry) plus
# the adapter to the dashboard's existing TelemetryFrame JSON shape. This
# adapter is the ONLY place field-name/unit translation happens, per the
# original project's "adapters, not redefinition" rule — SOFTWARE's schema
# is untouched by this consolidation.
# =============================================================================

@dataclass
class VehicleTelemetry:
    timestamp_us: int = 0
    sequence: int = 0
    vehicle_state: str = "IDLE"
    vehicle_state_index: int = 3  # VS_* enum index (see _VCU_STATE_BY_INDEX); used by
                                   # lora_encode_telemetry_compact — added alongside the
                                   # existing vehicle_state name, doesn't replace it

    speed_kmh: float = 0.0
    wheel_speed_fl_kmh: float = 0.0
    wheel_speed_fr_kmh: float = 0.0
    wheel_speed_rl_kmh: float = 0.0
    wheel_speed_rr_kmh: float = 0.0
    steering_angle_deg: float = 0.0
    throttle_pct: float = 0.0
    brake_pct: float = 0.0

    accel_x_g: float = 0.0
    accel_y_g: float = 0.0
    accel_z_g: float = 1.0
    gyro_x_dps: float = 0.0
    gyro_y_dps: float = 0.0
    gyro_z_dps: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0

    battery_voltage_v: float = 11.1
    battery_current_a: float = 0.0
    battery_temperature_c: float = 25.0
    motor_current_a: float = 0.0
    motor_temperature_c: float = 25.0

    wing_lf_deg: float = 0.0
    wing_rf_deg: float = 0.0
    wing_lr_deg: float = 0.0
    wing_rr_deg: float = 0.0
    wing_target_lf_deg: float = 0.0
    wing_target_rf_deg: float = 0.0
    wing_target_lr_deg: float = 0.0
    wing_target_rr_deg: float = 0.0

    fault_flags: int = 0
    can_status: str = "connected"
    lora_status: str = "connected"

    # [Stage 2.1] derived values — computed here or on the VCU, no extra hardware.
    # See SOFTWARE/Documentation/DerivedMetrics.md.
    distance_m: float = 0.0             # integrated ground distance this run
    wheel_rpm: float = 0.0              # from wheel speed and CFG_WHEEL_DIAMETER_M
    wheel_angle_rad: float = 0.0        # accumulated wheel rotation (dashboard 'wheelAngle')
    power_w: float = 0.0                # pack voltage x current, signed
    energy_wh: float = 0.0              # integrated pack energy used this run
    wh_per_km: float = 0.0              # energy_wh / distance
    soc_coulomb_pct: float = 100.0      # coulomb-counted state of charge, voltage-corrected
    pack_resistance_ohm: float = 0.0    # estimated from load steps (0 = not yet estimated)
    aero_mode: int = -1                 # from CAN 0x171: 0 baseline, 1 AI-assist, 2 failsafe hold,
                                        # -1 not yet reported. Logged so runs can be compared by mode.
    slip_ratio: float = 0.0             # from CAN 0x141 (driven vs reference axle)
    seconds_to_motor_limit: float | None = None
    seconds_to_battery_limit: float | None = None

    # [Stage 2] CAN v2 extras (defaults keep every Stage-1 constructor call valid)
    aero_measured_valid: bool = False   # True once 0x150 (potentiometer feedback) has been seen
    vcu_status: dict | None = None      # decoded 0x171
    fault_severity: int = 0             # from 0x170

    # [Stage 2d] dyno track run (CAN 0x145-0x147, 0x181/0x182)
    last_ai_reason_index: int = 9       # 0x171 byte 1 (9 = none received)
    track_pos: float = 0.0              # lap fraction 0..1, from the VCU's own odometry
    lap: int = 0
    track_id: int = 12                  # Monza by default (track_maps.h TRACK_DEFAULT_ID)
    track_flags: int = 0                # TF_* bits
    profile_duty_pct: float = 0.0       # motor duty the map asks for here
    lat_g_model: float = 0.0            # modelled full-scale lateral g at this position
    current_lap_ms: int = 0
    last_lap_ms: int = 0
    odometer_m: float = 0.0
    vibration_mg: float = 0.0
    run_state: int = 0                  # RUN_STATE_NAMES
    cal_phase: int = 0                  # DYNO_CAL_PHASE_NAMES
    config_report_key: int = 0          # the last CONFIG_REPORT (for the LoRa uplink)
    config_report_value: int = 0
    vcu_config: dict | None = None      # every key seen in 0x182, by name
    config_acks: list | None = None     # 0x181 received since the last send (drained by the sender)

    # [FW 2.2] CAN 0x148
    drive_mode: int = -1                # 0 dyno, 1 track, -1 not reported yet
    hw_profile: int = -1                # 0 prototype, 1 PCB rev B
    mode_flags: int | None = None       # MF_* bits
    run_laps: int | None = None
    tc_cut_pct: int | None = None
    knob: int | None = None


# Firmware VS_* (as sent in CAN_ID_VEHICLE_STATE byte 0) -> dashboard VcuState.
# Index matches the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h)'s VehicleState enum ordering exactly.
_VCU_STATE_BY_INDEX = [
    "POWER_ON", "INITIALIZATION", "SENSOR_CHECK", "IDLE", "IDLE",
    "DRIVE", "FAULT", "EMERGENCY_STOP", "FAULT",
]


def _vcu_state_name(index: int) -> str:
    if 0 <= index < len(_VCU_STATE_BY_INDEX):
        return _VCU_STATE_BY_INDEX[index]
    return "FAULT"


def vehicle_speed_kmh(fl: float, fr: float, rl: float, rr: float) -> float:
    """Same rule as the VCU sensor task (Stage 2d): the undriven front pair is the
    ground-speed reference; if the dyno does not turn the fronts, use the rear pair."""
    front, rear = (fl + fr) / 2.0, (rl + rr) / 2.0
    return front if front >= 0.3 else rear


def stage2d_frame_fields(t: "VehicleTelemetry") -> dict:
    """Additive dashboard keys (the backend payload is open, older dashboards ignore them).
    Shared shape with Ground Station/bridge.py compact_to_dashboard_frame()."""
    ai_idx = t.last_ai_reason_index
    return {
        "wheels": {"fl": t.wheel_speed_fl_kmh, "fr": t.wheel_speed_fr_kmh,
                   "rl": t.wheel_speed_rl_kmh, "rr": t.wheel_speed_rr_kmh},
        "gyroZ": t.gyro_z_dps,
        "vibrationMg": t.vibration_mg,
        "odometerM": t.odometer_m,
        "aeroMode": t.aero_mode,
        "lastAiReason": AI_REASON_NAMES[ai_idx] if 0 <= ai_idx < len(AI_REASON_NAMES) else str(ai_idx),
        "track": {
            "id": t.track_id, "pos": round(t.track_pos, 5), "lap": t.lap,
            "currentLapMs": t.current_lap_ms, "lastLapMs": t.last_lap_ms,
            "profileDutyPct": t.profile_duty_pct, "latGModel": round(t.lat_g_model, 3),
            **track_flags_to_dict(t.track_flags),
        },
        "runState": RUN_STATE_NAMES[t.run_state] if 0 <= t.run_state < len(RUN_STATE_NAMES) else str(t.run_state),
        "dynoCal": DYNO_CAL_PHASE_NAMES[t.cal_phase] if 0 <= t.cal_phase < len(DYNO_CAL_PHASE_NAMES) else str(t.cal_phase),
        "vcuConfig": t.vcu_config,
        "configAcks": t.config_acks or [],
        "vcu": vcu_mode_fields(t.drive_mode, t.hw_profile, t.mode_flags, t.run_laps, t.tc_cut_pct, t.knob),
    }


def _estimate_soc_pct(voltage_v: float) -> float:
    """[PLACEHOLDER] linear voltage->SOC approximation for a 3S LiPo
    (3.3V/cell = 0%, 4.2V/cell = 100%). Not a real coulomb-counted SOC."""
    cells = 3
    v_min, v_max = 3.3 * cells, 4.2 * cells
    pct = (voltage_v - v_min) / (v_max - v_min) * 100.0
    return max(0.0, min(100.0, pct))


def to_dashboard_frame(t: VehicleTelemetry, schema_version: int = 1) -> dict:
    """Produces the exact dict shape the existing FastAPI
    TelemetrySnapshotIn/TelemetryFramePayload (extra='allow') already
    accepts, matching field names in
    SOFTWARE/dashboard's types/telemetry.ts. Unchanged from before this
    consolidation."""
    return {
        "schemaVersion": schema_version,
        "timestamp": t.timestamp_us / 1000.0,  # ms
        "speed": t.speed_kmh,
        "voltage": t.battery_voltage_v,
        "current": t.battery_current_a,
        "motorCurrent": t.motor_current_a,
        "motorTemp": t.motor_temperature_c,
        "batteryTemp": t.battery_temperature_c,
        "battery": _estimate_soc_pct(t.battery_voltage_v),
        "imu": {"roll": t.roll_deg, "pitch": t.pitch_deg, "yaw": t.yaw_deg},
        "steering": t.steering_angle_deg,
        "wing": {"frontLeft": t.wing_lf_deg, "frontRight": t.wing_rf_deg,
                 "rearLeft": t.wing_lr_deg, "rearRight": t.wing_rr_deg},
        "wingTarget": {"frontLeft": t.wing_target_lf_deg, "frontRight": t.wing_target_rf_deg,
                       "rearLeft": t.wing_target_lr_deg, "rearRight": t.wing_target_rr_deg},
        "throttle": t.throttle_pct,
        "brake": t.brake_pct,
        "gForceLat": t.accel_y_g,
        "gForceLong": t.accel_x_g,
        "rpm": t.wheel_rpm,                 # wheel rpm (no motor rpm sensor in the BOM)
        "distance": t.distance_m,
        "wheelAngle": t.wheel_angle_rad,
        "vcuState": t.vehicle_state,
        # Additive keys: the backend payload is open and the dashboard ignores
        # what it does not know, so this needs no schema migration.
        "derived": {
            "powerW": round(t.power_w, 1),
            "energyWh": round(t.energy_wh, 3),
            "whPerKm": round(t.wh_per_km, 1),
            "socCoulombPct": round(t.soc_coulomb_pct, 1),
            "packResistanceOhm": round(t.pack_resistance_ohm, 4),
            "slipRatio": round(t.slip_ratio, 3),
            "secondsToMotorLimit": t.seconds_to_motor_limit,
            "secondsToBatteryLimit": t.seconds_to_battery_limit,
        },
        **stage2d_frame_fields(t),
    }


# =============================================================================
# SECTION 6b: FAULT DECODING
# Bit positions match the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h) Section 4 FAULT_BIT_* and the
# table in SOFTWARE/Documentation/SafetyArchitecture.md. This is a pure
# bit -> FaultPayload decode (see app/models.py FaultPayload) — the VCU's
# SafetyManager remains the sole source of fault truth; nothing here raises
# a fault the firmware didn't already raise. FAULT/CRITICAL severities both
# map to the dashboard's 'critical' HealthLevel since that type only has
# ok/warning/critical (no separate FAULT tier) — see types/telemetry.ts.
# =============================================================================

FAULT_BIT_WATCHDOG_TIMEOUT = 1 << 0
FAULT_BIT_SENSOR_TIMEOUT = 1 << 1
FAULT_BIT_CAN_TIMEOUT = 1 << 2
FAULT_BIT_LORA_TIMEOUT = 1 << 3
FAULT_BIT_BATTERY_UNDERVOLT = 1 << 4
FAULT_BIT_OVERCURRENT = 1 << 5
FAULT_BIT_MOTOR_OVERTEMP = 1 << 6
FAULT_BIT_BATTERY_OVERTEMP = 1 << 7
FAULT_BIT_INVALID_SENSOR = 1 << 8
FAULT_BIT_SERVO_POSITION_ERROR = 1 << 9    # [Stage 2] steering servo feedback disagrees with command
FAULT_BIT_AERO_POSITION_ERROR = 1 << 10    # [Stage 2] an aero flap feedback disagrees (aero -> failsafe)
FAULT_BIT_INVALID_AI_REC = 1 << 11         # [Stage 2] last AI recommendation rejected by the VCU (info)
FAULT_BIT_COMM_LOSS = 1 << 12              # [Stage 2] driver-command link lost (FAULT while DRIVE)
FAULT_BIT_ESTOP_ACTIVE = 1 << 13           # [Stage 2] emergency stop latched
FAULT_BIT_SELF_TEST_FAILED = 1 << 14       # [Stage 2] boot self-test failed
FAULT_BIT_MOTOR_STALL = 1 << 15            # [Stage 2d] duty applied, rear wheels not turning (run aborted)

# (bit, code, message, dashboard HealthLevel, subsystem) — code style matches
# SOFTWARE/dashboard/services/telemetry-simulator.ts's existing fault codes.
_FAULT_TABLE: list[tuple[int, str, str, str, str]] = [
    (FAULT_BIT_WATCHDOG_TIMEOUT, "SAFE-0x01", "Watchdog timeout", "critical", "Safety"),
    (FAULT_BIT_SENSOR_TIMEOUT, "SNS-0x02", "Sensor data stale/timeout", "critical", "Sensors"),
    (FAULT_BIT_CAN_TIMEOUT, "CAN-0x03", "CAN link timeout", "warning", "Comms"),
    (FAULT_BIT_LORA_TIMEOUT, "LORA-0x04", "LoRa link timeout", "warning", "Comms"),
    (FAULT_BIT_BATTERY_UNDERVOLT, "BMS-0x05", "Battery pack undervoltage", "critical", "Battery"),
    (FAULT_BIT_OVERCURRENT, "PWR-0x06", "Overcurrent (pack or motor)", "critical", "Powertrain"),
    (FAULT_BIT_MOTOR_OVERTEMP, "THR-0x07", "Motor overtemperature", "critical", "Powertrain"),
    (FAULT_BIT_BATTERY_OVERTEMP, "BMS-0x08", "Battery overtemperature", "critical", "Battery"),
    (FAULT_BIT_INVALID_SENSOR, "SNS-0x09", "Invalid sensor reading (NaN/Inf/out-of-range)", "critical", "Sensors"),
    (FAULT_BIT_SERVO_POSITION_ERROR, "STR-0x0A", "Steering servo position error (feedback vs command)", "critical", "Steering"),
    (FAULT_BIT_AERO_POSITION_ERROR, "AERO-0x0B", "Aero flap position error — flaps held at failsafe", "warning", "Aero"),
    (FAULT_BIT_INVALID_AI_REC, "AI-0x0C", "Last AI recommendation rejected by VCU", "warning", "AI"),
    (FAULT_BIT_COMM_LOSS, "COM-0x0D", "Driver command link lost", "critical", "Comms"),
    (FAULT_BIT_ESTOP_ACTIVE, "SAFE-0x0E", "Emergency stop latched", "critical", "Safety"),
    (FAULT_BIT_SELF_TEST_FAILED, "SAFE-0x0F", "VCU boot self-test failed", "critical", "Safety"),
    (FAULT_BIT_MOTOR_STALL, "PWR-0x10", "Motor stall — duty applied, rear wheels not turning (run aborted)",
     "warning", "Powertrain"),
]


def decode_fault_flags(fault_flags: int, timestamp_ms: float) -> list[dict]:
    """Currently-active faults only — matches the VCU's own SafetyManager,
    which re-evaluates fresh every cycle rather than latching (see
    SafetyArchitecture.md 'What is NOT yet implemented in Safety')."""
    out: list[dict] = []
    for bit, code, message, level, subsystem in _FAULT_TABLE:
        if fault_flags & bit:
            out.append({
                "id": f"{code}-{int(timestamp_ms)}",
                "code": code,
                "message": message,
                "level": level,
                "subsystem": subsystem,
                "timestamp": timestamp_ms,
            })
    return out


# =============================================================================
# SECTION 6c: PI-SIDE DIAGNOSTICS
# Stage 2: real Linux metrics (/proc, /sys thermal) and real radio RSSI/SNR
# when available. The deterministic sinusoid is kept ONLY as the fallback on
# a non-Linux dev machine, and is labelled as such in the payload.
# =============================================================================

class _HostMetrics:
    def __init__(self):
        self._prev_idle = None
        self._prev_total = None

    def cpu_pct(self) -> float | None:
        try:
            with open("/proc/stat") as f:
                parts = [int(x) for x in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle, total = parts[3] + parts[4], sum(parts)
        pct = None
        if self._prev_total is not None and total > self._prev_total:
            pct = 100.0 * (1.0 - (idle - self._prev_idle) / (total - self._prev_total))
        self._prev_idle, self._prev_total = idle, total
        return pct

    @staticmethod
    def mem_pct() -> float | None:
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0])
            return 100.0 * (1.0 - info["MemAvailable"] / info["MemTotal"])
        except (OSError, KeyError, ValueError):
            return None

    @staticmethod
    def soc_temp_c() -> float | None:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            return None


_HOST = _HostMetrics()


def build_diagnostics(t_s: float, link_fresh: bool, radio: "_VehicleRadioBase | None" = None,
                      vcu_status: dict | None = None, can_rx_age_s: float | None = None) -> dict:
    can_status = "connected" if link_fresh else "disconnected"
    cpu, mem, temp = _HOST.cpu_pct(), _HOST.mem_pct(), _HOST.soc_temp_c()
    real_host = mem is not None
    stats = radio.link_stats() if radio is not None else {}
    down_age = radio.seconds_since_last_downlink() if radio is not None else None
    lora_status = ("connected" if (down_age is not None and down_age < 3.0) else "disconnected") \
        if radio is not None else "disconnected"
    return {
        "can": can_status,
        "lora": lora_status,
        "packetLoss": 0.0 if link_fresh else 100.0,
        "cpu": cpu if cpu is not None else (22.0 + 4.0 * math.sin(t_s / 11.0)),
        "memory": mem if mem is not None else (41.0 + 3.0 * math.sin(t_s / 17.0)),
        "watchdog": "ok" if link_fresh else "warning",
        "ping": (can_rx_age_s * 1000.0) if can_rx_age_s is not None else 0.0,
        "packetRate": TELEMETRY_RATE_HZ,
        "refreshRate": TELEMETRY_RATE_HZ,
        "uptimeS": float((vcu_status or {}).get("uptimeS", t_s)),
        "temperatureC": temp if temp is not None else (44.0 + 3.0 * math.sin(t_s / 19.0)),
        "canNodes": [
            {"id": "0x100", "name": "ESP32-C6 VCU", "level": "ok" if link_fresh else "critical",
             "loadPct": 0.0, "lastSeenMs": int((can_rx_age_s or 0.0) * 1000) if link_fresh else 9999},
        ],
        "loraLink": {"rssi": stats.get("rssi") if stats.get("rssi") is not None else 0.0,
                     "snr": stats.get("snr") if stats.get("snr") is not None else 0.0,
                     "spreadingFactor": DEFAULT_SETTINGS.lora.spreading_factor,
                     "bandwidthKhz": DEFAULT_SETTINGS.lora.bandwidth_hz / 1000.0,
                     "txPowerDbm": float(DEFAULT_SETTINGS.lora.tx_power_dbm)},
        # extra, forward-compatible fields (backend accepts extra keys)
        "hostMetricsSource": "linux" if real_host else "synthetic-fallback",
        "vcu": vcu_status,
        "radio": stats or None,
    }


# =============================================================================
# SECTION 6d: ON-VEHICLE DATA LOGGER (Pi microSD)   [Stage 2]
# Architecture §26: vehicle telemetry is stored VCU -> CAN -> Pi. LoRa only
# carries ~5 Hz, so this full-rate CSV is the record used for replay/analysis.
# =============================================================================

class VehicleDataLogger:
    def __init__(self, log_dir: str):
        import csv
        import os
        from dataclasses import fields
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, time.strftime("vehicle_%Y%m%d_%H%M%S.csv"))
        self._fields = [f.name for f in fields(VehicleTelemetry)
                        if f.name not in ("vcu_status", "vcu_config", "config_acks")]
        self._fh = open(self.path, "w", newline="")
        self._w = csv.writer(self._fh)
        self._w.writerow(["wall_time_s"] + self._fields)
        self._last_flush = time.monotonic()
        self.rows = 0

    def write(self, vt: VehicleTelemetry) -> None:
        self._w.writerow([f"{time.time():.3f}"] + [getattr(vt, n) for n in self._fields])
        self.rows += 1
        if time.monotonic() - self._last_flush > 1.0:
            self._fh.flush()
            self._last_flush = time.monotonic()

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()


# =============================================================================
# SECTION 6f: DERIVED METRICS   [Stage 2.1]
# Values computed from sensors the BOM already has, so the dashboard and the
# logs carry real numbers instead of zeros/placeholders. Everything here is
# integration or simple estimation over the telemetry stream — no new hardware,
# no control authority (this is the gateway; the VCU still decides everything).
# Definitions, assumptions and error sources: Documentation/DerivedMetrics.md.
# =============================================================================

WHEEL_DIAMETER_M = 0.070            # [PLACEHOLDER] ASSUMED (1:10 of a 2026 F1 tyre); must match the
                                    # firmware CFG_WHEEL_DIAMETER_M. Measure the real tyre.
PACK_CAPACITY_AH = 2.2              # [PLACEHOLDER] printed on the LiPo
PACK_CELLS = 3
_OCV_MIN_V, _OCV_MAX_V = 3.30 * PACK_CELLS, 4.20 * PACK_CELLS


def _soc_from_voltage(voltage_v: float) -> float:
    """Crude open-circuit-voltage SoC. Only trustworthy at rest — used to seed
    and slowly correct the coulomb count, never on its own under load."""
    if not math.isfinite(voltage_v) or voltage_v <= 0:
        return 100.0
    return max(0.0, min(100.0, (voltage_v - _OCV_MIN_V) / (_OCV_MAX_V - _OCV_MIN_V) * 100.0))


class DerivedEstimator:
    """Updated once per telemetry sample. Stateless between runs by design:
    distance and energy are per-session, and the log records both."""

    def __init__(self, wheel_diameter_m: float = WHEEL_DIAMETER_M,
                 capacity_ah: float = PACK_CAPACITY_AH):
        self._circumference_m = math.pi * wheel_diameter_m
        self._capacity_ah = capacity_ah
        self._last_t: float | None = None
        self.distance_m = 0.0
        self.wheel_angle_rad = 0.0
        self.energy_wh = 0.0
        self.charge_ah = 0.0
        self._soc_pct: float | None = None
        self._rest_since: float | None = None
        # pack internal resistance from load steps
        self._prev_vi: tuple[float, float] | None = None
        self.pack_resistance_ohm = 0.0

    def update(self, vt: "VehicleTelemetry", t_s: float) -> "VehicleTelemetry":
        dt = 0.0 if self._last_t is None else max(0.0, min(1.0, t_s - self._last_t))
        self._last_t = t_s
        speed_ms = vt.speed_kmh / 3.6

        # --- distance, wheel rotation, rpm (dashboard showed 0 before) ---
        if math.isfinite(speed_ms):
            self.distance_m += speed_ms * dt
            self.wheel_angle_rad += (speed_ms / (self._circumference_m / (2 * math.pi))) * dt
        vt.distance_m = self.distance_m
        vt.wheel_angle_rad = self.wheel_angle_rad
        vt.wheel_rpm = (speed_ms / self._circumference_m) * 60.0 if math.isfinite(speed_ms) else 0.0

        # --- power and energy (replaces the backend's fabricated formula) ---
        v, i = vt.battery_voltage_v, vt.battery_current_a
        if math.isfinite(v) and math.isfinite(i):
            vt.power_w = v * i
            self.energy_wh += vt.power_w * dt / 3600.0
            self.charge_ah += i * dt / 3600.0
        vt.energy_wh = self.energy_wh
        vt.wh_per_km = (self.energy_wh / (self.distance_m / 1000.0)) if self.distance_m > 1.0 else 0.0

        # --- coulomb-counted SoC, slowly corrected by OCV while at rest ---
        if self._soc_pct is None:
            self._soc_pct = _soc_from_voltage(v)
        elif self._capacity_ah > 0:
            self._soc_pct -= (i * dt / 3600.0) / self._capacity_ah * 100.0
            resting = abs(i) < 0.5 and abs(vt.speed_kmh) < 0.5
            self._rest_since = (self._rest_since if resting else None) or (t_s if resting else None)
            if resting and self._rest_since is not None and t_s - self._rest_since > 2.0:
                # 1 %/s pull toward the voltage estimate; never a jump
                target = _soc_from_voltage(v)
                self._soc_pct += max(-1.0 * dt, min(1.0 * dt, target - self._soc_pct))
            self._soc_pct = max(0.0, min(100.0, self._soc_pct))
        vt.soc_coulomb_pct = self._soc_pct

        # --- pack internal resistance from load steps: R = -dV/dI ---
        if math.isfinite(v) and math.isfinite(i):
            if self._prev_vi is not None:
                dv, di = v - self._prev_vi[0], i - self._prev_vi[1]
                if abs(di) > 3.0:
                    r = -dv / di
                    if 0.005 < r < 0.5:  # plausible for a 3S LiPo + wiring
                        self.pack_resistance_ohm = (r if self.pack_resistance_ohm == 0.0
                                                    else 0.9 * self.pack_resistance_ohm + 0.1 * r)
            self._prev_vi = (v, i)
        vt.pack_resistance_ohm = self.pack_resistance_ohm
        return vt


# =============================================================================
# SECTION 6e: DRIVE LINK (laptop/gamepad -> Pi UDP -> CAN 0x118)   [Stage 2]
# The BOM has no RC receiver, and LoRa (~5 Hz, ~50 ms air time per packet) is
# too slow for real-time driving. Driver commands therefore travel over the
# same Wi-Fi network the ESP32-CAM video already needs, and are forwarded to
# the VCU only when a fresh packet arrives. The VCU cuts the motor 250 ms after
# the last command (CFG_DRIVER_CMD_TIMEOUT_MS). Sender: HARDWARE/Ground
# Station/drive_client.py.
# Packet (10 B, LE): magic 0xD7, version 1, seq u8, steer i16, throttle i16,
#                    flags u8, crc16-ccitt u16 over the first 8 bytes.
# =============================================================================

DRIVE_MAGIC = 0xD7
DRIVE_VERSION = 1
_DRIVE_FMT = "<BBBhhB"


def drive_packet_encode(seq: int, steering_permille: int, throttle_permille: int, flags: int) -> bytes:
    body = struct.pack(_DRIVE_FMT, DRIVE_MAGIC, DRIVE_VERSION, seq & 0xFF,
                       max(-1000, min(1000, int(steering_permille))),
                       max(-1000, min(1000, int(throttle_permille))), flags & 0xFF)
    return body + struct.pack("<H", lora_crc16_ccitt(body))


def drive_packet_decode(data: bytes) -> dict | None:
    if len(data) < 10 or data[0] != DRIVE_MAGIC or data[1] != DRIVE_VERSION:
        return None
    body, (crc,) = data[:8], struct.unpack("<H", data[8:10])
    if lora_crc16_ccitt(body) != crc:
        return None
    _, _, seq, steer, thr, flags = struct.unpack(_DRIVE_FMT, body)
    if not (-1000 <= steer <= 1000 and -1000 <= thr <= 1000):
        return None
    return {"seq": seq, "steering_permille": steer, "throttle_permille": thr, "flags": flags}


class DriveLinkListener:
    def __init__(self, port: int):
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.bind(("0.0.0.0", port))
        self.port = port
        self.rejected = 0
        self.last_rx_monotonic: float | None = None

    def poll(self) -> list[dict]:
        out = []
        while True:
            try:
                data, _ = self._sock.recvfrom(64)
            except (BlockingIOError, OSError):
                break
            d = drive_packet_decode(data)
            if d is None:
                self.rejected += 1
                continue
            self.last_rx_monotonic = time.monotonic()
            out.append(d)
        return out


# =============================================================================
# SECTION 7: SOFTWARE / BACKEND CONNECTION
# Speaks the exact same message shape SOFTWARE's dashboard already sends
# over /ws/telemetry (see SOFTWARE/dashboard/services/telemetry-socket.ts
# and SOFTWARE/backend/app/websocket.py). No SOFTWARE change is required.
# =============================================================================

def _http_base_from_ws_url(ws_url: str) -> str:
    scheme, _, rest = ws_url.partition("://")
    http_scheme = "https" if scheme == "wss" else "http"
    host_port = rest.split("/", 1)[0]
    return f"{http_scheme}://{host_port}"


def _create_session(http_base: str, mode: str = "practice") -> str:
    """SOFTWARE's backend requires an existing session row to attach frames
    to. The dashboard creates one itself in the browser; this gateway runs
    independently, so it creates its own via the same POST /api/sessions."""
    body = json.dumps({"mode": mode}).encode("utf-8")
    req = urllib.request.Request(f"{http_base}/api/sessions", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())["id"]


class TelemetryGatewayClient:
    """Thin wrapper — the `websockets` connection is created lazily so this
    module imports and runs (in --no-backend mode) without the optional
    `websockets` package installed."""

    def __init__(self, url: str):
        self._url = url
        self._ws = None

    async def connect(self):
        import websockets  # local import: optional dependency
        self._ws = await websockets.connect(self._url)
        # FIX (Stage 2c): the backend echoes every frame back to the sender. If
        # nobody reads those echoes the client's receive queue fills, keepalive
        # pongs stop being processed and the connection is dropped after ~40 s
        # (reproduced). Drain and discard them in the background.
        self._drain = asyncio.create_task(_drain_ws(self._ws))

    async def send_telemetry(self, t: VehicleTelemetry, session_id: str,
                              ai: dict | None = None, diagnostics: dict | None = None,
                              faults: list[dict] | None = None) -> None:
        if self._ws is None:
            raise RuntimeError("connect() must be called first")
        msg = json.dumps({
            "type": "frame",
            "sessionId": session_id,
            "snapshot": {
                "frame": to_dashboard_frame(t), "ai": ai, "diagnostics": diagnostics,
                "faults": faults or [],
            },
        })
        await self._ws.send(msg)

    async def close(self):
        drain = getattr(self, "_drain", None)
        if drain is not None:
            drain.cancel()
            self._drain = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


async def _drain_ws(ws) -> None:
    """Read and discard everything the backend sends on this socket."""
    try:
        async for _ in ws:
            pass
    except Exception:
        pass  # the next send() raises and the normal reconnect path takes over


# =============================================================================
# SECTION 8: SIMULATION / SAMPLE DATA
# Deterministic (not random) waveform for standalone/offline runs before
# the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h) is connected over a real CAN bus. Same shape the
# CAN-sourced path (Section 9) produces, so main() doesn't need to know
# which one is active.
# =============================================================================

class SampleTelemetrySource:
    """Deterministic VehicleTelemetry waveform as a function of elapsed
    time. Same waveform the VCU firmware (main_updated_corrected__1_.ino + vcu_logic.h)'s sample sensors use, so a
    developer sees consistent numbers whether the Pi is reading CAN frames
    from the real sketch or running fully standalone."""

    def __init__(self):
        self._sequence = 0

    def next(self, t_s: float) -> VehicleTelemetry:
        self._sequence += 1
        speed = max(0.0, 20.0 + 15.0 * math.sin(t_s / 6.0))
        wing = 15.0 + 5.0 * math.sin(t_s / 5.0)
        return VehicleTelemetry(
            timestamp_us=int(t_s * 1_000_000), sequence=self._sequence, vehicle_state="DRIVE",
            vehicle_state_index=5,
            speed_kmh=speed, wheel_speed_fl_kmh=speed, wheel_speed_fr_kmh=speed,
            wheel_speed_rl_kmh=speed, wheel_speed_rr_kmh=speed,
            steering_angle_deg=8.0 * math.sin(t_s / 3.0),
            throttle_pct=max(0.0, 40.0 + 20.0 * math.sin(t_s / 4.0)), brake_pct=0.0,
            accel_x_g=0.05 * math.sin(t_s / 2.0), accel_y_g=0.4 * math.sin(t_s / 3.0), accel_z_g=1.0,
            gyro_z_dps=12.0 * math.sin(t_s / 3.0),
            roll_deg=1.5 * math.sin(t_s / 4.0), pitch_deg=0.8 * math.sin(t_s / 5.0),
            yaw_deg=(t_s * 3.0) % 360.0,
            battery_voltage_v=12.6 - 0.2 * abs(math.sin(t_s / 20.0)),
            battery_current_a=3.0 + 2.0 * abs(math.sin(t_s / 4.0)),
            battery_temperature_c=28.0 + 2.0 * math.sin(t_s / 30.0),
            motor_current_a=2.0 + 1.5 * abs(math.sin(t_s / 4.0)),
            motor_temperature_c=32.0 + 3.0 * math.sin(t_s / 25.0),
            wing_lf_deg=wing, wing_rf_deg=wing, wing_lr_deg=wing * 0.8, wing_rr_deg=wing * 0.8,
            wing_target_lf_deg=wing, wing_target_rf_deg=wing,
            wing_target_lr_deg=wing * 0.8, wing_target_rr_deg=wing * 0.8,
        )


class CanTelemetrySource:
    """Real-hardware-shaped path: accumulates VehicleTelemetry from decoded
    CAN frames instead of a waveform. Works today against MockCanInterface
    (nothing arrives unless something calls .send() on the same mock queue,
    e.g. a bench test harness); works unchanged against SocketCanInterface
    once physical hardware exists — this class does not change."""

    def __init__(self, iface: CanInterface):
        self._iface = iface
        self._vt = VehicleTelemetry()
        self._sequence = 0
        self.pending_acks: list[dict] = []   # CONFIG_ACKs not yet forwarded to the ground
        # LoRa carries ONE config value per uplink. The VCU reports at 10 Hz but the
        # uplink runs at ~5 Hz, so forwarding "the latest report" would skip keys at
        # random. The Pi keeps the whole table and runs its own rotation, sending a
        # value that just changed (or was just ACKed) first.
        self._cfg_by_key: dict[int, int] = {}
        self._cfg_priority: list[int] = []
        self._cfg_rr = 0

    def take_acks(self) -> list[dict]:
        out, self.pending_acks = self.pending_acks, []
        return out

    def _note_config(self, key: int, value: int) -> None:
        if self._cfg_by_key.get(key) != value and key not in self._cfg_priority:
            self._cfg_priority.append(key)
        self._cfg_by_key[key] = value

    def next_config_report(self) -> tuple[int, int]:
        """(key, value) for the next LoRa uplink; (0, 0) until the VCU has reported."""
        if self._cfg_priority:
            k = self._cfg_priority.pop(0)
            return k, self._cfg_by_key[k]
        if not self._cfg_by_key:
            return 0, 0
        keys = sorted(self._cfg_by_key)
        k = keys[self._cfg_rr % len(keys)]
        self._cfg_rr += 1
        return k, self._cfg_by_key[k]

    def next(self, t_s: float) -> VehicleTelemetry:
        for frame in self._iface.poll():
            vs = can_decode_vehicle_state(frame)
            if vs:
                state_idx, fault_flags = vs
                self._vt.vehicle_state = _vcu_state_name(state_idx)
                self._vt.vehicle_state_index = state_idx
                self._vt.fault_flags = fault_flags
                continue
            ws = can_decode_wheel_speed(frame)
            if ws:
                fl, fr, rl, rr = ws
                self._vt.wheel_speed_fl_kmh, self._vt.wheel_speed_fr_kmh = fl, fr
                self._vt.wheel_speed_rl_kmh, self._vt.wheel_speed_rr_kmh = rl, rr
                self._vt.speed_kmh = vehicle_speed_kmh(fl, fr, rl, rr)
                continue
            di = can_decode_driver_inputs(frame)
            if di:
                self._vt.steering_angle_deg, self._vt.throttle_pct, self._vt.brake_pct = di
                continue
            ia = can_decode_imu_accel(frame)
            if ia:
                self._vt.accel_x_g, self._vt.accel_y_g, self._vt.accel_z_g = ia
                continue
            ig = can_decode_imu_gyro(frame)
            if ig:
                self._vt.gyro_x_dps, self._vt.gyro_y_dps, self._vt.gyro_z_dps = ig
                continue
            bat = can_decode_battery(frame)
            if bat:
                self._vt.battery_voltage_v, self._vt.battery_current_a = bat
                continue
            temp = can_decode_temperature(frame)
            if temp:
                self._vt.motor_temperature_c, self._vt.battery_temperature_c, self._vt.motor_current_a = temp
                continue
            aero = can_decode_aero_target(frame)
            if aero:
                (self._vt.wing_target_lf_deg, self._vt.wing_target_rf_deg,
                 self._vt.wing_target_lr_deg, self._vt.wing_target_rr_deg) = aero
                if not self._vt.aero_measured_valid:
                    # no potentiometer feedback yet -> show target as actual (Stage-1 behaviour)
                    self._vt.wing_lf_deg, self._vt.wing_rf_deg = aero[0], aero[1]
                    self._vt.wing_lr_deg, self._vt.wing_rr_deg = aero[2], aero[3]
                continue
            meas = can_decode_aero_measured(frame)
            if meas:
                self._vt.aero_measured_valid = True
                self._vt.wing_lf_deg, self._vt.wing_rf_deg, self._vt.wing_lr_deg, self._vt.wing_rr_deg = meas
                continue
            att = can_decode_imu_attitude(frame)
            if att:
                self._vt.roll_deg, self._vt.pitch_deg, self._vt.yaw_deg = att
                continue
            fe = can_decode_fault_event(frame)
            if fe:
                self._vt.fault_flags, self._vt.fault_severity, state_idx = fe
                self._vt.vehicle_state = _vcu_state_name(state_idx)
                self._vt.vehicle_state_index = state_idx
                continue
            st = can_decode_vcu_status(frame)
            if st:
                self._vt.vcu_status = st
                self._vt.aero_mode = st["aeroMode"]
                self._vt.last_ai_reason_index = frame.data[1]
                continue
            derived = can_decode_vehicle_derived(frame)
            if derived is not None:
                self._vt.slip_ratio, self._vt.seconds_to_motor_limit, self._vt.seconds_to_battery_limit = derived
                if len(frame.data) >= 8:
                    self._vt.vibration_mg = float(struct.unpack("<H", frame.data[6:8])[0])
                continue
            tp = can_decode_track_position(frame)
            if tp:
                self._vt.track_pos, self._vt.lap, self._vt.track_id = tp["pos"], tp["lap"], tp["trackId"]
                self._vt.track_flags, self._vt.profile_duty_pct = tp["flags"], tp["profileDutyPct"]
                self._vt.lat_g_model = tp["latGModel"]
                continue
            lt = can_decode_lap_timing(frame)
            if lt:
                self._vt.last_lap_ms, self._vt.current_lap_ms = lt
                continue
            dy = can_decode_dyno(frame)
            if dy:
                self._vt.odometer_m, self._vt.vibration_mg = dy["odometerM"], dy["vibrationMg"]
                self._vt.run_state, self._vt.cal_phase = dy["runState"], dy["calPhase"]
                continue
            vm = can_decode_vcu_mode(frame)
            if vm:
                self._vt.drive_mode, self._vt.hw_profile = vm["driveMode"], vm["hwProfile"]
                self._vt.mode_flags, self._vt.run_laps = vm["flags"], vm["runLaps"]
                self._vt.tc_cut_pct, self._vt.knob = vm["tcCutPct"], vm["knob"]
                continue
            ack = can_decode_config_ack(frame)
            if ack:
                self.pending_acks.append(ack)
                if ack["status"] in (0, 1):   # applied / staged: the value is (or will be) on the car
                    self._note_config(ack["key"], ack["value"])
                logger.info("VCU CONFIG_ACK key=%d (%s) -> %s value=%d", ack["key"],
                            CONFIG_KEY_NAMES.get(ack["key"], "?"),
                            CONFIG_STATUS_NAMES[ack["status"]] if ack["status"] < len(CONFIG_STATUS_NAMES) else ack["status"],
                            ack["value"])
                continue
            rpt = can_decode_config_report(frame)
            if rpt:
                cfg = dict(self._vt.vcu_config or {})
                cfg[CONFIG_KEY_NAMES.get(rpt["key"], str(rpt["key"]))] = rpt["value"]
                cfg["pending"] = rpt["pending"]
                self._vt.vcu_config = cfg
                self._note_config(rpt["key"], rpt["value"])
        self._sequence += 1
        self._vt.timestamp_us = int(t_s * 1_000_000)
        self._vt.sequence = self._sequence
        return replace(self._vt)


# =============================================================================
# SECTION 9: MAIN LOOP   [Stage 2 — rewritten as independent asyncio tasks]
#
# Why tasks instead of one loop: the VCU declares COMM_LOSS if the Pi
# heartbeat (0x190) is silent for 500 ms, and cuts the motor if driver
# commands (0x118) stop for 250 ms. In Stage 1 the single loop awaited the
# backend reconnect (seconds) and the AI HTTP fetch (up to 1 s) inline, which
# would have starved both. Now:
#   heartbeat_task   10 Hz  0x190             never waits on the network
#   drive_task      100 Hz  UDP/LoRa -> 0x118 forward-only, never replayed
#   telemetry loop   20 Hz  CAN -> log (full rate) -> backend (MODE A) or
#                           LoRa uplink decimated to --lora-uplink-hz (MODE B)
#   ai_task (MODE A) --ai-hz  backend GET -> 0x160/0x161 (HTTP in a thread)
#   backend_task (MODE A)   connect/reconnect with backoff, off the hot path
# All CAN sends happen on the event-loop thread (no locking needed).
# =============================================================================

class _GatewayState:
    def __init__(self):
        self.backend_ok = False
        self.ai_sequence = 0
        self.drive_sequence = 0
        self.drive_forwarded = 0
        self.config_forwarded = 0
        self.latest_vt: VehicleTelemetry | None = None
        self.link_fresh = False


def _build_can_interface(args: argparse.Namespace) -> CanInterface:
    if args.can_interface == "mock":
        return MockCanInterface()
    if args.can_interface == "wifi":
        logger.info("*** BENCH SUBSTITUTE *** WiFi-UDP CAN transport listening on :%d", args.listen_port)
        return WifiCanInterface(listen_port=args.listen_port, esp32_ip=args.esp32_ip, esp32_port=args.esp32_port)
    iface = SocketCanInterface(channel=args.can_channel, bitrate=args.can_bitrate,
                               bustype="virtual" if args.can_channel.startswith("virtual") else "socketcan")
    logger.info("SocketCAN on %s (bitrate set by `ip link`, expected %d)", args.can_channel, args.can_bitrate)
    return iface


def _build_radio(args: argparse.Namespace) -> _VehicleRadioBase:
    cfg = replace(DEFAULT_SETTINGS.lora, frequency_hz=args.lora_freq_hz, tx_power_dbm=args.lora_tx_dbm)
    if args.lora_backend == "sx1278":
        return SX1278SpiRadio(cfg, spi_bus=args.sx1278_spi_bus, spi_cs=args.sx1278_spi_cs,
                              reset_pin=args.sx1278_reset, dio0_pin=args.sx1278_dio0,
                              tx_power_pa_boost=not args.sx1278_rfo)
    if args.lora_backend == "serial":
        logger.info("LoRa via SLIP serial modem on %s @ %d", args.lora_serial_port, args.lora_serial_baud)
        return SerialSlipRadio(args.lora_serial_port, args.lora_serial_baud)
    logger.info("*** TEST SUBSTITUTE RADIO *** UDP to ground bridge %s:%d, listening :%d",
                args.ground_host, args.ground_port, args.radio_listen_port)
    return UdpLoRaRadio(listen_port=args.radio_listen_port, ground_host=args.ground_host,
                        ground_port=args.ground_port)


def _check_lora_budget(uplink_hz: float) -> None:
    cfg = DEFAULT_SETTINGS.lora
    pkt_len = LORA_HEADER_LEN + len(lora_encode_telemetry_compact(VehicleTelemetry())) + LORA_CRC_LEN
    toa = lora_time_on_air_ms(pkt_len, cfg.spreading_factor, cfg.bandwidth_hz, cfg.coding_rate, cfg.preamble)
    duty = toa * uplink_hz / 1000.0
    logger.info("LoRa uplink: %d B packet, %.1f ms on air, %.1f Hz -> %.0f%% of air time",
                pkt_len, toa, uplink_hz, duty * 100)
    if duty > 0.5:
        logger.warning("LoRa uplink uses >50%% of air time — downlink (AI/driver/config) will be starved. "
                       "Lower --lora-uplink-hz.")


COMMAND_POLL_S = 0.25   # [FW 2.2] dashboard Stop / E-STOP reach the car within ~0.3 s in MODE A


async def run(args: argparse.Namespace) -> None:
    can_iface = _build_can_interface(args)
    source = SampleTelemetrySource() if args.source == "sample" else CanTelemetrySource(can_iface)
    ground_link = args.ground_link
    http_base = args.http_url or _http_base_from_ws_url(args.url)
    st = _GatewayState()
    stop = asyncio.Event()

    radio: _VehicleRadioBase | None = None
    if ground_link == "lora":
        radio = _build_radio(args)
        _check_lora_budget(args.lora_uplink_hz)

    drive = DriveLinkListener(args.drive_port) if args.drive_port > 0 else None
    if drive is not None:
        logger.info("Drive link listening on UDP :%d (sender: Ground Station/drive_client.py)", args.drive_port)

    derived = DerivedEstimator(wheel_diameter_m=args.wheel_diameter_m, capacity_ah=args.pack_capacity_ah)
    data_logger = None if args.no_log else VehicleDataLogger(args.log_dir)
    if data_logger is not None:
        logger.info("Full-rate vehicle log -> %s", data_logger.path)

    client: TelemetryGatewayClient | None = None
    session_id: str | None = None
    if ground_link == "direct" and not args.no_backend:
        client = TelemetryGatewayClient(args.url)
        session_id = args.session_id

    # ---------------------------------------------------------------- tasks
    async def heartbeat_task():
        counter = 0
        while not stop.is_set():
            if radio is not None:  # MODE B: the ground link is the LoRa radio
                down_age = radio.seconds_since_last_downlink()
                lora_ok = down_age is not None and down_age < 3.0
            else:                  # MODE A: the ground link is Wi-Fi to the backend
                lora_ok = st.backend_ok
            can_iface.send(can_encode_pi_heartbeat(counter, lora_ok, st.backend_ok))
            counter = (counter + 1) & 0xFF
            await asyncio.sleep(0.1)

    def _forward_driver(d: dict, origin: str) -> None:
        can_iface.send(can_encode_driver_command(st.drive_sequence, d["steering_permille"],
                                                 d["throttle_permille"], d["flags"]))
        st.drive_sequence = (st.drive_sequence + 1) & 0xFF
        st.drive_forwarded += 1
        if args.verbose and d["flags"]:
            logger.info("driver flags 0x%02X from %s -> CAN 0x118", d["flags"], origin)

    async def drive_task():
        while not stop.is_set():
            if drive is not None:
                cmds = drive.poll()
                if cmds:  # only the newest matters; older ones in the same burst are stale
                    _forward_driver(cmds[-1], "udp")
            if radio is not None:
                for rec in radio.poll_recommendations():
                    fa, fb = can_encode_ai_recommendation(st.ai_sequence, rec["lf_deg"], rec["rf_deg"],
                                                          rec["lr_deg"], rec["rr_deg"], rec["confidence"])
                    can_iface.send(fa)
                    can_iface.send(fb)
                    st.ai_sequence += 1
                    if args.verbose:
                        logger.info("AI_RECOMMENDATION from ground -> CAN: lf=%.1f conf=%.2f",
                                    rec["lf_deg"], rec["confidence"])
                cmds = radio.take_driver_commands()
                if cmds:
                    _forward_driver(cmds[-1], "lora")
                for c in radio.take_config_writes():
                    can_iface.send(can_encode_config_write(c["key"], c["value"]))
                    st.config_forwarded += 1
                    logger.info("CONFIG_WRITE key=%d value=%d from ground -> CAN 0x180", c["key"], c["value"])
            await asyncio.sleep(0.01)

    async def backend_task():
        nonlocal session_id
        assert client is not None
        backoff = 2.0
        while not stop.is_set():
            if st.backend_ok:
                await asyncio.sleep(0.5)
                continue
            try:
                if session_id is None:
                    session_id = await asyncio.to_thread(_create_session, http_base)
                    logger.info("Created backend session %s via %s/api/sessions", session_id, http_base)
                await asyncio.wait_for(client.connect(), timeout=3.0)
                st.backend_ok = True
                backoff = 2.0
                logger.info("Connected to %s", args.url)
            except (urllib.error.URLError, OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
                logger.warning("Backend unavailable (%s) — retry in %.1fs; vehicle link unaffected", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 10.0)

    async def ai_task():
        period = 1.0 / args.ai_hz
        last_cmd_poll = 0.0
        while not stop.is_set():
            if st.backend_ok and st.link_fresh and time.monotonic() - last_cmd_poll >= COMMAND_POLL_S:
                # [Stage 2d] operator-approved AI-engineer changes -> CAN 0x180 (VCU validates + ACKs)
                last_cmd_poll = time.monotonic()
                for c in await asyncio.to_thread(fetch_vehicle_commands, http_base):
                    can_iface.send(can_encode_config_write(c["key"], c["value"]))
                    st.config_forwarded += 1
                    logger.info("Approved change key=%d (%s) value=%d -> CAN 0x180", c["key"],
                                CONFIG_KEY_NAMES.get(c["key"], "?"), c["value"])
            if st.backend_ok and st.link_fresh:
                rec = await asyncio.to_thread(fetch_engineer_station_recommendation, http_base)
                if rec is not None:
                    fa, fb = can_encode_ai_recommendation(st.ai_sequence, rec["lf_deg"], rec["rf_deg"],
                                                          rec["lr_deg"], rec["rr_deg"], rec["confidence"])
                    can_iface.send(fa)
                    can_iface.send(fb)
                    st.ai_sequence += 1
                    if args.verbose:
                        logger.info("Engineer Station rec -> CAN: lf=%.1f rf=%.1f conf=%.2f type=%s",
                                    rec["lf_deg"], rec["rf_deg"], rec["confidence"], rec.get("type"))
            await asyncio.sleep(period)

    # one-shot startup config (e.g. --aero-mode ai), repeated 3x for a lossy start-up bus
    async def startup_config_task():
        if args.aero_mode is None:
            return
        value = {"baseline": 0, "ai": 1, "hold": 2}[args.aero_mode]
        for _ in range(3):
            can_iface.send(can_encode_config_write(CONFIG_KEY_AERO_MODE, value))
            await asyncio.sleep(0.2)
        logger.info("Sent CONFIG_WRITE aero_mode=%s (VCU refuses it while in DRIVE)", args.aero_mode)

    tasks = [asyncio.create_task(heartbeat_task()), asyncio.create_task(drive_task()),
             asyncio.create_task(startup_config_task())]
    if client is not None:
        tasks += [asyncio.create_task(backend_task()), asyncio.create_task(ai_task())]

    logger.info("Pi gateway: source=%s can=%s ground-link=%s%s backend=%s rate=%.0fHz",
                args.source, args.can_interface, ground_link,
                f" ({args.lora_backend})" if radio is not None else "",
                "disabled" if client is None else args.url, args.rate)

    # ------------------------------------------------------ telemetry loop
    period_s = 1.0 / args.rate
    uplink_period_s = 1.0 / args.lora_uplink_hz
    start = time.monotonic()
    last_uplink = -1e9
    last_fault_flags_sent = 0
    last_verbose = -1e9
    try:
        while args.duration_s <= 0 or (time.monotonic() - start) < args.duration_s:
            t_s = time.monotonic() - start
            now_ms = int(t_s * 1000)
            vt = source.next(t_s)
            vt = derived.update(vt, t_s)

            if args.source == "sample":
                can_age: float | None = 0.0
                st.link_fresh = True
            else:
                can_age = can_iface.seconds_since_last_rx()
                st.link_fresh = can_age is not None and can_age < ESP32_LINK_TIMEOUT_S
            vt.can_status = "connected" if st.link_fresh else "disconnected"
            st.latest_vt = vt

            if data_logger is not None and st.link_fresh:
                data_logger.write(vt)

            acks = source.take_acks() if isinstance(source, CanTelemetrySource) else []
            if ground_link == "direct":
                vt.config_acks = acks
                if client is not None and st.backend_ok and st.link_fresh:
                    diag = build_diagnostics(t_s, st.link_fresh, None, vt.vcu_status, can_age)
                    faults = decode_fault_flags(vt.fault_flags, to_dashboard_frame(vt)["timestamp"])
                    try:
                        await asyncio.wait_for(client.send_telemetry(vt, session_id, ai=None,
                                                                     diagnostics=diag, faults=faults), 0.5)
                    except (OSError, ConnectionClosed, asyncio.TimeoutError, RuntimeError) as exc:
                        logger.warning("Backend send failed (%s) — reconnecting in background", exc)
                        st.backend_ok = False
                        await client.close()
            else:
                assert radio is not None
                for a in acks:  # [Stage 2d] ACKs bypass decimation: the operator is waiting for them
                    radio.send_config_ack(a["key"], a["status"], a["value"], now_ms)
                if st.link_fresh:
                    if vt.fault_flags != last_fault_flags_sent:  # fault events bypass decimation
                        radio.send_fault_event(vt.fault_flags, now_ms)
                        last_fault_flags_sent = vt.fault_flags
                    elif t_s - last_uplink >= uplink_period_s:
                        if isinstance(source, CanTelemetrySource):
                            vt.config_report_key, vt.config_report_value = source.next_config_report()
                        radio.send_telemetry(vt, now_ms)
                        last_uplink = t_s

            if args.verbose and t_s - last_verbose >= 1.0:
                last_verbose = t_s
                logger.info("t=%.1fs state=%s speed=%.1f V=%.2f faults=0x%X link=%s backend=%s drive_fwd=%d ai_seq=%d",
                            t_s, vt.vehicle_state, vt.speed_kmh, vt.battery_voltage_v, vt.fault_flags,
                            "fresh" if st.link_fresh else "STALE", "ok" if st.backend_ok else "down",
                            st.drive_forwarded, st.ai_sequence)
            await asyncio.sleep(period_s)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if client is not None:
            await client.close()
        if data_logger is not None:
            data_logger.close()
            logger.info("Vehicle log closed: %s (%d rows)", data_logger.path, data_logger.rows)
        logger.info("Run summary: %.0f m travelled, %.2f Wh used, %.1f Wh/km, SoC %.0f%%, pack R %.3f ohm",
                    derived.distance_m, derived.energy_wh,
                    derived.energy_wh / (derived.distance_m / 1000.0) if derived.distance_m > 1 else 0.0,
                    derived._soc_pct if derived._soc_pct is not None else 100.0,
                    derived.pack_resistance_ohm)
        if isinstance(can_iface, SocketCanInterface):
            can_iface.close()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Raspberry Pi 4B vehicle telemetry gateway (Stage 2)")
    g = p.add_argument_group("backend (MODE A)")
    g.add_argument("--laptop", default=None, metavar="IP",
                   help="Laptop IP running the backend; shorthand for --url ws://IP:8000/ws/telemetry")
    g.add_argument("--url", default=BACKEND_WS_URL, help="SOFTWARE backend WebSocket URL (/ws/telemetry)")
    g.add_argument("--http-url", default=None, help="Backend REST base (default: derived from --url)")
    g.add_argument("--no-backend", action="store_true", help="Do not contact the backend at all")
    g.add_argument("--session-id", default=None, help="Use an existing SOFTWARE session id")
    g.add_argument("--ai-hz", type=float, default=5.0,
                   help="How often to fetch the Engineer Station recommendation (VCU staleness limit is 500 ms)")

    g = p.add_argument_group("vehicle link")
    g.add_argument("--source", choices=["sample", "can"], default="can",
                   help="'can' = decode frames from --can-interface; 'sample' = synthetic waveform (no VCU)")
    g.add_argument("--can-interface", choices=["socketcan", "wifi", "mock"], default="socketcan",
                   help="'socketcan' (default) = Pi CAN HAT can0; 'wifi' = Stage-1 UDP bench substitute; "
                        "'mock' = in-process loopback")
    g.add_argument("--can-channel", default="can0", help="SocketCAN channel; 'virtual*' uses python-can's virtual bus")
    g.add_argument("--can-bitrate", type=int, default=500000)
    g.add_argument("--listen-port", type=int, default=PI_LISTEN_PORT, help="[wifi] Pi UDP listen port")
    g.add_argument("--esp32-ip", default=None, help="[wifi] fallback ESP32 IP before first packet")
    g.add_argument("--esp32-port", type=int, default=ESP32_LISTEN_PORT, help="[wifi] ESP32 UDP listen port")
    g.add_argument("--rate", type=float, default=TELEMETRY_RATE_HZ, help="Telemetry loop rate (Hz)")
    g.add_argument("--aero-mode", choices=["baseline", "ai", "hold"], default=None,
                   help="Send CONFIG_WRITE aero_mode at startup (VCU ignores it while in DRIVE)")
    g.add_argument("--wheel-diameter-m", type=float, default=WHEEL_DIAMETER_M,
                   help="Must match the firmware's CFG_WHEEL_DIAMETER_M (distance, rpm, slip)")
    g.add_argument("--pack-capacity-ah", type=float, default=PACK_CAPACITY_AH,
                   help="Battery capacity for coulomb-counted state of charge")

    g = p.add_argument_group("driver link")
    g.add_argument("--drive-port", type=int, default=5020,
                   help="UDP port for drive_client.py packets -> CAN 0x118 (0 disables)")

    g = p.add_argument_group("ground link / LoRa (MODE B)")
    g.add_argument("--ground-link", choices=["direct", "lora"], default="direct",
                   help="'direct' = this Pi talks to the backend over Wi-Fi (MODE A). 'lora' = telemetry "
                        "goes over LoRa to Ground Station/bridge.py, which owns the backend (MODE B).")
    g.add_argument("--lora-backend", choices=["sx1278", "serial", "udp"], default="sx1278",
                   help="'sx1278' = Ra-02/SX1278 on SPI via LoRaRF; 'serial' = SLIP UART modem; "
                        "'udp' = test substitute")
    g.add_argument("--lora-uplink-hz", type=float, default=DEFAULT_SETTINGS.lora.uplink_rate_hz)
    g.add_argument("--lora-freq-hz", type=int, default=DEFAULT_SETTINGS.lora.frequency_hz)
    g.add_argument("--lora-tx-dbm", type=int, default=DEFAULT_SETTINGS.lora.tx_power_dbm)
    g.add_argument("--lora-serial-port", default="/dev/serial0")
    g.add_argument("--lora-serial-baud", type=int, default=115200)
    g.add_argument("--sx1278-spi-bus", type=int, default=1,
                   help="SPI1 — the CAN HAT owns both SPI0 chip selects (needs dtoverlay=spi1-1cs)")
    g.add_argument("--sx1278-spi-cs", type=int, default=0, help="/dev/spidev1.0 = GPIO18")
    g.add_argument("--sx1278-reset", type=int, default=17)
    g.add_argument("--sx1278-dio0", type=int, default=27, help="RxDone/TxDone line (wired, RX is polled)")
    g.add_argument("--sx1278-rfo", action="store_true",
                   help="Module uses the RFO output instead of PA_BOOST (Ra-02 uses PA_BOOST)")
    g.add_argument("--ground-host", default="127.0.0.1", help="[udp] ground bridge host")
    g.add_argument("--ground-port", type=int, default=5010, help="[udp] ground bridge port")
    g.add_argument("--radio-listen-port", type=int, default=5011, help="[udp] downlink listen port")

    g = p.add_argument_group("logging / misc")
    g.add_argument("--log-dir", default="logs", help="Directory for full-rate vehicle CSV logs")
    g.add_argument("--no-log", action="store_true")
    g.add_argument("--duration-s", type=float, default=0.0, help="Stop after N seconds (0 = run forever)")
    g.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.laptop:
        args.url = f"ws://{args.laptop}:{LAPTOP_BACKEND_PORT}/ws/telemetry"
    if ("SET_LAPTOP_IP" in args.url and not args.no_backend and args.ground_link == "direct"):
        raise SystemExit("Laptop IP not set: run with --laptop <ip> (see `ipconfig`/`ip a` on the laptop), "
                         "or export LAPTOP_IP=<ip>, or use --no-backend.")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
