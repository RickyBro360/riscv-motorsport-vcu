"""
test_fw23_chain.py — FW 2.3 end to end, every real program, the REAL VCU logic, no hardware.

    backend (uvicorn) <- main.py (MODE A, Wi-Fi WebSocket) <- CAN -> virtual car (prototype build)
    backend (uvicorn) <- bridge.py (MODE B, UDP radio) <- LoRa v3 -> main.py (Pi) <- CAN -> virtual car

What the dashboard does in FW 2.3, followed all the way to vcu_logic.h and back:
  - the knob on the car steers; the steering and the four flaps show up on the dashboard
    (right turn: left flaps up, right flaps down)
  - the motor runs from the dashboard in TRACK mode, its duty changes live, Stop stops it
  - a servo end point set on the dashboard is applied by the VCU and reported back
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time
from pathlib import Path

import pytest

HARDWARE_DIR = Path(__file__).resolve().parent.parent
can = pytest.importorskip("can")
pytest.importorskip("uvicorn")
pytest.importorskip("websockets")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import virtual_car  # noqa: E402
from test_fw22_chain import _cmd, _frame, backend  # noqa: E402,F401  (fixture re-used)
from test_stage2d_chain import _free_port, _get, _load, _post  # noqa: E402


@pytest.fixture(scope="module")
def proto_exe(tmp_path_factory):
    import shutil
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    return virtual_car.build_sim(tmp_path_factory.mktemp("vsim23"), 0)    # prototype: no kill switch


async def _wait(pred, timeout_s: float, period_s: float = 0.1):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        v = await asyncio.to_thread(pred)
        if v:
            return v
        await asyncio.sleep(period_s)
    return None


def test_knob_steering_flaps_and_dashboard_motor_over_mode_a(backend, proto_exe):
    pi = _load("fw23_pi_a", HARDWARE_DIR / "raspberry_pi" / "main.py")
    channel = f"virtual_fw23a_{time.time_ns()}"
    ws = backend.replace("http", "ws") + "/ws/telemetry"
    dur = 16.0
    pi_args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--ground-link", "direct", "--url", ws, "--drive-port", "0", "--no-log", "--duration-s", str(dur)])
    car = virtual_car.VirtualCar(proto_exe, channel, dyno=True)
    seen: dict = {}

    async def operator():
        await asyncio.sleep(2.5)
        seen["boot"] = await asyncio.to_thread(_frame, backend)
        # four clicks clockwise on the knob = 12 deg RIGHT
        car.set("knob", 4)
        # steering rides on 0x115 (20 ms), the knob count on 0x148 (100 ms): wait for both
        seen["turned"] = await _wait(lambda: (f := _frame(backend)) and f.get("steering", 0) < -11.5
                                     and (f.get("vcu") or {}).get("knob") == 4 and f, 3.0)
        # the motor from the dashboard, still in TRACK mode
        t_m = time.monotonic()
        seen["motor"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/motor", {"dutyPct": 45})
        seen["running"] = await _wait(lambda: (f := _frame(backend)) and f.get("throttle", 0) > 40 and f, 4.0)
        seen["motor_latency_s"] = time.monotonic() - t_m
        seen["status"] = await asyncio.to_thread(_get, f"{backend}/api/vehicle/status")
        await asyncio.to_thread(_post, f"{backend}/api/vehicle/motor", {"dutyPct": 70})
        seen["faster"] = await _wait(lambda: (f := _frame(backend)) and f.get("throttle", 0) > 65 and f, 4.0)
        # a calibration end point from the dashboard
        seen["cal"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/config", {"name": "steerLeftUs", "value": 1111})
        seen["cal_view"] = await _wait(
            lambda: (c := _get(f"{backend}/api/vehicle/calibration")) and
            next(k for k in c["keys"] if k["name"] == "steerLeftUs")["value"] == 1111 and c, 8.0, 0.25)
        seen["stop"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/motor", {"dutyPct": 0})
        seen["stopped"] = await _wait(lambda: (f := _frame(backend)) and f.get("runState") == "off" and f.get("throttle") == 0 and f, 3.0)
        seen["centre"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/steering/center", {})
        seen["centred"] = await _wait(lambda: (f := _frame(backend)) and abs(f.get("steering", 9)) < 0.3 and f, 3.0)

    async def main():
        await asyncio.gather(pi.run(pi_args), car.run(dur + 0.5), operator())

    asyncio.run(main())

    boot = seen["boot"]
    assert boot["vcuState"] == "IDLE" and boot["vcu"]["driveMode"] == "track" and boot["vcu"]["hwProfile"] == "prototype"
    turned = seen["turned"]
    assert turned, "knob clicks never showed up as steering on the dashboard"
    assert turned["vcu"]["knob"] == 4
    w = turned["wingTarget"]
    assert w["frontLeft"] > w["frontRight"] + 5 and w["rearLeft"] > w["rearRight"] + 5     # right turn: left flaps up
    run = seen["running"]
    assert run, "the motor never ran"
    assert run["runState"] == "bench_motor" and run["vcu"]["driveMode"] == "track" and run["wheels"]["rl"] > 0.2
    assert seen["motor_latency_s"] < 2.0, seen["motor_latency_s"]
    assert _cmd(backend, seen["motor"]["id"])["status"] == "applied"
    assert seen["status"]["motorRunning"] is True
    assert seen["faster"], "the duty did not change while running"
    assert seen["cal_view"], "the calibration change was not reported back"
    assert _cmd(backend, seen["cal"]["id"])["status"] == "applied"
    assert seen["stopped"], "the motor did not stop"
    assert seen["centred"] and seen["centred"]["vcu"]["knob"] == 0
    acked = {(k, v) for k, s, v in car.acks if s == 0}
    assert {(9, 450), (9, 700), (9, 0), (30, 1111), (23, 1)} <= acked


def test_dashboard_motor_and_estop_over_lora(backend, proto_exe):
    pi = _load("fw23_pi_b", HARDWARE_DIR / "raspberry_pi" / "main.py")
    bridge = _load("fw23_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")
    channel = f"virtual_fw23b_{time.time_ns()}"
    ground_port, pi_radio_port = _free_port(socket.SOCK_DGRAM), _free_port(socket.SOCK_DGRAM)
    ws = backend.replace("http", "ws") + "/ws/telemetry"
    dur = 16.0
    pi_args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--ground-link", "lora", "--lora-backend", "udp", "--ground-host", "127.0.0.1",
        "--ground-port", str(ground_port), "--radio-listen-port", str(pi_radio_port),
        "--drive-port", "0", "--no-log", "--duration-s", str(dur)])
    br_args = argparse.Namespace(radio="udp", serial_port="", serial_baud=115200, listen_host="127.0.0.1",
                                 listen_port=ground_port, pi_host="127.0.0.1", pi_port=pi_radio_port,
                                 url=ws, http_url=None, no_backend=False, session_id=None, ai_hz=5.0,
                                 aero_mode=None, poll_rate=100.0, duration_s=dur + 0.3, verbose=False)
    car = virtual_car.VirtualCar(proto_exe, channel, dyno=True)
    seen: dict = {}

    async def operator():
        await asyncio.sleep(3.0)
        car.set("knob", -3)                                   # 9 deg LEFT
        seen["turned"] = await _wait(lambda: (f := _frame(backend)) and f.get("steering", 0) > 8.5 and f, 4.0)
        seen["motor"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/motor", {"dutyPct": 50})
        seen["running"] = await _wait(lambda: (f := _frame(backend)) and f.get("throttle", 0) > 45 and f, 5.0)
        t_e = time.monotonic()
        await asyncio.to_thread(_post, f"{backend}/api/vehicle/estop", {"action": "trigger"})
        while car.state() != 7 and time.monotonic() - t_e < 4:
            await asyncio.sleep(0.02)
        seen["estop_latency_s"] = time.monotonic() - t_e
        seen["stopped"] = await _wait(lambda: (f := _frame(backend)) and f.get("vcuState") == "EMERGENCY_STOP" and f, 4.0)

    async def main():
        await asyncio.gather(bridge.run(br_args), pi.run(pi_args), car.run(dur + 0.5), operator())

    asyncio.run(main())

    turned = seen["turned"]
    assert turned, "knob steering not seen over LoRa"
    w = turned["wingTarget"]
    assert w["frontRight"] > w["frontLeft"] + 5                                     # left turn: right flaps up
    assert seen["running"] and seen["running"]["runState"] == "bench_motor"
    assert seen["estop_latency_s"] < 2.5, seen["estop_latency_s"]
    st = seen["stopped"]
    assert st and st["throttle"] == 0 and st["runState"] == "off"
    assert st["steering"] == 0 and st["wingTarget"]["frontLeft"] == 0                # E-STOP: failsafe
