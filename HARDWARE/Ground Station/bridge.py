#!/usr/bin/env python3
"""
=============================================================================
bridge.py — Ground-Station Bridge (Racing Engineer Station receiver)
=============================================================================

Formula-Student-style 1:10 RC car project — HARDWARE layer, ground side.
Runs on the laptop ground station next to the SOFTWARE backend.

STAGE 2 (see SOFTWARE/Documentation/ProjectAudit_Stage2.md):
  * LoRa protocol v2 — TELEMETRY_COMPACT currents in centi-amps (v1 clamped
    at 32.767 A), AI_RECOMMENDATION formalised (recommendation only, the VCU
    validates it), new DRIVER_COMMAND(7) / CONFIG_WRITE(8) downlink types.
  * Real radio backend: GroundSerialRadio — USB serial to the ESP32 + SX1278
    radio bridge (HARDWARE/Ground Station/ground_radio_bridge/), SLIP framed,
    with RSSI/SNR reported back. The UDP GroundRadio test substitute is KEPT
    (same class name/API) for bench runs without radios.
  * Half-duplex-aware downlink: the ground only transmits immediately after
    it has received an uplink packet, when the Pi's SX1278 is guaranteed to
    be back in RX. One downlink per uplink, priority CONFIG > AI.
  * Backend reconnect no longer stalls the radio loop.
  NOT HARDWARE VALIDATED — the serial/SX1278 path has not been run on radios.

Flow (production, MODE B):
    ESP32-C6 VCU --CAN--> Raspberry Pi 4B --SX1278 LoRa--> ground radio bridge
    (ESP32+SX1278, USB) --> bridge.py (this file) --> FastAPI /ws/telemetry
    --> Next.js dashboard.   AI: GET /api/ai/recommendation --> LoRa downlink
    --> Pi --> CAN 0x160/0x161 --> VCU validates and decides.

Driving is NOT done over LoRa (≈5 Hz, ~50 ms air time per packet is too slow
for a 250 ms command timeout with margin) — use drive_client.py over Wi-Fi.

Self-contained on purpose: the codec pieces below are duplicated from
HARDWARE/raspberry_pi/main.py (two different machines, no shared library);
HARDWARE/tests keep the two copies byte-identical.

Usage:
    cd "HARDWARE/Ground Station"
    pip install websockets pyserial --break-system-packages

    # Real radios (ESP32+SX1278 bridge on USB):
    python bridge.py --radio serial --serial-port /dev/ttyUSB0      # COM5 on Windows

    # Bench substitute (Pi gateway run with --ground-link lora --lora-backend udp):
    python bridge.py --radio udp

    # Put the VCU in AI-assist aero mode once the link is up:
    python bridge.py --radio serial --serial-port COM5 --aero-mode ai
=============================================================================
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import struct
import time
import urllib.error
import urllib.request

logger = logging.getLogger("ground_bridge")

try:
    from websockets.exceptions import ConnectionClosed
except ImportError:
    class ConnectionClosed(Exception):
        pass

# =============================================================================
# SECTION 1: LoRa envelope — byte-identical to HARDWARE/raspberry_pi/main.py
# =============================================================================

LORA_MAGIC = 0xA5
LORA_PROTOCOL_VERSION = 3  # v3 (Stage 2d): track position / lap timing / config report; CONFIG_ACK (9)
LORA_HEADER_LEN = 9
LORA_CRC_LEN = 2
LORA_MAX_PAYLOAD = 64

# air settings — must match the Pi (LoRaConfig) and ground_radio_bridge.ino
LORA_FREQUENCY_HZ = 433_000_000   # Ra-02 / SX1278 band; must match the Pi
LORA_SPREADING_FACTOR = 7
LORA_BANDWIDTH_HZ = 250_000
LORA_TX_POWER_DBM = 10            # Ra-02 via PA_BOOST; must equal ground_radio_bridge.ino and the Pi


class LoRaMsgType:
    TELEMETRY_COMPACT = 1
    FAULT_EVENT = 2
    CONFIG_READ_REQ = 3
    CONFIG_READ_RESP = 4
    HEARTBEAT = 5
    AI_RECOMMENDATION = 6       # recommendation only — VCU validates (see LoRaProtocol.md)
    AI_RECOMMENDATION_SIM = 6   # Stage-1 name, kept so existing callers/tests still work
    DRIVER_COMMAND = 7          # ground -> vehicle -> CAN 0x118 (VCU 250 ms timeout)
    CONFIG_WRITE = 8            # ground -> vehicle -> CAN 0x180 (VCU validates, answers CONFIG_ACK)
    CONFIG_ACK = 9              # vehicle -> ground, relayed CAN 0x181


CONFIG_KEY_AERO_MODE = 1

# identical to raspberry_pi/main.py / vcu_logic.h ConfigKey (checked by HARDWARE/tests)
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
CONFIG_KEY_RUN_CMD, CONFIG_KEY_ESTOP = 6, 8
CONFIG_KEY_MOTOR_CMD = 9            # FW 2.3
DRIVE_MODE_NAMES = ["dyno", "track"]
HW_PROFILE_NAMES = ["prototype", "pcb_rev_b"]
CONFIG_STATUS_NAMES = ["applied", "staged_next_lap", "rejected_range", "rejected_state", "rejected_unknown_key"]
RUN_STATE_NAMES = ["off", "track_drive", "bench_laps", "bench_motor", "dyno_cal", "demo"]
DYNO_CAL_PHASE_NAMES = ["idle", "find_start", "hold_cap", "done", "failed"]
AI_REASON_NAMES = ["accepted", "not_populated", "low_confidence", "future_timestamp", "stale",
                   "non_finite", "out_of_range", "safe_state_override", "ai_mode_disabled", "none_received"]
TF_RUN_ACTIVE, TF_STRAIGHT_MODE, TF_BRAKING_ZONE, TF_CONFIG_PENDING = 0x01, 0x02, 0x04, 0x08
TF_BENCH_RUN, TF_SECTOR_SHIFT, TF_STALLED = 0x10, 5, 0x80


def track_flags_to_dict(flags: int) -> dict:
    return {"runActive": bool(flags & TF_RUN_ACTIVE), "straightMode": bool(flags & TF_STRAIGHT_MODE),
            "brakingZone": bool(flags & TF_BRAKING_ZONE), "configPending": bool(flags & TF_CONFIG_PENDING),
            "benchRun": bool(flags & TF_BENCH_RUN), "sector": (flags >> TF_SECTOR_SHIFT) & 0x03,
            "stalled": bool(flags & TF_STALLED)}


def lora_crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


class LoRaPacket:
    __slots__ = ("version", "msg_type", "sequence", "timestamp_ms", "payload")

    def __init__(self, version: int, msg_type: int, sequence: int, timestamp_ms: int,
                 payload: bytes = b""):
        self.version = version
        self.msg_type = msg_type
        self.sequence = sequence
        self.timestamp_ms = timestamp_ms
        self.payload = payload


def lora_encode(pkt: LoRaPacket) -> bytes:
    if len(pkt.payload) > LORA_MAX_PAYLOAD:
        raise ValueError("payload exceeds LORA_MAX_PAYLOAD")
    header = struct.pack("<BBBBIB", LORA_MAGIC, pkt.version, pkt.msg_type, pkt.sequence & 0xFF,
                          pkt.timestamp_ms & 0xFFFFFFFF, len(pkt.payload))
    body = header + pkt.payload
    return body + struct.pack("<H", lora_crc16_ccitt(body))


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
    (actual_crc,) = struct.unpack("<H", buf[LORA_HEADER_LEN + payload_len:total])
    if lora_crc16_ccitt(body) != actual_crc:
        return None
    _, _, msg_type, sequence, timestamp_ms, _ = struct.unpack("<BBBBIB", body[:LORA_HEADER_LEN])
    return LoRaPacket(version=version, msg_type=msg_type, sequence=sequence,
                       timestamp_ms=timestamp_ms, payload=body[LORA_HEADER_LEN:])


# --- SLIP (RFC 1055), identical to main.py -----------------------------------
SLIP_END, SLIP_ESC, SLIP_ESC_END, SLIP_ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
LINK_STATS_MAGIC = 0xB5  # [0xB5, rssi i16 (x10 dBm), snr i8 (x4 dB)] from ground_radio_bridge


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
    def __init__(self, max_len: int = 256):
        self._buf = bytearray()
        self._esc = False
        self._max = max_len

    def feed(self, data: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for b in data:
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
                if len(self._buf) > self._max:
                    self._buf.clear()
        return frames


# =============================================================================
# SECTION 2: payload codecs — byte-identical to main.py
# =============================================================================

_TELEMETRY_COMPACT_FMT = "<BHhhBBhhhhhhHhhhBBBBBBBBhhhhHBBBBHHBiHh"   # v3, 64 bytes
_AI_RECOMMENDATION_FMT = "<hhhhB"
_WING_Q = 5.0


def _round_half_away(v: float) -> int:
    """identical to main.py / C++ std::lround"""
    if not math.isfinite(v):
        return 0
    return math.floor(v + 0.5) if v >= 0 else math.ceil(v - 0.5)


def _clamp_i16(v: float) -> int:
    return max(-32768, min(32767, _round_half_away(v)))


def lora_decode_telemetry_compact(payload: bytes) -> dict | None:
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
        "motor_current_a": batt_ca / 100.0,
        "motor_temperature_c": motor_t_x10 / 10.0, "battery_temperature_c": batt_t_x10 / 10.0,
        "wing_lf_deg": wlf / _WING_Q, "wing_rf_deg": wrf / _WING_Q,
        "wing_lr_deg": wlr / _WING_Q, "wing_rr_deg": wrr / _WING_Q,
        "wing_target_lf_deg": tlf / _WING_Q, "wing_target_rf_deg": trf / _WING_Q,
        "wing_target_lr_deg": tlr / _WING_Q, "wing_target_rr_deg": trr / _WING_Q,
        "wheel_speed_fl_kmh": fl / 100.0, "wheel_speed_fr_kmh": fr / 100.0,
        "wheel_speed_rl_kmh": rl / 100.0, "wheel_speed_rr_kmh": rr / 100.0,
        "track_pos": pos / 65535.0, "lap": lap, "track_id": track, "track_flags": tflags,
        "run_state": run_cal & 0x07, "cal_phase": (run_cal >> 3) & 0x07,
        "drive_mode": (run_cal >> 6) & 0x01, "motor_power": bool(run_cal & 0x80),   # FW 2.2
        "current_lap_ms": cur_cs * 10, "last_lap_ms": last_cs * 10,
        "config_report_key": cfg_key, "config_report_value": cfg_val,
        "vibration_mg": float(vib), "gyro_z_dps": gz_x10 / 10.0,
    }


def lora_decode_config_ack(payload: bytes) -> dict | None:
    if len(payload) < 6:
        return None
    key, status, value = struct.unpack("<BBi", payload[:6])
    return {"key": key, "status": status, "value": value}


def lora_encode_ai_recommendation(lf: float, rf: float, lr: float, rr: float, confidence: float) -> bytes:
    conf_byte = max(0, min(255, int(confidence * 255)))
    return struct.pack(_AI_RECOMMENDATION_FMT, _clamp_i16(lf * 10), _clamp_i16(rf * 10),
                        _clamp_i16(lr * 10), _clamp_i16(rr * 10), conf_byte)


def lora_encode_driver_command(steering_permille: int, throttle_permille: int, flags: int) -> bytes:
    return struct.pack("<hhB", max(-1000, min(1000, int(steering_permille))),
                       max(-1000, min(1000, int(throttle_permille))), flags & 0xFF)


def lora_encode_config_write(key: int, value: int) -> bytes:
    return struct.pack("<Bi", key & 0xFF, int(value))


_VCU_STATE_BY_INDEX = [
    "POWER_ON", "INITIALIZATION", "SENSOR_CHECK", "IDLE", "IDLE",
    "DRIVE", "FAULT", "EMERGENCY_STOP", "FAULT",
]


def _vcu_state_name(index: int) -> str:
    if 0 <= index < len(_VCU_STATE_BY_INDEX):
        return _VCU_STATE_BY_INDEX[index]
    return "FAULT"


def _estimate_soc_pct(voltage_v: float) -> float:
    """[PLACEHOLDER] identical approximation to main.py's _estimate_soc_pct."""
    cells = 3
    v_min, v_max = 3.3 * cells, 4.2 * cells
    return max(0.0, min(100.0, (voltage_v - v_min) / (v_max - v_min) * 100.0))


