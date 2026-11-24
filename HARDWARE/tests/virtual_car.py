#!/usr/bin/env python3
"""
virtual_car.py — the REAL VCU logic (vcu_logic.h, via vcu_host_sim.cpp) on a python-can
virtual CAN bus, for end-to-end tests of the Pi gateway, ground bridge, backend and
dashboard without hardware.

python-can's "virtual" bus only connects objects in the SAME process, so this module runs
the Pi gateway (raspberry_pi/main.py run()) in-process next to the virtual car:

    python virtual_car.py --ground-link lora  --ground-host 127.0.0.1    # + bridge.py --radio udp
    python virtual_car.py --ground-link direct --laptop 127.0.0.1         # MODE A, straight to the backend
    (add --hw prototype to build the prototype profile, --floor to put the car on the floor)

Needs g++ (the virtual car is compiled from the firmware's own header on first use).

What it is NOT: a hardware test. It proves that the logic, the protocol and the software
chain behave as designed; sensors, servos, the 12 V adapter and radio range need the car.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path

HARDWARE_DIR = Path(__file__).resolve().parent.parent
SKETCH_DIR = HARDWARE_DIR / "esp32" / "main_updated_corrected__1_"
SIM_SRC = Path(__file__).resolve().parent / "vcu_host_sim.cpp"


def build_sim(out_dir: Path, hw_profile: int = 1) -> Path:
    if shutil.which("g++") is None:
        raise SystemExit("g++ not found: the virtual car is compiled from vcu_logic.h")
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / f"vcu_host_sim_hw{hw_profile}"
    srcs = [SIM_SRC, SKETCH_DIR / "vcu_logic.h", SKETCH_DIR / "track_maps.h"]
    if not exe.exists() or any(s.stat().st_mtime > exe.stat().st_mtime for s in srcs):
        r = subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{SKETCH_DIR}",
                            f"-DSIM_HW_PROFILE={hw_profile}", str(SIM_SRC), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"vcu_host_sim failed to compile:\n{r.stderr}")
    return exe


class VirtualCar:
    """Steps the host-compiled VCU every 10 ms (wall clock) and bridges its frames to a
    python-can virtual bus. `log` keeps every frame the VCU sent (id -> last data)."""

    def __init__(self, exe: Path, channel: str, dyno: bool = True):
        import can
        self.can = can
        self.bus = can.Bus(channel=channel, interface="virtual", receive_own_messages=False)
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.last: dict[int, bytes] = {}
        self.acks: list[tuple[int, int, int]] = []
        self.rx_from_pi: list[int] = []
        self._send_line(f"SET dyno {1 if dyno else 0}")

    def _send_line(self, s: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(s + "\n")
        self.proc.stdin.flush()

    def set(self, name: str, value: float) -> None:
        self._send_line(f"SET {name} {value}")

    def step(self, now_ms: int) -> None:
        while (m := self.bus.recv(timeout=0)) is not None:
            if m.is_extended_id or m.is_remote_frame:
                continue
            self.rx_from_pi.append(m.arbitration_id)
            self._send_line(f"RX {m.arbitration_id:03X} {bytes(m.data).hex().upper()}")
        self._send_line(f"STEP {now_ms}")
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line or line.startswith("END"):
                break
            _, cid, data = line.split()
            fid, payload = int(cid, 16), bytes.fromhex(data)
            self.last[fid] = payload
            if fid == 0x181:
                self.acks.append((payload[0], payload[1], int.from_bytes(payload[2:6], "little", signed=True)))
            self.bus.send(self.can.Message(arbitration_id=fid, data=payload, is_extended_id=False))

    async def run(self, duration_s: float) -> None:
        t0 = time.monotonic()
        next_t = t0
        while time.monotonic() - t0 < duration_s:
            self.step(int((time.monotonic() - t0) * 1000) + 10)
            next_t += 0.01
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))
        self.close()

    def close(self) -> None:
        try:
            self._send_line("QUIT")
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()
        self.bus.shutdown()

    # helpers for tests
    def state(self) -> int | None:
        d = self.last.get(0x100)
        return d[0] if d else None

    def mode(self) -> dict | None:
        d = self.last.get(0x148)
        if not d:
            return None
        return {"driveMode": d[0], "hwProfile": d[1], "flags": d[2], "runLaps": d[3], "tcCutPct": d[4]}


def _load_pi():
    spec = importlib.util.spec_from_file_location("vc_pi_main", HARDWARE_DIR / "raspberry_pi" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vc_pi_main"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hw", choices=["revb", "prototype"], default="prototype")
    ap.add_argument("--speed-model", action="store_true", help="wheel speed from the motor duty, as the car is set up (VCU_CAL_SPEED_FROM_DUTY 1)")
    ap.add_argument("--floor", action="store_true", help="car on the floor (fronts turn, steering corners)")
    ap.add_argument("--duration-s", type=float, default=0.0)
    ap.add_argument("--build-dir", default=str(Path(__file__).resolve().parent / ".build"))
    args, pi_argv = ap.parse_known_args()
    exe = build_sim(Path(args.build_dir), 1 if args.hw == "revb" else 0)
    pi = _load_pi()
    channel = f"virtual_car_{time.time_ns()}"
    pi_args = pi.build_arg_parser().parse_args(
        ["--source", "can", "--can-interface", "socketcan", "--can-channel", channel, "--no-log"] + pi_argv)
    if args.duration_s:
        pi_args.duration_s = args.duration_s
    car = VirtualCar(exe, channel, dyno=not args.floor)
    if args.speed_model:
        car.set("speedmodel", 1)
    print(f"virtual car ({args.hw}, {'floor' if args.floor else 'rollers'}) on {channel}; Pi gateway args: {pi_argv}")

    async def go():
        await asyncio.gather(pi.run(pi_args), car.run(args.duration_s or 1e9))

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
