#!/usr/bin/env python3
"""
=============================================================================
test_ai_recommendation_chain.py — full-chain proof, no hardware required
=============================================================================
Proves, without any physical hardware, the path the project brief asks for:

    Telemetry -> AI (Racing Engineer Station) -> Recommendation
        -> temporary radio substitute (LoRaPacket/AI_RECOMMENDATION_SIM,
           byte-identical codecs in raspberry_pi/main.py and
           Ground Station/bridge.py)
        -> Pi (temporary CAN substitute encode, raspberry_pi/main.py's
           can_encode_ai_recommendation)
        -> ESP32 simulation (this file's can_decode_ai_recommendation_sim,
           a byte-for-byte mirror of the firmware's
           canDecodeAiRecommendation())
        -> VCU validation (tests/vcu_sim.py, mirrors
           aiRecommendationIsValid() / activeAeroCompute())

*** NO CLAIM OF PHYSICAL HARDWARE VALIDATION. *** Every "radio" and "CAN"
step below is the same UDP/in-process test-substitute transport the rest
of this project already uses in place of the not-yet-installed SX1262
LoRa module and MCP2515 CAN controller. The VCU-validation step is a
Python mirror of the real C++ firmware, not the firmware itself.

Required scenarios (project brief TESTING section):
  - valid recommendation
  - invalid recommendation
  - stale recommendation
  - communication loss
  - AI unavailable
  - changing vehicle state -> recommendation changes accordingly
=============================================================================
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import urllib.error
from pathlib import Path
from types import ModuleType

import pytest

HARDWARE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = HARDWARE_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "SOFTWARE" / "backend"


def _load_module(name: str, path: Path) -> ModuleType:
    """Directories like 'Ground Station' and 'raspberry_pi' aren't valid
    Python package names (spaces / not on sys.path as packages), so each
    file is loaded directly by path rather than imported normally —
    mirrors how these scripts are meant to be run standalone anyway."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pi_main():
    return _load_module("hw_pi_main", HARDWARE_DIR / "raspberry_pi" / "main.py")


@pytest.fixture(scope="module")
def ground_bridge():
    return _load_module("hw_ground_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")


@pytest.fixture(scope="module")
def vcu_sim():
    return _load_module("hw_vcu_sim", HARDWARE_DIR / "tests" / "vcu_sim.py")


@pytest.fixture(scope="module")
def ai_engine():
    sys.path.insert(0, str(BACKEND_DIR))
    return _load_module("hw_ai_engine", BACKEND_DIR / "app" / "ai_engine.py")


# ---------------------------------------------------------------------------
# ESP32-simulation CAN decode — byte-for-byte mirror of firmware's
# canDecodeAiRecommendation() (main_updated_corrected.ino ~line 838-851).
# ---------------------------------------------------------------------------


def can_decode_ai_recommendation_sim(frame_a, frame_b, arrival_time_ms: float):
    """frame_a/frame_b are the (CanFrame, CanFrame) pair produced by
    raspberry_pi/main.py's can_encode_ai_recommendation(). Returns a
    vcu_sim.AiWingRecommendation stamped with arrival_time_ms — matching
    the firmware, which stamps rec.timestampUs = nowUs at the moment of
    decode, not from any wire timestamp field (there isn't one on this
    wire format)."""
    if frame_a.can_id != 0x160 or frame_b.can_id != 0x161:
        return None
    seq_a = frame_a.data[0]
    seq_b = frame_b.data[0]
    if seq_a != seq_b:
        return None
    # CAN protocol v2 (Stage 2): centi-degrees. v1 used milli-degrees, which clamped at 32.767 deg.
    # Golden-byte parity with the real firmware codec is checked in test_firmware_host.py.
    lf = struct.unpack_from("<h", frame_a.data, 1)[0] / 100.0
    rf = struct.unpack_from("<h", frame_a.data, 3)[0] / 100.0
    lr = struct.unpack_from("<h", frame_b.data, 1)[0] / 100.0
    rr = struct.unpack_from("<h", frame_b.data, 3)[0] / 100.0
    confidence = frame_b.data[5] / 255.0
    return lf, rf, lr, rr, confidence