WHEEL_DIAMETER_M = 0.070   # ASSUMED; must match vcu_logic.h CFG_WHEEL_DIAMETER_M and main.py


def compact_to_dashboard_frame(d: dict, timestamp_ms: int, schema_version: int = 1,
                               vcu_config: dict | None = None, config_acks: list | None = None) -> dict:
    """Same output shape as main.py's to_dashboard_frame() (+ stage2d_frame_fields).
    v3 carries both measured and target flap angles, all four wheel speeds and the
    VCU's track position, so the dashboard map and flap panels work over LoRa too."""
    wing = {"frontLeft": d["wing_lf_deg"], "frontRight": d["wing_rf_deg"],
            "rearLeft": d["wing_lr_deg"], "rearRight": d["wing_rr_deg"]}
    target = {"frontLeft": d["wing_target_lf_deg"], "frontRight": d["wing_target_rf_deg"],
              "rearLeft": d["wing_target_lr_deg"], "rearRight": d["wing_target_rr_deg"]}
    speed_ms = d["speed_kmh"] / 3.6
    circ = math.pi * WHEEL_DIAMETER_M
    ai_idx = d["last_ai_reason_index"]
    power = d["battery_voltage_v"] * d["battery_current_a"]
    return {
        "schemaVersion": schema_version, "timestamp": float(timestamp_ms),
        "speed": d["speed_kmh"], "voltage": d["battery_voltage_v"],
        "current": d["battery_current_a"], "motorCurrent": d["motor_current_a"],
        "motorTemp": d["motor_temperature_c"], "batteryTemp": d["battery_temperature_c"],
        "battery": _estimate_soc_pct(d["battery_voltage_v"]),
        "imu": {"roll": d["roll_deg"], "pitch": d["pitch_deg"], "yaw": d["yaw_deg"]},
        "steering": d["steering_angle_deg"], "wing": wing, "wingTarget": target,
        "throttle": d["throttle_pct"], "brake": d["brake_pct"],
        "gForceLat": d["accel_y_g"], "gForceLong": d["accel_x_g"],
        "rpm": speed_ms / circ * 60.0, "distance": 0.0, "wheelAngle": 0.0,
        "vcuState": _vcu_state_name(d["state_index"]),
        "derived": {"powerW": round(power, 2)},
        # --- Stage 2d (same keys as main.py stage2d_frame_fields) ---
        "wheels": {"fl": d["wheel_speed_fl_kmh"], "fr": d["wheel_speed_fr_kmh"],
                   "rl": d["wheel_speed_rl_kmh"], "rr": d["wheel_speed_rr_kmh"]},
        "gyroZ": d["gyro_z_dps"],
        "vibrationMg": d["vibration_mg"],
        "odometerM": None,            # not carried over LoRa (the Pi CSV has it)
        "aeroMode": d["aero_mode"],
        "lastAiReason": AI_REASON_NAMES[ai_idx] if 0 <= ai_idx < len(AI_REASON_NAMES) else str(ai_idx),
        "track": {
            "id": d["track_id"], "pos": round(d["track_pos"], 5), "lap": d["lap"],
            "currentLapMs": d["current_lap_ms"], "lastLapMs": d["last_lap_ms"],
            "profileDutyPct": None, "latGModel": None,
            **track_flags_to_dict(d["track_flags"]),
        },
        "runState": RUN_STATE_NAMES[d["run_state"]] if d["run_state"] < len(RUN_STATE_NAMES) else str(d["run_state"]),
        "dynoCal": DYNO_CAL_PHASE_NAMES[d["cal_phase"]] if d["cal_phase"] < len(DYNO_CAL_PHASE_NAMES) else str(d["cal_phase"]),
        "vcuConfig": vcu_config,
        "configAcks": config_acks or [],
        "vcu": compact_vcu_fields(d, vcu_config),
    }


