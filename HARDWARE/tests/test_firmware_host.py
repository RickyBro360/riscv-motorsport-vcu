"""
test_firmware_host.py — the ESP32-C6 firmware logic, compiled for the PC,
checked against the Raspberry Pi gateway's Python codec and tests/vcu_sim.py.

Why: vcu_logic.h is the exact header the sketch compiles. Previously the
Python side was compared against a hand-written Python "mirror" of the
firmware, which can drift silently. Here the real C++ is executed.

Needs g++ (skipped otherwise). What it proves / does not prove:
  PROVES   CAN v2 byte layout, scaling and rounding are identical on both
           ends; AI acceptance and aero blend match vcu_sim.py; state
           machine / safety / motor / servo maths behave as specified.
  DOES NOT prove anything about real sensors, TWAI timing or servos.
"""
from __future__ import annotations

import importlib.util
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

HARDWARE_DIR = Path(__file__).resolve().parent.parent
SKETCH_DIR = HARDWARE_DIR / "esp32" / "main_updated_corrected__1_"
CPP = Path(__file__).resolve().parent / "firmware_host_test.cpp"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pi():
    return _load("fw_pi_main", HARDWARE_DIR / "raspberry_pi" / "main.py")


@pytest.fixture(scope="module")
def vcu_sim():
    return _load("fw_vcu_sim", HARDWARE_DIR / "tests" / "vcu_sim.py")


@pytest.fixture(scope="module")
def fw(tmp_path_factory):
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    exe = tmp_path_factory.mktemp("fw") / "firmware_host_test"
    r = subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{SKETCH_DIR}",
                        str(CPP), "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, f"vcu_logic.h failed to compile on host:\n{r.stderr}"
    return exe