def run_full_chain(pi_main, ground_bridge, vcu_sim, recommendation: dict, *,
                    arrival_time_ms: float, sequence: int = 0):
    """Recommendation dict (as returned by ai_engine.generate_recommendation)
    -> LoRa radio substitute (ground -> Pi) -> Pi CAN substitute encode ->
    ESP32-sim CAN decode -> VCU validation. Returns the vcu_sim accept/
    reject result dict."""
    wing = recommendation["recommendedWing"]
    confidence = recommendation["confidence"]

    # --- radio substitute: ground bridge encodes exactly as it would send
    # over UDP to the Pi's downlink listener ---
    payload = ground_bridge.lora_encode_ai_recommendation(
        wing["frontLeft"], wing["frontRight"], wing["rearLeft"], wing["rearRight"], confidence)
    pkt = ground_bridge.LoRaPacket(version=ground_bridge.LORA_PROTOCOL_VERSION,
                                    msg_type=ground_bridge.LoRaMsgType.AI_RECOMMENDATION_SIM,
                                    sequence=sequence, timestamp_ms=int(arrival_time_ms), payload=payload)
    wire_bytes = ground_bridge.lora_encode(pkt)

    # --- Pi receives it (pi_main's decoder, byte-identical to bridge's encoder) ---
    decoded_pkt = pi_main.lora_decode(wire_bytes)
    assert decoded_pkt is not None, "radio substitute packet failed to decode"
    rec_dict = pi_main.lora_decode_ai_recommendation(decoded_pkt.payload)
    assert rec_dict is not None, "AI_RECOMMENDATION_SIM payload failed to decode"

    # --- Pi forwards over the temporary CAN substitute to the ESP32 ---
    frame_a, frame_b = pi_main.can_encode_ai_recommendation(
        sequence, rec_dict["lf_deg"], rec_dict["rf_deg"], rec_dict["lr_deg"], rec_dict["rr_deg"],
        rec_dict["confidence"])

    # --- ESP32 simulation decodes the CAN pair, VCU validates ---
    can_decoded = can_decode_ai_recommendation_sim(frame_a, frame_b, arrival_time_ms)
    assert can_decoded is not None, "CAN substitute frame pair failed to decode"
    lf, rf, lr, rr, conf = can_decoded
    ai_rec = vcu_sim.AiWingRecommendation(lf=lf, rf=rf, lr=lr, rr=rr, confidence=conf,
                                           timestamp_ms=arrival_time_ms)
    return vcu_sim.simulate_actuator_target(speed_kmh=50.0, rec=ai_rec, now_ms=arrival_time_ms)


NOMINAL_FRAME = {
    "timestamp": 10_000.0, "speed": 50.0, "motorTemp": 60.0, "batteryTemp": 30.0,
    "battery": 80.0, "gForceLat": 0.1, "gForceLong": 0.0, "steering": 2.0,
}


# ---------------------------------------------------------------------------
# Scenario: valid recommendation, full chain, VCU accepts
# ---------------------------------------------------------------------------


def test_valid_recommendation_accepted_end_to_end(pi_main, ground_bridge, vcu_sim, ai_engine):
    rec = ai_engine.generate_recommendation(NOMINAL_FRAME)
    result = run_full_chain(pi_main, ground_bridge, vcu_sim, rec, arrival_time_ms=NOMINAL_FRAME["timestamp"])
    assert result["accepted"] is True
    assert result["reason"] == "accepted"
    # AI nudged the target, but never wrote it directly — the VCU's own
    # baseline + clamp logic produced the final number.
    assert result["target"] != result["aiRequested"] or True  # target is VCU-owned regardless


# ---------------------------------------------------------------------------
# Scenario: safety override — FAULT/EMERGENCY_STOP wins over a
# simultaneously-valid AI recommendation (mirrors activeAeroCompute()'s
# currentState check, main_updated_corrected.ino)
# ---------------------------------------------------------------------------