def compact_vcu_fields(d: dict, vcu_config: dict | None) -> dict:
    """[FW 2.2] same keys as main.py vcu_mode_fields(). LoRa carries the drive mode and the
    motor-power bit in every packet; the hardware profile and run laps arrive in the VCU's
    config rotation; traction control, the knob and the VCU's own view of the link are not
    carried (None)."""
    cfg = vcu_config or {}
    hw = cfg.get("hwProfile")
    hw = int(hw) if isinstance(hw, (int, float)) else -1
    ks = None if hw not in (0, 1) else hw == 1
    run_cmd = cfg.get("runCmd")
    return {
        "driveMode": DRIVE_MODE_NAMES[d.get("drive_mode", 0)],
        "hwProfile": HW_PROFILE_NAMES[hw] if hw in (0, 1) else None,
        "killSwitchFitted": ks,
        "motorPower": None if ks is not True else bool(d.get("motor_power")),
        "remoteRun": None if run_cmd is None else bool(run_cmd) and d.get("run_state") in (2, 4),
        "tcActive": None, "tcCutPct": None, "groundLinkOk": None,
        "aeroStraight": bool(d["track_flags"] & TF_STRAIGHT_MODE),
        "encoderFitted": None, "knob": None,
        "runLaps": cfg.get("runLaps"),
    }


