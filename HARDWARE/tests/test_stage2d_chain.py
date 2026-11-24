"""
test_stage2d_chain.py — the whole approval loop, every real program, no hardware.

    backend (uvicorn, real HTTP + WebSocket)
      <- bridge.py (MODE B, UDP radio stand-in)  <- LoRa v3 ->  main.py (Pi gateway)
      <- python-can virtual bus ->  fake VCU (answers CONFIG_WRITE with CONFIG_ACK,
                                    streams track position + CONFIG_REPORT)

1. The fake VCU "drives" Monza: the dashboard-facing frame in the backend carries the
   VCU's track position, lap, flap targets and the VCU's config table.
2. An operator change queued in the backend reaches the VCU as CAN 0x180 through the
   LoRa downlink, the VCU ACKs it, and the backend marks it applied.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import socket
import struct
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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    db = tmp_path_factory.mktemp("chain") / "chain.db"
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


def _get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.loads(r.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


class FakeVcu:
    """Answers CONFIG_WRITE like vcu_logic.h ConfigStore (idle: applied) and streams
    the Stage 2d frames at their real periods."""

    def __init__(self, channel: str):
        self.bus = can.Bus(channel=channel, interface="virtual", receive_own_messages=False)
        self.cfg = {1: 1, 2: 12, 3: 6000, 4: 0, 10: 500, 11: 500, 12: 3000, 13: 3500, 14: 200, 15: 0, 16: 0,
                    17: 850, 18: 350, 19: 120, 20: 1000, 21: 200}
        self.writes: list[tuple[int, int]] = []

    def _send(self, cid, data):
        self.bus.send(can.Message(arbitration_id=cid, data=data, is_extended_id=False))

    async def run(self, duration_s: float):
        t0 = time.monotonic()
        tick, keys = 0, list(self.cfg)
        pos = 0.0
        while time.monotonic() - t0 < duration_s:
            tick += 1
            pos = (pos + 0.004) % 1.0
            self._send(0x100, bytes([3, 0, 0, 0, 0]))
            self._send(0x110, struct.pack("<hhhh", 0, 0, 350, 352))
            self._send(0x130, struct.pack("<Hh", 11900, 180))
            self._send(0x151, struct.pack("<hhhh", 500, 500, 500, 500))
            self._send(0x145, struct.pack("<HHBBBb", int(pos * 65535), 2, self.cfg[2], 0x01 | 0x02 | (1 << 5), 70, -20))
            if tick % 5 == 0:
                self._send(0x146, struct.pack("<II", 71230, 12000))
                k = keys[(tick // 5) % len(keys)]
                self._send(0x182, struct.pack("<BiB", k, self.cfg[k], 0))
            while (m := self.bus.recv(timeout=0)) is not None:
                if m.arbitration_id == 0x180:
                    key, value = struct.unpack("<Bi", bytes(m.data)[:5])
                    self.writes.append((key, value))
                    self.cfg[key] = value
                    self._send(0x181, struct.pack("<BBi", key, 0, value))
            await asyncio.sleep(0.02)
        self.bus.shutdown()


def test_full_chain_track_position_and_approved_change(backend):
    pi = _load("chain_pi", HARDWARE_DIR / "raspberry_pi" / "main.py")
    bridge = _load("chain_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")
    channel = f"virtual_chain_{time.time_ns()}"
    ground_port, pi_radio_port = _free_port(socket.SOCK_DGRAM), _free_port(socket.SOCK_DGRAM)
    ws = backend.replace("http", "ws") + "/ws/telemetry"
    pi_args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--ground-link", "lora", "--lora-backend", "udp", "--ground-host", "127.0.0.1",
        "--ground-port", str(ground_port), "--radio-listen-port", str(pi_radio_port),
        "--drive-port", "0", "--no-log", "--duration-s", "7.5"])
    br_args = argparse.Namespace(radio="udp", serial_port="", serial_baud=115200, listen_host="127.0.0.1",
                                 listen_port=ground_port, pi_host="127.0.0.1", pi_port=pi_radio_port,
                                 url=ws, http_url=None, no_backend=False, session_id=None, ai_hz=5.0,
                                 aero_mode=None, poll_rate=100.0, duration_s=7.8, verbose=False)
    vcu = FakeVcu(channel)
    result = {}

    async def operator():
        await asyncio.sleep(2.5)       # let telemetry flow first
        result["queued"] = await asyncio.to_thread(_post, f"{backend}/api/vehicle/config",
                                                   {"name": "cornerRearCdeg", "value": 40})

    async def main():
        await asyncio.gather(bridge.run(br_args), pi.run(pi_args), vcu.run(8.0), operator())

    asyncio.run(main())

    # 1. position + VCU config reached the backend over LoRa v3
    state = _get(f"{backend}/api/vehicle/state")
    frame = state["frame"]
    assert frame["track"]["id"] == 12 and frame["track"]["runActive"] and frame["track"]["straightMode"]
    assert frame["track"]["sector"] == 1 and frame["track"]["lastLapMs"] == 71230
    assert frame["wheels"]["rl"] == pytest.approx(3.5)
    assert frame["vcuConfig"]["lapLengthCm"] == 6000
    # 2. the approved change went down, was ACKed, and is recorded as applied
    assert (13, 4000) in vcu.writes
    cmds = _get(f"{backend}/api/vehicle/commands")
    mine = next(c for c in cmds if c["id"] == result["queued"]["id"])
    assert mine["status"] == "applied", mine
    # 3. the AI engineer sees the running car on the right circuit
    eng = _get(f"{backend}/api/ai/engineer")
    assert eng["running"] and eng["trackCode"] == "ITA"
    assert eng["config"]["cornerRearCdeg"] == 4000