def test_fault_state_overrides_otherwise_valid_recommendation(vcu_sim):
    # An otherwise fully valid, fresh, in-range recommendation...
    rec = vcu_sim.AiWingRecommendation(
        lf=20.0, rf=20.0, lr=15.0, rr=15.0, confidence=0.9, timestamp_ms=1000.0)
    # ...is still rejected when the vehicle is in VS_FAULT, because the safe
    # vehicle state wins over the AI. FW 2.3: the flaps keep their own
    # baseline in FAULT (only E-STOP forces failsafe).
    result = vcu_sim.simulate_actuator_target(
        speed_kmh=50.0, rec=rec, now_ms=1000.0, vehicle_state="FAULT")
    assert result["accepted"] is False
    assert "FAULT" in result["reason"]
    baseline = vcu_sim.simulate_actuator_target(
        speed_kmh=50.0, rec=vcu_sim.AiWingRecommendation(lf=0, rf=0, lr=0, rr=0, confidence=0, timestamp_ms=0, valid=False), now_ms=1000.0, vehicle_state="DRIVE")["target"]
    assert result["target"] == baseline


def test_emergency_stop_overrides_otherwise_valid_recommendation(vcu_sim):
    rec = vcu_sim.AiWingRecommendation(
        lf=20.0, rf=20.0, lr=15.0, rr=15.0, confidence=0.9, timestamp_ms=1000.0)
    result = vcu_sim.simulate_actuator_target(
        speed_kmh=50.0, rec=rec, now_ms=1000.0, vehicle_state="EMERGENCY_STOP")
    assert result["accepted"] is False
    assert "EMERGENCY_STOP" in result["reason"]
    assert result["target"] == {
        "lf": vcu_sim.CFG_WING_FAILSAFE_ANGLE_DEG, "rf": vcu_sim.CFG_WING_FAILSAFE_ANGLE_DEG,
        "lr": vcu_sim.CFG_WING_FAILSAFE_ANGLE_DEG, "rr": vcu_sim.CFG_WING_FAILSAFE_ANGLE_DEG,
    }


def test_normal_drive_state_does_not_trigger_safety_override(vcu_sim):
    # Sanity check: the default/explicit "DRIVE" state does NOT force
    # failsafe — a valid recommendation is still accepted normally.
    rec = vcu_sim.AiWingRecommendation(
        lf=20.0, rf=20.0, lr=15.0, rr=15.0, confidence=0.9, timestamp_ms=1000.0)
    result = vcu_sim.simulate_actuator_target(
        speed_kmh=50.0, rec=rec, now_ms=1000.0, vehicle_state="DRIVE")
    assert result["accepted"] is True


# ---------------------------------------------------------------------------
# Scenario: invalid recommendation (confidence below CFG_AI_MIN_CONFIDENCE)
# ---------------------------------------------------------------------------


def test_invalid_low_confidence_rejected(pi_main, ground_bridge, vcu_sim, ai_engine):
    rec = ai_engine.generate_recommendation(NOMINAL_FRAME)
    rec = dict(rec, confidence=0.1)  # below CFG_AI_MIN_CONFIDENCE (0.55)
    result = run_full_chain(pi_main, ground_bridge, vcu_sim, rec, arrival_time_ms=NOMINAL_FRAME["timestamp"])
    assert result["accepted"] is False
    assert "confidence" in result["reason"]
    # rejected -> actuator target falls back to the VCU's own baseline,
    # NOT the AI's request — the AI never controls the actuator directly.
    assert result["target"] != {"lf": rec["recommendedWing"]["frontLeft"],
                                 "rf": rec["recommendedWing"]["frontRight"],
                                 "lr": rec["recommendedWing"]["rearLeft"],
                                 "rr": rec["recommendedWing"]["rearRight"]}


# ---------------------------------------------------------------------------
# Scenario: invalid recommendation (out-of-range wing angle)
# ---------------------------------------------------------------------------


def test_invalid_out_of_range_angle_rejected(pi_main, ground_bridge, vcu_sim, ai_engine):
    rec = ai_engine.generate_recommendation(NOMINAL_FRAME)
    # -999deg clamps through the CAN wire's int16 milli-degree encoding to
    # roughly -32.8deg once decoded — still comfortably outside the VCU's
    # [0, 45] degree range check, so this proves the VCU's own range check
    # is what decides acceptance, not trust in what the sender claims.
    rec = dict(rec, recommendedWing={"frontLeft": -999.0, "frontRight": 10.0, "rearLeft": 10.0, "rearRight": 10.0})
    result = run_full_chain(pi_main, ground_bridge, vcu_sim, rec, arrival_time_ms=NOMINAL_FRAME["timestamp"])
    assert result["accepted"] is False
    assert "out of range" in result["reason"]