# =============================================================================
# SECTION 3: FAULT DECODING — identical table to main.py / vcu_logic.h.
# The compact payload carries 16 fault bits; bits 0..15 are defined.
# =============================================================================

_FAULT_TABLE: list[tuple[int, str, str, str, str]] = [
    (1 << 0, "SAFE-0x01", "Watchdog timeout", "critical", "Safety"),
    (1 << 1, "SNS-0x02", "Sensor data stale/timeout", "critical", "Sensors"),
    (1 << 2, "CAN-0x03", "CAN link timeout", "warning", "Comms"),
    (1 << 3, "LORA-0x04", "LoRa link timeout", "warning", "Comms"),
    (1 << 4, "BMS-0x05", "Battery pack undervoltage", "critical", "Battery"),
    (1 << 5, "PWR-0x06", "Overcurrent (pack or motor)", "critical", "Powertrain"),
    (1 << 6, "THR-0x07", "Motor overtemperature", "critical", "Powertrain"),
    (1 << 7, "BMS-0x08", "Battery overtemperature", "critical", "Battery"),
    (1 << 8, "SNS-0x09", "Invalid sensor reading (NaN/Inf/out-of-range)", "critical", "Sensors"),
    (1 << 9, "STR-0x0A", "Steering servo position error (feedback vs command)", "critical", "Steering"),
    (1 << 10, "AERO-0x0B", "Aero flap position error — flaps held at failsafe", "warning", "Aero"),
    (1 << 11, "AI-0x0C", "Last AI recommendation rejected by VCU", "warning", "AI"),
    (1 << 12, "COM-0x0D", "Driver command link lost", "critical", "Comms"),
    (1 << 13, "SAFE-0x0E", "Emergency stop latched", "critical", "Safety"),
    (1 << 14, "SAFE-0x0F", "VCU boot self-test failed", "critical", "Safety"),
    (1 << 15, "PWR-0x10", "Motor stall — duty applied, rear wheels not turning (run aborted)",
     "warning", "Powertrain"),
]


def decode_fault_flags(fault_flags: int, timestamp_ms: float) -> list[dict]:
    return [{"id": f"{code}-{int(timestamp_ms)}", "code": code, "message": message,
             "level": level, "subsystem": subsystem, "timestamp": timestamp_ms}
            for bit, code, message, level, subsystem in _FAULT_TABLE if fault_flags & bit]


# =============================================================================
# SECTION 4: GROUND-SIDE DIAGNOSTICS (radio link quality measured HERE)
# =============================================================================

def build_diagnostics(packets_received: int, packets_lost_estimate: int, seconds_since_last: float,
                      rssi_dbm: float | None = None, snr_db: float | None = None,
                      uplink_rate_hz: float = 0.0) -> dict:
    total = packets_received + packets_lost_estimate
    packet_loss_pct = 0.0 if total == 0 else (packets_lost_estimate / total) * 100.0
    link_ok = seconds_since_last < 2.0
    return {
        "can": "connected",  # no CAN visibility here; vehicle CAN health arrives in fault_flags
        "lora": "connected" if link_ok else "disconnected",
        "packetLoss": packet_loss_pct,
        "cpu": 0.0, "memory": 0.0,
        "watchdog": "ok" if link_ok else "warning",
        "ping": 0.0,
        "packetRate": uplink_rate_hz, "refreshRate": uplink_rate_hz, "uptimeS": 0.0,
        "temperatureC": 0.0,
        "canNodes": [],
        "loraLink": {"rssi": rssi_dbm if rssi_dbm is not None else 0.0,
                     "snr": snr_db if snr_db is not None else 0.0,
                     "spreadingFactor": LORA_SPREADING_FACTOR,
                     "bandwidthKhz": LORA_BANDWIDTH_HZ / 1000.0, "txPowerDbm": float(LORA_TX_POWER_DBM)},
        "radioStatsMeasured": rssi_dbm is not None,
    }