def _rpc(fw, lines: list[str]) -> list[str]:
    r = subprocess.run([str(fw), "rpc"], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip().splitlines()
    assert len(out) == len(lines), out
    assert not any(o.startswith("ERR") for o in out), out
    return out


def _hex(frame) -> str:
    return f"{frame.can_id:03X}:" + frame.data.hex().upper()


def _frame(pi, text: str):
    cid, data = text.split(":")
    return pi.CanFrame(can_id=int(cid, 16), data=bytes.fromhex(data))


# --------------------------------------------------------------------------
def test_firmware_selftest(fw):
    r = subprocess.run([str(fw), "selftest"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "SELFTEST PASSED" in r.stdout, r.stdout


AI_CASES = [(0, 0.0, 0.0, 0.0, 0.0, 0.0), (7, 12.3, 45.0, 0.0, 33.3, 0.9), (255, 44.9, 0.1, 22.5, 8.8, 1.0),
            (1, 32.8, 40.0, 45.0, 44.4, 0.55), (2, -5.0, 60.0, 400.0, -400.0, 0.54)]
# every angle the LoRa downlink can deliver (0.1 deg steps, 0..45)
AI_CASES += [(k % 256, k / 10, (450 - k) / 10, k / 10, (450 - k) / 10, (k % 256) / 255) for k in range(0, 451, 7)]


def test_ai_recommendation_bytes_identical(fw, pi):
    out = _rpc(fw, [f"ai_encode {s} {lf!r} {rf!r} {lr!r} {rr!r} {c!r}" for s, lf, rf, lr, rr, c in AI_CASES])
    for case, line in zip(AI_CASES, out):
        a, b = pi.can_encode_ai_recommendation(*case)
        assert line == f"{_hex(a)} {_hex(b)}", f"firmware and Pi disagree for {case}"


def test_firmware_decodes_pi_ai_frames(fw, pi):
    lines = []
    for case in AI_CASES:
        a, b = pi.can_encode_ai_recommendation(*case)
        lines.append(f"ai_decode {a.data.hex()} {b.data.hex()}")
    for case, line in zip(AI_CASES, _rpc(fw, lines)):
        ok, lf, rf, lr, rr, conf, seq = line.split()
        assert ok == "1" and int(seq) == case[0] & 0xFF
        for got, want in zip((lf, rf, lr, rr), case[1:5]):
            assert abs(float(got) - max(-327.68, min(327.67, want))) < 0.006
        assert abs(float(conf) - int(case[5] * 255) / 255) < 1e-4


def test_golden_bytes_documented_in_canprotocol_md(fw):
    """These exact strings are the worked examples in SOFTWARE/Documentation/CANProtocol.md."""
    out = _rpc(fw, ["ai_encode 7 12.3 45 0 33.3 0.9", "enc battery 11.1 34.5", "enc aero_target 0 12.3 33.3 45",
                    "enc state 5 4097"])
    assert out == ["160:07CE04941100 161:070000020DE5", "130:5C2B7A0D", "151:0000CE04020D9411", "100:0501100000"]


def test_vcu_to_pi_frames_decode_on_pi(fw, pi):
    cmds = ["enc state 5 20480", "enc wheel 12.34 -0.5 99.99 0",
            "enc driver_inputs -12.5 55 0", "enc accel 0.05 -1.25 0.98", "enc gyro 120.5 -3.3 0",
            "enc attitude -12.3 4.5 359.9", "enc battery 12.6 -34.56", "enc temperature 65.5 31.2 28.75",
            "enc aero_target 0 12.3 33.3 45", "enc aero_measured 44.99 0.01 22.5 7.77",
            "enc fault_event 4096 3 6", "enc vcu_status 1 4 1 31 3600 2 2"]
    out = _rpc(fw, cmds)
    f = [_frame(pi, o) for o in out]
    assert pi.can_decode_vehicle_state(f[0]) == (5, 20480)
    assert pi.can_decode_wheel_speed(f[1]) == pytest.approx((12.34, -0.5, 99.99, 0.0), abs=0.006)
    assert pi.can_decode_driver_inputs(f[2]) == pytest.approx((-12.5, 55, 0), abs=0.002)
    assert pi.can_decode_imu_accel(f[3]) == pytest.approx((0.05, -1.25, 0.98), abs=0.0006)
    assert pi.can_decode_imu_gyro(f[4]) == pytest.approx((120.5, -3.3, 0.0), abs=0.06)
    assert pi.can_decode_imu_attitude(f[5]) == pytest.approx((-12.3, 4.5, 359.9), abs=0.06)
    assert pi.can_decode_battery(f[6]) == pytest.approx((12.6, -34.56), abs=0.006)   # > 32.767 A: v1 would clamp
    assert pi.can_decode_temperature(f[7]) == pytest.approx((65.5, 31.2, 28.75), abs=0.06)
    assert pi.can_decode_aero_target(f[8]) == pytest.approx((0, 12.3, 33.3, 45.0), abs=0.006)  # > 32.767 deg
    assert pi.can_decode_aero_measured(f[9]) == pytest.approx((44.99, 0.01, 22.5, 7.77), abs=0.006)
    assert pi.can_decode_fault_event(f[10]) == (4096, 3, 6)
    st = pi.can_decode_vcu_status(f[11])
    assert st["aeroMode"] == 1 and st["lastAiReason"] == "stale" and st["driverLinkOk"] is True
    assert st["servoFeedbackCalibratedMask"] == 31 and st["uptimeS"] == 3600 and st["protocolVersion"] == 2


def test_pi_to_vcu_command_frames_decode_on_firmware(fw, pi):
    drv = [(0, 0, 0, 0), (17, -1000, 1000, 1), (255, 999, -1000, 6), (3, 1500, 0, 1)]  # last clamps on Pi
    hbs = [(0, False, False), (200, True, False), (255, True, True)]
    cfgs = [(1, 0), (1, 2), (9, -123456)]
    lines = [f"dec_driver {pi.can_encode_driver_command(*d).data.hex()}" for d in drv]
    lines += [f"dec_hb {pi.can_encode_pi_heartbeat(*h).data.hex()}" for h in hbs]
    lines += [f"dec_cfg {pi.can_encode_config_write(*c).data.hex()}" for c in cfgs]
    out = _rpc(fw, lines)
    for d, line in zip(drv, out[:4]):
        assert line.split() == ["1", str(d[0]), str(max(-1000, min(1000, d[1]))), str(d[2]), str(d[3])]
    for h, line in zip(hbs, out[4:7]):
        assert line.split() == ["1", str(h[0]), str(int(h[1])), str(int(h[2]))]
    for c, line in zip(cfgs, out[7:]):
        assert line.split() == ["1", str(c[0]), str(c[1])]
    assert pi.CAN_ID_DRIVER_COMMAND == 0x118 and pi.CAN_ID_PI_HEARTBEAT == 0x190 and pi.CAN_ID_CONFIG_WRITE == 0x180


AI_CHECK_CASES = [  # valid, conf, ts, now, lf, rf, lr, rr
    (1, 0.9, 1000, 1000, 10, 10, 10, 10), (0, 0.9, 1000, 1000, 10, 10, 10, 10),
    (1, 0.54, 1000, 1000, 10, 10, 10, 10), (1, 0.55, 1000, 1000, 10, 10, 10, 10),
    (1, 0.9, 1001, 1000, 10, 10, 10, 10), (1, 0.9, 1000, 1500, 10, 10, 10, 10),
    (1, 0.9, 1000, 1501, 10, 10, 10, 10), (1, 0.9, 1000, 1000, -0.1, 10, 10, 10),
    (1, 0.9, 1000, 1000, 10, 10, 10, 45.1), (1, 0.9, 1000, 1000, 45.0, 0.0, 45.0, 0.0),
    (1, float("nan"), 1000, 1000, 10, 10, 10, 10), (1, 0.9, 1000, 1000, 10, float("nan"), 10, 10),
]


def test_ai_acceptance_matches_vcu_sim(fw, vcu_sim):
    out = _rpc(fw, ["ai_check " + " ".join(repr(float(v)) if isinstance(v, float) else str(v) for v in c)
                    for c in AI_CHECK_CASES])
    for c, reason in zip(AI_CHECK_CASES, out):
        rec = vcu_sim.AiWingRecommendation(lf=c[4], rf=c[5], lr=c[6], rr=c[7], confidence=c[1],
                                           timestamp_ms=c[2], valid=bool(c[0]))
        accepted, _ = vcu_sim.ai_recommendation_is_valid(rec, c[3])
        assert (reason == "accepted") == accepted, f"{c}: firmware={reason} sim={accepted}"


def test_aero_blend_matches_vcu_sim(fw, vcu_sim):
    cases = []
    for speed in (0.0, 7.9, 8.0, 20.0, 50.0, 80.0):
        for state in ("DRIVE", "FAULT", "EMERGENCY_STOP"):
            for rec in ((1, 0.9, 1000, 1000, 40.0, 0.0, 25.0, 10.0), (1, 0.3, 1000, 1000, 40.0, 0.0, 25.0, 10.0),
                        (1, 0.9, 0, 1000, 40.0, 0.0, 25.0, 10.0)):
                cases.append((speed, state, rec))
    code = {"DRIVE": 0, "FAULT": 1, "EMERGENCY_STOP": 2}
    out = _rpc(fw, [f"aero {s} {code[st]} " + " ".join(str(v) for v in r) for s, st, r in cases])
    for (speed, state, r), line in zip(cases, out):
        applied, *desired = line.split()
        rec = vcu_sim.AiWingRecommendation(lf=r[4], rf=r[5], lr=r[6], rr=r[7], confidence=r[1], timestamp_ms=r[2])
        sim = vcu_sim.simulate_actuator_target(speed_kmh=speed, rec=rec, now_ms=r[3], vehicle_state=state)
        assert (applied == "1") == sim["accepted"], (speed, state, r)
        for got, ch in zip(desired, ("lf", "rf", "lr", "rr")):
            assert math.isclose(float(got), sim["target"][ch], abs_tol=1e-3), (speed, state, r, ch)


def test_state_indices_match_pi_table(pi):
    text = (SKETCH_DIR / "vcu_logic.h").read_text()
    assert "VS_BOOT = 0, VS_INIT = 1, VS_SELF_TEST = 2, VS_IDLE = 3, VS_ARMED = 4" in text
    assert "VS_DRIVE = 5, VS_FAULT = 6, VS_EMERGENCY_STOP = 7, VS_SHUTDOWN = 8" in text
    assert pi._VCU_STATE_BY_INDEX[5] == "DRIVE" and pi._VCU_STATE_BY_INDEX[7] == "EMERGENCY_STOP"


def test_fault_bits_match_pi_and_bridge(pi):
    import re
    text = (SKETCH_DIR / "vcu_logic.h").read_text()
    fw_bits = {m.group(1): int(m.group(2)) for m in re.finditer(r"FAULT_BIT_(\w+)\s*=\s*1u << (\d+)", text)}
    assert len(fw_bits) == 16   # Stage 2d: bit 15 MOTOR_STALL
    for name, bit in fw_bits.items():
        assert getattr(pi, f"FAULT_BIT_{name}") == 1 << bit, name
    bridge = _load("fw_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")
    assert [row[:2] for row in bridge._FAULT_TABLE] == [row[:2] for row in pi._FAULT_TABLE]


# ------------------------------------------------------------------ Stage 2d
def test_stage2d_frames_decode_identically_on_the_pi(fw, pi):
    out = _rpc(fw, [
        "enc track_position 0.25 3 12 11 0.62 -1.5",
        "enc lap_timing 83120 45670",
        "enc dyno 123.456 42.4 2 3",
        "enc config_ack 17 1 700",
        "enc config_report 15 -50 1",
        "enc derived2 10 20 30 55.5",
        "enc config_write 16 -7",
    ])
    tp = pi.can_decode_track_position(_frame(pi, out[0]))
    assert tp["pos"] == pytest.approx(0.25, abs=2e-5) and tp["lap"] == 3 and tp["trackId"] == 12
    assert tp["flags"] == 11 and tp["profileDutyPct"] == 62 and tp["latGModel"] == pytest.approx(-1.5)
    assert pi.can_decode_lap_timing(_frame(pi, out[1])) == (83120, 45670)
    dy = pi.can_decode_dyno(_frame(pi, out[2]))
    assert dy == {"odometerM": 123.45, "vibrationMg": 42.0, "runState": 2, "calPhase": 3}
    assert pi.can_decode_config_ack(_frame(pi, out[3])) == {"key": 17, "status": 1, "value": 700}
    assert pi.can_decode_config_report(_frame(pi, out[4])) == {"key": 15, "value": -50, "pending": True}
    derived = _frame(pi, out[5])
    assert pi.can_decode_vehicle_derived(derived)[0] == pytest.approx(0.010)
    assert struct.unpack("<H", derived.data[6:8])[0] == 56
    assert out[6] == _hex(pi.can_encode_config_write(16, -7))


def test_track_maps_header_matches_tracks_json(fw):
    """The VCU drives from track_maps.h; the backend AI and the dashboard read
    tracks.json. Both come from build_tracks.py and must describe the same laps."""
    import json
    doc = json.loads((HARDWARE_DIR.parent / "SOFTWARE" / "tracks" / "tracks.json").read_text())
    dash = json.loads((HARDWARE_DIR.parent / "SOFTWARE" / "dashboard" / "public" / "tracks.json").read_text())
    assert [t["code"] for t in doc["tracks"]] == [t["code"] for t in dash["tracks"]]
    lines = []
    for t in doc["tracks"]:
        lines.append(f"track_info {t['id']}")
        for frac in (0.0, 0.25, 0.5, 0.75):
            lines.append(f"track_sample {t['id']} {frac}")
    out = iter(_rpc(fw, lines))
    for t in doc["tracks"]:
        code, length, vmax, lap_ms = next(out).split()
        assert code == t["code"] and int(length) == round(t["lengthM"])
        assert int(vmax) == round(t["model"]["vmaxKmh"]) and int(lap_ms) == round(t["model"]["lapTimeS"] * 1000)
        n = doc["points"]
        for frac in (0.0, 0.25, 0.5, 0.75):
            steer, speed, latg, straight, braking, sector = next(out).split()
            p = t["vcuTable"][int(frac * n)]
            assert float(steer) == pytest.approx(p["steerQ"] * 0.25, abs=1e-3)
            assert float(speed) == pytest.approx(p["speedQ"] / 255, abs=1e-3)
            assert int(sector) == (p["flags"] >> 2) & 3
    assert doc["tracks"][doc["defaultTrackId"]]["code"] == "ITA"


# ------------------------------------------------------------------ FW 2.2
def test_fw22_mode_frame_decodes_identically_on_the_pi(fw, pi):
    out = _rpc(fw, ["enc vcu_mode 1 1 87 5 42 -3", "enc vcu_mode 0 0 0 0 0 0"])
    vm = pi.can_decode_vcu_mode(_frame(pi, out[0]))
    assert vm == {"driveMode": 1, "hwProfile": 1, "flags": 87, "runLaps": 5, "tcCutPct": 42, "knob": -3}
    f = pi.vcu_mode_fields(vm["driveMode"], vm["hwProfile"], vm["flags"], vm["runLaps"], vm["tcCutPct"], vm["knob"])
    assert f["driveMode"] == "track" and f["hwProfile"] == "pcb_rev_b" and f["killSwitchFitted"] is True
    assert f["motorPower"] is True and f["remoteRun"] is True and f["groundLinkOk"] is True and f["tcActive"] is False
    proto = pi.vcu_mode_fields(0, 0, 0, 0, 0, 0)
    assert proto["driveMode"] == "dyno" and proto["killSwitchFitted"] is False and proto["motorPower"] is None


def test_fw22_config_keys_identical_in_firmware_pi_and_bridge(pi):
    import re
    text = (SKETCH_DIR / "vcu_logic.h").read_text()
    fw = {int(m.group(2)) for m in re.finditer(r"CFG_KEY_(\w+)\s*=\s*(\d+),", text)}
    bridge = _load("fw_bridge22", HARDWARE_DIR / "Ground Station" / "bridge.py")
    assert set(pi.CONFIG_KEY_NAMES) == fw
    assert bridge.CONFIG_KEY_NAMES == pi.CONFIG_KEY_NAMES
    assert {5, 6, 7, 8, 22, 104, 105} <= fw


def test_fw22_lora_compact_carries_drive_mode_and_motor_power(pi):
    vt = pi.VehicleTelemetry(drive_mode=1, mode_flags=pi.MF_MOTOR_POWER | pi.MF_KILL_SWITCH_FITTED, run_state=1)
    payload = pi.lora_encode_telemetry_compact(vt)
    assert len(payload) == 64
    d = pi.lora_decode_telemetry_compact(payload)
    assert d["drive_mode"] == 1 and d["motor_power"] is True and d["run_state"] == 1
    bridge = _load("fw_bridge22b", HARDWARE_DIR / "Ground Station" / "bridge.py")
    db = bridge.lora_decode_telemetry_compact(payload)
    assert db["drive_mode"] == 1 and db["motor_power"] is True
    frame = bridge.compact_to_dashboard_frame(db, 1000, vcu_config={"hwProfile": 1, "runCmd": 0, "runLaps": 3})
    assert frame["vcu"]["driveMode"] == "track" and frame["vcu"]["hwProfile"] == "pcb_rev_b"
    assert frame["vcu"]["motorPower"] is True and frame["vcu"]["runLaps"] == 3
    # same keys on both paths (MODE A and MODE B)
    a = pi.to_dashboard_frame(vt)
    assert set(a["vcu"]) == set(frame["vcu"])


def test_fw22_estop_jumps_the_lora_queue():
    bridge = _load("fw_bridge22c", HARDWARE_DIR / "Ground Station" / "bridge.py")
    r = bridge._GroundRadioBase()
    r.queue_config_write(17, 700, repeats=2)
    r.queue_config_write(8, 1, repeats=1)
    r.queue_config_write(6, 0, repeats=1)
    assert r.pop_config_write() == (6, 0) and r.pop_config_write() == (8, 1) and r.pop_config_write() == (17, 700)