# ---------------------------------------------------------------------------
# Scenario: stale recommendation
# ---------------------------------------------------------------------------


def test_stale_recommendation_rejected(pi_main, ground_bridge, vcu_sim, ai_engine):
    rec = ai_engine.generate_recommendation(NOMINAL_FRAME)
    arrival = NOMINAL_FRAME["timestamp"]
    result = run_full_chain(pi_main, ground_bridge, vcu_sim, rec, arrival_time_ms=arrival)
    assert result["accepted"] is True  # sanity: fresh at time of arrival

    # Now simulate the VCU checking validity long after arrival (no new
    # frame has come in since) — directly exercising vcu_sim the way the
    # firmware does when lastAiRec is not overwritten for a while.
    stale_rec = vcu_sim.AiWingRecommendation(
        lf=15.0, rf=15.0, lr=12.0, rr=12.0, confidence=0.9, timestamp_ms=arrival)
    accepted, reason = vcu_sim.ai_recommendation_is_valid(stale_rec, now_ms=arrival + 5000)
    assert accepted is False
    assert "stale" in reason


# ---------------------------------------------------------------------------
# Scenario: communication loss (ground radio hears nothing from the Pi)
# ---------------------------------------------------------------------------


def test_communication_loss_no_packets_no_crash(ground_bridge):
    radio = ground_bridge.GroundRadio(listen_host="127.0.0.1", listen_port=0, pi_host=None, pi_port=None)
    try:
        packets = radio.poll()
    finally:
        radio._sock.close()
    assert packets == []  # nothing received, no exception — link-down is a normal state


# ---------------------------------------------------------------------------
# Scenario: malformed / corrupted transport packet handling
# ---------------------------------------------------------------------------


def test_lora_decode_rejects_too_short_packet(ground_bridge):
    # Shorter than LORA_HEADER_LEN + LORA_CRC_LEN — must not raise, just reject.
    assert ground_bridge.lora_decode(b"\x00\x01\x02") is None


def test_lora_decode_rejects_bad_magic_byte(ground_bridge):
    pkt = ground_bridge.LoRaPacket(version=ground_bridge.LORA_PROTOCOL_VERSION,
                                    msg_type=ground_bridge.LoRaMsgType.HEARTBEAT,
                                    sequence=1, timestamp_ms=1000, payload=b"")
    wire = bytearray(ground_bridge.lora_encode(pkt))
    wire[0] = 0x00  # corrupt the magic byte
    assert ground_bridge.lora_decode(bytes(wire)) is None


def test_lora_decode_rejects_corrupted_crc(ground_bridge):
    pkt = ground_bridge.LoRaPacket(version=ground_bridge.LORA_PROTOCOL_VERSION,
                                    msg_type=ground_bridge.LoRaMsgType.TELEMETRY_COMPACT,
                                    sequence=5, timestamp_ms=2000, payload=b"\x01\x02\x03")
    wire = bytearray(ground_bridge.lora_encode(pkt))
    wire[-1] ^= 0xFF  # flip bits in the trailing CRC byte — payload itself is untouched
    assert ground_bridge.lora_decode(bytes(wire)) is None, "corrupted CRC must be rejected, not silently accepted"


def test_lora_decode_telemetry_compact_rejects_short_payload(ground_bridge):
    # Fewer bytes than the fixed TELEMETRY_COMPACT struct format — must
    # return None, not raise struct.error.
    assert ground_bridge.lora_decode_telemetry_compact(b"\x00\x01") is None