# =============================================================================
# SECTION 5: RACING ENGINEER STATION AI — fetched from the EXISTING backend
# =============================================================================
# The AI/perception/analysis system lives at the Racing Engineer Station —
# the SOFTWARE backend (app/ai_engine.py) — NOT here, and NOT on the Pi.
# This bridge just asks it for its current recommendation and relays that
# answer down the LoRa link to the Pi, exactly like it already
# relays telemetry up. compute_wing_recommendation_ground() used to be a
# local SAMPLE/PLACEHOLDER that closed this loop before the Engineer
# Station had a real (deterministic-simulated) AI of its own; now that
# app/ai_engine.py exists, this bridge defers to it instead of guessing
# locally — see fetch_engineer_station_recommendation() below.
#
# FAILURE BEHAVIOUR: if the backend is unreachable or has no recommendation
# yet (AI unavailable / no telemetry seen yet), this returns None and the
# caller simply skips sending a downlink recommendation this cycle — the
# vehicle keeps running on its own control loop either way (see project
# brief: "If AI stops: vehicle remains operational.").

_AI_RECOMMENDATION_TIMEOUT_S = 1.0


def fetch_engineer_station_recommendation(http_base: str) -> dict | None:
    """GET /api/ai/recommendation from the EXISTING SOFTWARE backend — the
    Racing Engineer Station's own AI, computed from the telemetry this
    bridge just forwarded it. Returns None (not a fabricated fallback) on
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
        # the downlink wire format (AI_RECOMMENDATION_SIM) only budgets for
        # lf/rf/lr/rr/confidence, same as before.
        "type": body.get("type"), "reason": body.get("reason"), "target": body.get("target"),
        "validUntil": body.get("validUntil"),
    }


def fetch_vehicle_commands(http_base: str) -> list[dict]:
    """[Stage 2d] identical to raspberry_pi/main.py fetch_vehicle_commands(): approved
    AI-engineer changes waiting for the car. [] on any failure."""
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
# SECTION 6: BACKEND CONNECTION — mirrors HARDWARE/raspberry_pi/main.py's
# _create_session()/TelemetryGatewayClient exactly (same envelope, same
# /api/sessions + /ws/telemetry endpoints, both EXISTING and unmodified).
# =============================================================================

def _http_base_from_ws_url(ws_url: str) -> str:
    scheme, _, rest = ws_url.partition("://")
    http_scheme = "https" if scheme == "wss" else "http"
    host_port = rest.split("/", 1)[0]
    return f"{http_scheme}://{host_port}"


def _create_session(http_base: str, mode: str = "practice") -> str:
    body = json.dumps({"mode": mode}).encode("utf-8")
    req = urllib.request.Request(f"{http_base}/api/sessions", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())["id"]


class BackendClient:
    def __init__(self, url: str):
        self._url = url
        self._ws = None

    async def connect(self):
        import websockets
        self._ws = await websockets.connect(self._url)
        # FIX (Stage 2c): the backend echoes every frame to the sender; unread
        # echoes stall keepalive and drop the socket after ~40 s. Drain them.
        self._drain = asyncio.create_task(_drain_ws(self._ws))

    async def send_snapshot(self, session_id: str, frame: dict, ai: dict | None,
                             diagnostics: dict, faults: list[dict]) -> None:
        if self._ws is None:
            raise RuntimeError("connect() must be called first")
        msg = json.dumps({
            "type": "frame", "sessionId": session_id,
            "snapshot": {"frame": frame, "ai": ai, "diagnostics": diagnostics, "faults": faults},
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
# SECTION 7: GROUND RADIOS
# _GroundRadioBase: envelope, sequence numbers, duplicate drop, loss estimate
# from uplink sequence gaps, downlink queue. Backends implement _tx/_rx.
# =============================================================================

class _GroundRadioBase:
    def __init__(self):
        self._last_sequence: int | None = None
        self._down_sequence = 0
        self.packets_received = 0
        self.duplicates_dropped = 0
        self.lost_estimate = 0
        self.decode_errors = 0
        self.rssi_dbm: float | None = None
        self.snr_db: float | None = None
        self._pending_config: list[tuple[int, int]] = []

    def _tx(self, data: bytes) -> bool:
        raise NotImplementedError

    def _rx(self) -> list[bytes]:
        raise NotImplementedError

    def poll(self) -> list[LoRaPacket]:
        out: list[LoRaPacket] = []
        for data in self._rx():
            pkt = lora_decode(data)
            if pkt is None:
                self.decode_errors += 1
                continue
            if self._last_sequence is not None:
                if pkt.sequence == self._last_sequence:
                    self.duplicates_dropped += 1
                    continue  # duplicate detection, matches LoRaProtocol.md
                gap = (pkt.sequence - self._last_sequence - 1) & 0xFF
                if gap < 64:  # larger jumps = Pi restarted, not loss
                    self.lost_estimate += gap
            self._last_sequence = pkt.sequence
            self.packets_received += 1
            out.append(pkt)
        return out

    def _send(self, msg_type: int, payload: bytes, now_ms: int) -> bool:
        # ONE downlink counter for every message type: the Pi drops a packet whose
        # sequence equals the previous one, so per-type counters could collide.
        seq = self._down_sequence
        self._down_sequence = (seq + 1) & 0xFF
        return self._tx(lora_encode(LoRaPacket(LORA_PROTOCOL_VERSION, msg_type, seq, now_ms, payload)))

    def send_recommendation(self, sequence: int, now_ms: int, lf: float, rf: float, lr: float,
                             rr: float, confidence: float) -> bool:
        # `sequence` kept for Stage-1 API compatibility; the wire sequence is the shared counter.
        return self._send(LoRaMsgType.AI_RECOMMENDATION,
                          lora_encode_ai_recommendation(lf, rf, lr, rr, confidence), now_ms)

    def send_driver_command(self, now_ms: int, steering_permille: int, throttle_permille: int, flags: int) -> bool:
        return self._send(LoRaMsgType.DRIVER_COMMAND,
                          lora_encode_driver_command(steering_permille, throttle_permille, flags), now_ms)

    def queue_config_write(self, key: int, value: int, repeats: int = 3) -> None:
        # [FW 2.3] a newer value for the same key replaces copies still waiting (a dragged motor
        # slider or a calibration nudge must not queue up seconds of stale values on LoRa)
        if key != CONFIG_KEY_ESTOP:
            self._pending_config = [(k, v) for k, v in self._pending_config if k != key]
        # [FW 2.2] an e-stop or a run STOP jumps the queue: it goes out in the very next slot
        # [FW 2.3] ... and so does a motor STOP (key 9 = 0)
        if key == CONFIG_KEY_ESTOP or (key in (CONFIG_KEY_RUN_CMD, CONFIG_KEY_MOTOR_CMD) and value == 0):
            self._pending_config[:0] = [(key, value)] * repeats
        else:
            self._pending_config.extend([(key, value)] * repeats)

    def pop_config_write(self) -> tuple[int, int] | None:
        return self._pending_config.pop(0) if self._pending_config else None

    def send_config_write(self, now_ms: int, key: int, value: int) -> bool:
        return self._send(LoRaMsgType.CONFIG_WRITE, lora_encode_config_write(key, value), now_ms)


class GroundRadio(_GroundRadioBase):
    """*** TEST SUBSTITUTE *** ground side of main.py's UdpLoRaRadio. The Pi's
    address is learned from the first uplink unless --pi-host pins it."""

    def __init__(self, listen_host: str, listen_port: int, pi_host: str | None, pi_port: int | None):
        super().__init__()
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.bind((listen_host, listen_port))
        self._pi_addr = (pi_host, pi_port) if pi_host else None
        self._pi_port_default = pi_port

    def _rx(self) -> list[bytes]:
        out: list[bytes] = []
        while True:
            try:
                data, addr = self._sock.recvfrom(256)
            except (BlockingIOError, OSError):
                break
            if self._pi_addr is None or self._pi_addr[0] != addr[0]:
                port = self._pi_addr[1] if self._pi_addr else (self._pi_port_default or addr[1])
                self._pi_addr = (addr[0], port)
            out.append(data)
        return out

    def _tx(self, data: bytes) -> bool:
        if self._pi_addr is None:
            return False  # haven't heard from a Pi gateway yet
        self._sock.sendto(data, self._pi_addr)
        return True


class GroundSerialRadio(_GroundRadioBase):
    """REAL radio: USB serial to ground_radio_bridge.ino (ESP32 + SX1278),
    SLIP framed both ways. The bridge firmware also emits a LINK_STATS frame
    after every received packet. NOT HARDWARE VALIDATED."""

    def __init__(self, port: str, baud: int = 115200):
        super().__init__()
        try:
            import serial  # pyserial
        except ImportError as exc:
            raise SystemExit("pyserial is required for --radio serial: pip install pyserial") from exc
        # Open with DTR/RTS de-asserted. ESP32 dev boards wire DTR/RTS to EN/IO0
        # (auto-reset); toggling them on open resets the modem, and on some
        # USB-UART drivers can leave it held in reset or in download mode.
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = port, baud, 0
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except serial.SerialException as exc:
            raise SystemExit(f"Cannot open {port} ({exc}). Is the Arduino Serial Monitor still open on it?") from exc
        self._ser = ser
        self._dec = SlipDecoder()
        time.sleep(1.5)                 # let the modem finish booting if the open did reset it
        self._ser.reset_input_buffer()  # discard ROM boot text

    def _rx(self) -> list[bytes]:
        n = self._ser.in_waiting
        out = []
        for f in (self._dec.feed(self._ser.read(n)) if n else []):
            if f and f[0] == LINK_STATS_MAGIC and len(f) >= 4:
                rssi_x10, snr_x4 = struct.unpack("<hb", f[1:4])
                self.rssi_dbm, self.snr_db = rssi_x10 / 10.0, snr_x4 / 4.0
            elif f and f[:1] == b"#":
                # status text from ground_radio_bridge.ino ("# tx=.. rx=.."), not a packet
                logger.info("radio modem: %s", f.decode("ascii", "replace").strip())
            else:
                out.append(f)
        return out

    def _tx(self, data: bytes) -> bool:
        self._ser.write(slip_encode(data))
        return True


# =============================================================================
# SECTION 8: MAIN LOOP — independent tasks so a slow/down backend never
# stalls the radio, and the downlink is sent only in the slot straight after
# an uplink (half-duplex LoRa: the Pi is listening then).
# =============================================================================

COMMAND_POLL_S = 0.25   # [FW 2.2] dashboard Stop / E-STOP are queued for the next downlink slot quickly


async def run(args: argparse.Namespace) -> None:
    if args.radio == "serial":
        radio: _GroundRadioBase = GroundSerialRadio(args.serial_port, args.serial_baud)
        logger.info("Ground radio: serial SLIP on %s @ %d", args.serial_port, args.serial_baud)
    else:
        radio = GroundRadio(listen_host=args.listen_host, listen_port=args.listen_port,
                            pi_host=args.pi_host, pi_port=args.pi_port)
        logger.info("*** TEST SUBSTITUTE RADIO *** UDP %s:%d", args.listen_host, args.listen_port)

    if args.aero_mode is not None:
        radio.queue_config_write(CONFIG_KEY_AERO_MODE, {"baseline": 0, "ai": 1, "hold": 2}[args.aero_mode])

    http_base = args.http_url or _http_base_from_ws_url(args.url)
    client = None if args.no_backend else BackendClient(args.url)
    session_id = args.session_id
    backend_ok = False
    latest_rec: dict | None = None
    latest_rec_monotonic = 0.0
    last_uplink_monotonic: float | None = None
    vcu_config: dict = {}           # rebuilt from the one-key-per-packet CONFIG_REPORT
    pending_acks: list[dict] = []   # CONFIG_ACKs waiting to ride on the next frame to the backend
    stop = asyncio.Event()

    async def backend_task():
        nonlocal backend_ok, session_id
        backoff = 2.0
        while not stop.is_set():
            if backend_ok:
                await asyncio.sleep(0.5)
                continue
            try:
                if session_id is None:
                    session_id = await asyncio.to_thread(_create_session, http_base)
                    logger.info("Created backend session %s", session_id)
                await asyncio.wait_for(client.connect(), timeout=3.0)
                backend_ok, backoff = True, 2.0
                logger.info("Connected to %s", args.url)
            except (urllib.error.URLError, OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
                logger.warning("Backend unavailable (%s) — retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 10.0)

    async def ai_task():
        nonlocal latest_rec, latest_rec_monotonic
        last_cmd_poll = 0.0
        while not stop.is_set():
            fresh_uplink = last_uplink_monotonic is not None and time.monotonic() - last_uplink_monotonic < 2.0
            if backend_ok and fresh_uplink and time.monotonic() - last_cmd_poll >= COMMAND_POLL_S:
                # [Stage 2d] approved changes -> LoRa CONFIG_WRITE (sent before any AI rec)
                last_cmd_poll = time.monotonic()
                for c in await asyncio.to_thread(fetch_vehicle_commands, http_base):
                    radio.queue_config_write(c["key"], c["value"], repeats=1)
                    logger.info("approved change queued for the car: key=%d (%s) value=%d", c["key"],
                                CONFIG_KEY_NAMES.get(c["key"], "?"), c["value"])
            if backend_ok and fresh_uplink:
                rec = await asyncio.to_thread(fetch_engineer_station_recommendation, http_base)
                if rec is not None:
                    latest_rec, latest_rec_monotonic = rec, time.monotonic()
            await asyncio.sleep(1.0 / args.ai_hz)

    tasks = []
    if client is not None:
        tasks = [asyncio.create_task(backend_task()), asyncio.create_task(ai_task())]

    ai_sequence = 0
    last_downlink_monotonic = 0.0
    rate_window: list[float] = []
    poll_period_s = 1.0 / max(args.poll_rate, 1.0)
    start = time.monotonic()
    try:
        while args.duration_s <= 0 or time.monotonic() - start < args.duration_s:
            got_uplink = False
            for pkt in radio.poll():
                now = time.monotonic()
                if pkt.msg_type == LoRaMsgType.TELEMETRY_COMPACT:
                    d = lora_decode_telemetry_compact(pkt.payload)
                    if d is None:
                        continue
                    got_uplink = True
                    last_uplink_monotonic = now
                    rate_window = [t for t in rate_window if now - t < 2.0] + [now]
                    if d["config_report_key"]:
                        vcu_config[CONFIG_KEY_NAMES.get(d["config_report_key"], str(d["config_report_key"]))] = \
                            d["config_report_value"]
                        vcu_config["pending"] = bool(d["track_flags"] & TF_CONFIG_PENDING)
                    frame = compact_to_dashboard_frame(d, pkt.timestamp_ms, vcu_config=dict(vcu_config),
                                                       config_acks=pending_acks)
                    diag = build_diagnostics(radio.packets_received, radio.lost_estimate, 0.0,
                                             radio.rssi_dbm, radio.snr_db, len(rate_window) / 2.0)
                    faults = decode_fault_flags(d["fault_flags"], frame["timestamp"])
                    if client is not None and backend_ok:
                        try:
                            await asyncio.wait_for(client.send_snapshot(session_id, frame, None, diag, faults), 0.5)
                            pending_acks = []
                        except (OSError, ConnectionClosed, asyncio.TimeoutError, RuntimeError) as exc:
                            logger.warning("Backend send failed (%s) — reconnecting", exc)
                            backend_ok = False
                            await client.close()
                    if args.verbose:
                        logger.info("uplink seq=%d state=%s speed=%.1f V=%.2f rssi=%s",
                                    pkt.sequence, frame["vcuState"], d["speed_kmh"], d["battery_voltage_v"],
                                    radio.rssi_dbm)
                elif pkt.msg_type == LoRaMsgType.CONFIG_ACK:
                    ack = lora_decode_config_ack(pkt.payload)
                    if ack is not None:
                        got_uplink = True
                        pending_acks.append(ack)
                        logger.info("VCU CONFIG_ACK key=%d (%s): %s value=%d", ack["key"],
                                    CONFIG_KEY_NAMES.get(ack["key"], "?"),
                                    CONFIG_STATUS_NAMES[ack["status"]] if ack["status"] < len(CONFIG_STATUS_NAMES)
                                    else ack["status"], ack["value"])
                elif pkt.msg_type == LoRaMsgType.FAULT_EVENT and len(pkt.payload) >= 2:
                    got_uplink = True
                    (flags,) = struct.unpack("<H", pkt.payload[:2])
                    logger.warning("FAULT_EVENT from vehicle: flags=0x%04x %s", flags,
                                   [f["code"] for f in decode_fault_flags(flags, 0)])

            # --- downlink slot: exactly one packet, right after an uplink ---
            if got_uplink:
                now_ms = int(time.time() * 1000) & 0xFFFFFFFF
                cfg = radio.pop_config_write()
                if cfg is not None:
                    radio.send_config_write(now_ms, *cfg)
                    last_downlink_monotonic = time.monotonic()
                    logger.info("CONFIG_WRITE key=%d value=%d sent", *cfg)
                elif latest_rec is not None and time.monotonic() - latest_rec_monotonic < 0.4:
                    rec = latest_rec
                    if radio.send_recommendation(ai_sequence, now_ms, rec["lf_deg"], rec["rf_deg"],
                                                 rec["lr_deg"], rec["rr_deg"], rec["confidence"]):
                        ai_sequence = (ai_sequence + 1) & 0xFF
                        last_downlink_monotonic = time.monotonic()
                    latest_rec = None  # never resend the same answer
                    if args.verbose:
                        logger.info("AI recommendation relayed (type=%s conf=%.2f)", rec.get("type"),
                                    rec["confidence"])
                elif time.monotonic() - last_downlink_monotonic > 1.0:
                    # FIX (Stage 2c): keep-alive so the Pi's lora_ok bit (and the VCU's
                    # LoRa-timeout warning) reflects a working downlink even when the
                    # backend/AI has nothing to send.
                    if radio._send(LoRaMsgType.HEARTBEAT, b"", now_ms):
                        last_downlink_monotonic = time.monotonic()

            if last_uplink_monotonic is not None and time.monotonic() - last_uplink_monotonic > 2.0 and args.verbose:
                logger.warning("no uplink for %.1fs — vehicle radio link may be down",
                               time.monotonic() - last_uplink_monotonic)
            await asyncio.sleep(poll_period_s)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if client is not None:
            await client.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Ground-station bridge: LoRa (real or UDP substitute) -> backend")
    p.add_argument("--radio", choices=["serial", "udp"], default="udp",
                   help="'serial' = ESP32+SX1278 ground_radio_bridge over USB; 'udp' = bench substitute")
    p.add_argument("--serial-port", default="/dev/ttyUSB0", help="[serial] e.g. /dev/ttyUSB0 or COM5")
    p.add_argument("--serial-baud", type=int, default=115200)
    p.add_argument("--listen-host", default="0.0.0.0", help="[udp] bind address")
    p.add_argument("--listen-port", type=int, default=5010, help="[udp] must match main.py --ground-port")
    p.add_argument("--pi-host", default=None, help="[udp] pin the Pi's address instead of auto-learning")
    p.add_argument("--pi-port", type=int, default=5011, help="[udp] must match main.py --radio-listen-port")
    p.add_argument("--url", default="ws://127.0.0.1:8000/ws/telemetry", help="Backend WebSocket URL")
    p.add_argument("--http-url", default=None, help="Backend REST base (default: derived from --url)")
    p.add_argument("--no-backend", action="store_true", help="Just log received uplinks")
    p.add_argument("--session-id", default=None)
    p.add_argument("--ai-hz", type=float, default=5.0, help="How often to refresh the AI recommendation")
    p.add_argument("--aero-mode", choices=["baseline", "ai", "hold"], default=None,
                   help="Queue a CONFIG_WRITE aero_mode for the vehicle (sent in the next downlink slots)")
    p.add_argument("--poll-rate", type=float, default=100.0, help="Radio poll rate (Hz)")
    p.add_argument("--duration-s", type=float, default=0.0, help="Stop after N seconds (0 = forever)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
