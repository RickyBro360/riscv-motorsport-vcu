"""
test_fw22_chain.py — FW 2.2 end to end, every real program, the REAL VCU logic, no hardware.

    backend (uvicorn) <- bridge.py (MODE B, UDP radio) <- LoRa v3 -> main.py (Pi) <- CAN -> virtual car
    backend (uvicorn) <- main.py (MODE A, Wi-Fi WebSocket) <- CAN -> virtual car  (+ drive link UDP)

The virtual car is vcu_host_sim.cpp: vcu_logic.h's VcuController (the code the ESP32-C6 runs)
with a small plant model. These tests follow the dashboard's buttons all the way to the VCU
and back: drive mode, start run, e-stop, reset — and a driver in track mode over the drive link.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

HARDWARE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = HARDWARE_DIR.parent / "SOFTWARE" / "backend"
can = pytest.importorskip("can")
uvicorn = pytest.importorskip("uvicorn")
pytest.importorskip("websockets")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import virtual_car  # noqa: E402
from test_stage2d_chain import _free_port, _get, _load, _post  # noqa: E402


@pytest.fixture(scope="module")
def sim_exe(tmp_path_factory):
    import shutil
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    return virtual_car.build_sim(tmp_path_factory.mktemp("vsim"), 1)


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    db = tmp_path_factory.mktemp("fw22chain") / "chain.db"
    os.environ["VCU_DB_PATH"] = str(db)
    sys.path.insert(0, str(BACKEND_DIR))
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    from app.main import app  # noqa: E402
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    t0 = time.time()
    while not server.started and time.time() - t0 < 10:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)
    sys.path.remove(str(BACKEND_DIR))


def _frame(backend) -> dict:
    return (_get(f"{backend}/api/vehicle/state") or {}).get("frame") or {}


def _cmd(backend, cid) -> dict:
    return next(c for c in _get(f"{backend}/api/vehicle/commands") if c["id"] == cid)


def test_dashboard_controls_reach_the_real_vcu_logic_over_lora(backend, sim_exe):
    pi = _load("fw22_pi", HARDWARE_DIR / "raspberry_pi" / "main.py")
    bridge = _load("fw22_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")
    channel = f"virtual_fw22_{time.time_ns()}"
    ground_port, pi_radio_port = _free_port(socket.SOCK_DGRAM), _free_port(socket.SOCK_DGRAM)
    ws = backend.replace("http", "ws") + "/ws/telemetry"
    dur = 21.0
    pi_args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--ground-link", "lora", "--lora-backend", "udp", "--ground-host", "127.0.0.1",
        "--ground-port", str(ground_port), "--radio-listen-port", str(pi_radio_port),
        "--drive-port", "0", "--no-log", "--duration-s", str(dur)])
    br_args = argparse.Namespace(radio="udp", serial_port="", serial_baud=115200, listen_host="127.0.0.1",
                                 listen_port=ground_port, pi_host="127.0.0.1", pi_port=pi_radio_port,
                                 url=ws, http_url=None, no_backend=False, session_id=None, ai_hz=5.0,
                                 aero_mode=None, poll_rate=100.0, duration_s=dur + 0.3, verbose=False)
    car = virtual_car.VirtualCar(sim_exe, channel, dyno=True)
    seen: dict = {}

    async def operator():
        await asyncio.sleep(2.5)
        seen["boot"] = await asyncio.to_thread(_frame, backend)
        seen["refused"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/run", {"action": "start"})
        await asyncio.sleep(1.5)
        seen["mode"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/mode", {"mode": "dyno"})
        await asyncio.sleep(2.0)
        seen["start"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/run", {"action": "start", "laps": 0})
        await asyncio.sleep(4.0)
        seen["running"] = await asyncio.to_thread(_frame, backend)
        seen["status_running"] = await asyncio.to_thread(_get, f"{backend}/api/vehicle/status")
        seen["car_running"] = (car.mode(), car.state())
        t_e = time.monotonic()
        seen["estop"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/estop", {"action": "trigger"})
        while car.state() != 7 and time.monotonic() - t_e < 4:
            await asyncio.sleep(0.02)
        seen["estop_latency_s"] = time.monotonic() - t_e
        await asyncio.sleep(1.5)
        seen["stopped"] = await asyncio.to_thread(_frame, backend)
        seen["reset"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/estop", {"action": "reset"})
        await asyncio.sleep(3.0)
        seen["after_reset"] = await asyncio.to_thread(_frame, backend)
        seen["track"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/mode", {"mode": "track"})
        await asyncio.sleep(2.0)
        seen["final"] = await asyncio.to_thread(_frame, backend)

    async def main():
        await asyncio.gather(bridge.run(br_args), pi.run(pi_args), car.run(dur + 0.5), operator())

    asyncio.run(main())

    boot = seen["boot"]
    assert boot["vcuState"] == "IDLE" and boot["vcu"]["driveMode"] == "track"      # safe default
    assert _cmd(backend, seen["refused"]["id"])["status"] == "rejected_state"      # no runs on the floor
    assert _cmd(backend, seen["mode"]["id"])["status"] == "applied"
    assert _cmd(backend, seen["start"]["id"])["status"] == "applied"
    run = seen["running"]
    assert run["runState"] == "bench_laps" and run["track"]["runActive"] and run["vcu"]["driveMode"] == "dyno"
    assert run["vcu"]["motorPower"] is True and run["vcu"]["hwProfile"] == "pcb_rev_b"
    assert run["wheels"]["rl"] > 0.5 and run["throttle"] > 20
    mode, state = seen["car_running"]
    assert state == 3 and mode["flags"] & 0x04                                      # remote run flag on the car
    assert seen["status_running"]["remoteRun"] in (True, None)
    assert seen["estop_latency_s"] < 2.5, seen["estop_latency_s"]                  # LoRa slot + outbox poll
    stopped = seen["stopped"]
    assert stopped["vcuState"] == "EMERGENCY_STOP" and stopped["runState"] == "off"
    assert stopped["vcu"]["motorPower"] is False and stopped["throttle"] == 0
    assert seen["after_reset"]["vcuState"] == "IDLE"
    assert _cmd(backend, seen["track"]["id"])["status"] == "applied"
    assert seen["final"]["vcu"]["driveMode"] == "track"
    # every command the dashboard sent arrived at the VCU as CAN 0x180 and was ACKed
    acked = {(k, v) for k, s, v in car.acks}
    assert {(5, 0), (6, 1), (8, 1), (8, 2), (5, 1)} <= acked
    assert (6, 3, 1) in car.acks                                                    # the refused first start


def test_track_mode_driver_over_mode_a_with_dashboard_estop(backend, sim_exe):
    pi = _load("fw22_pi_a", HARDWARE_DIR / "raspberry_pi" / "main.py")
    channel = f"virtual_fw22a_{time.time_ns()}"
    drive_port = _free_port(socket.SOCK_DGRAM)
    ws = backend.replace("http", "ws") + "/ws/telemetry"
    dur = 13.0
    pi_args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--ground-link", "direct", "--url", ws, "--drive-port", str(drive_port), "--no-log",
        "--duration-s", str(dur)])
    car = virtual_car.VirtualCar(sim_exe, channel, dyno=False)      # on the floor
    seen: dict = {}

    async def driver():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        seq = 0

        def send(steer, thr, flags):
            nonlocal seq
            sock.sendto(pi.drive_packet_encode(seq, steer, thr, flags), ("127.0.0.1", drive_port))
            seq = (seq + 1) & 0xFF

        await asyncio.sleep(2.5)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.6:                  # ARM with neutral throttle
            send(0, 0, 0x01)
            await asyncio.sleep(0.02)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.5:                  # straight, full throttle
            send(0, 900, 0x01)
            await asyncio.sleep(0.02)
        seen["straight"] = await asyncio.to_thread(_frame, backend)
        seen["rec_straight"] = await asyncio.to_thread(_get, f"{backend}/api/ai/recommendation")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.5:                  # turn
            send(600, 700, 0x01)
            await asyncio.sleep(0.02)
        seen["turning"] = await asyncio.to_thread(_frame, backend)
        seen["rec_turn"] = await asyncio.to_thread(_get, f"{backend}/api/ai/recommendation")
        # the engineer hits E-STOP on the dashboard while the driver still holds ARM + throttle
        t_e = time.monotonic()
        estop_task = asyncio.create_task(asyncio.to_thread(_post, f"{backend}/api/vehicle/estop", {"action": "trigger"}))
        while car.state() != 7 and time.monotonic() - t_e < 3:
            send(0, 700, 0x01)
            await asyncio.sleep(0.02)
        seen["estop_latency_s"] = time.monotonic() - t_e
        await estop_task
        for _ in range(25):
            send(0, 700, 0x01)
            await asyncio.sleep(0.02)
        seen["estopped"] = await asyncio.to_thread(_frame, backend)
        seen["car_after"] = (car.state(), car.mode())
        sock.close()

    async def main():
        await asyncio.gather(pi.run(pi_args), car.run(dur + 0.5), driver())

    asyncio.run(main())

    st = seen["straight"]
    assert st["vcuState"] == "DRIVE" and st["vcu"]["driveMode"] == "track" and st["speed"] > 1.0
    assert st["vcu"]["motorPower"] is True
    assert st["track"]["runActive"] is False                                # no circuit map on the floor
    assert seen["rec_straight"]["source"] == "engineer_station_track_mode_v1"
    tr = seen["turning"]
    assert abs(tr["gForceLat"]) > 0.05 and tr["vcu"]["aeroStraight"] is False
    assert seen["rec_turn"]["straightModeAhead"] is False
    assert seen["estop_latency_s"] < 1.5, seen["estop_latency_s"]
    assert seen["estopped"]["vcuState"] == "EMERGENCY_STOP" and seen["estopped"]["throttle"] == 0
    state, mode = seen["car_after"]
    assert state == 7 and not (mode["flags"] & 0x01)                         # kill switch off