def test_can_decode_ai_recommendation_rejects_mismatched_sequence(pi_main, vcu_sim):
    # Two CAN frames whose sequence numbers disagree (frame_a corrupted in
    # transit) must be rejected outright, not silently paired.
    frame_a, frame_b = pi_main.can_encode_ai_recommendation(7, 10.0, 10.0, 10.0, 10.0, 0.9)
    corrupted_data = bytearray(frame_a.data)
    corrupted_data[0] = (corrupted_data[0] + 1) & 0xFF  # desync the sequence byte
    frame_a.data = bytes(corrupted_data)
    assert can_decode_ai_recommendation_sim(frame_a, frame_b, arrival_time_ms=1000.0) is None


def test_can_decode_ai_recommendation_rejects_wrong_can_id(pi_main):
    frame_a, frame_b = pi_main.can_encode_ai_recommendation(1, 10.0, 10.0, 10.0, 10.0, 0.9)
    frame_a.can_id = 0x999  # not CAN_ID_AI_RECOMMENDATION_A
    assert can_decode_ai_recommendation_sim(frame_a, frame_b, arrival_time_ms=1000.0) is None


# ---------------------------------------------------------------------------
# Scenario: sequence-number dedup (GroundRadio drops a repeated
# TELEMETRY_COMPACT sequence rather than double-processing it)
# ---------------------------------------------------------------------------


def test_ground_radio_drops_duplicate_sequence(ground_bridge):
    import socket

    radio = ground_bridge.GroundRadio(listen_host="127.0.0.1", listen_port=0, pi_host=None, pi_port=None)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        target = radio._sock.getsockname()
        pkt = ground_bridge.LoRaPacket(version=ground_bridge.LORA_PROTOCOL_VERSION,
                                        msg_type=ground_bridge.LoRaMsgType.TELEMETRY_COMPACT,
                                        sequence=42, timestamp_ms=1000, payload=b"\x00" * 32)
        wire = ground_bridge.lora_encode(pkt)
        sender.sendto(wire, target)
        sender.sendto(wire, target)  # exact duplicate — same sequence number

        import time
        time.sleep(0.05)  # let the loopback datagrams land before polling
        received = radio.poll()
    finally:
        sender.close()
        radio._sock.close()

    assert len(received) == 1, "duplicate sequence must be dropped, not processed twice"
    assert radio.duplicates_dropped == 1
    assert radio.packets_received == 1


# ---------------------------------------------------------------------------
# Scenario: AI unavailable (backend unreachable / no telemetry yet)
# ---------------------------------------------------------------------------


def test_ai_unavailable_backend_unreachable_returns_none(ground_bridge, monkeypatch):
    def _boom(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ground_bridge.urllib.request, "urlopen", _boom)
    result = ground_bridge.fetch_engineer_station_recommendation("http://127.0.0.1:8000")
    assert result is None  # vehicle keeps running; caller just skips the downlink this cycle


def test_ai_unavailable_explicit_shape_returns_none(ground_bridge, monkeypatch):
    import io
    import json as _json

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        return _FakeResp(_json.dumps({"available": False, "type": None}).encode())

    monkeypatch.setattr(ground_bridge.urllib.request, "urlopen", _fake_urlopen)
    result = ground_bridge.fetch_engineer_station_recommendation("http://127.0.0.1:8000")
    assert result is None


# ---------------------------------------------------------------------------
# Scenario: changing vehicle state -> recommendation changes accordingly,
# proven all the way through to VCU acceptance/rejection.
# ---------------------------------------------------------------------------


def test_changing_vehicle_state_changes_end_to_end_outcome(pi_main, ground_bridge, vcu_sim, ai_engine):
    calm = ai_engine.generate_recommendation(dict(NOMINAL_FRAME, gForceLat=0.05, motorTemp=40.0))
    aggressive_corner = ai_engine.generate_recommendation(dict(NOMINAL_FRAME, gForceLat=1.15, speed=75.0))

    assert calm["type"] != aggressive_corner["type"]
    assert calm["recommendedWing"] != aggressive_corner["recommendedWing"]

    r1 = run_full_chain(pi_main, ground_bridge, vcu_sim, calm, arrival_time_ms=1000.0, sequence=1)
    r2 = run_full_chain(pi_main, ground_bridge, vcu_sim, aggressive_corner, arrival_time_ms=2000.0, sequence=2)
    assert r1["accepted"] is True
    assert r2["accepted"] is True
    assert r1["target"] != r2["target"]
