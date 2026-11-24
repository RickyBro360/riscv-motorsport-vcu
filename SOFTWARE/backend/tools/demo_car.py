#!/usr/bin/env python3
"""
demo_car.py — a stand-in for the whole car for rehearsing the dashboard (Stage 2d).

NOT THE CAR. It sends frames in exactly the shape the ground bridge produces, marked
"demoFeed": true so the dashboard shows a DEMO FEED badge, and it mimics the VCU's
track run (same map, same flap/steering/duty rules as vcu_logic.h TrackRunner) with
bench-supply electrics. It also plays the VCU's side of the approval loop: it polls
/api/vehicle/commands/next, validates each write with the same bounds, stages
per-lap changes to the next start/finish crossing, and answers with CONFIG_ACKs — so
the AI engineer's approve -> applied flow can be rehearsed end to end.

FW 2.2: it also plays the drive modes and the dashboard's Car control panel the way the
VCU does — boots IDLE in TRACK mode (no run), `driveMode` changes only when stopped,
Start run / Dyno cal are refused unless DYNO + IDLE, Stop ends the run, E-STOP latches
EMERGENCY_STOP until Reset, and the laps target ends a run. It sends the `vcu` object
(as the Pi decodes CAN 0x148). --hw prototype|pcb_rev_b picks what it pretends to be.

Optional: --camera PORT serves a synthetic MJPEG stream (labelled DEMO) so the camera
relay, recording and downloads can be rehearsed without the ESP32-CAM.

    cd SOFTWARE/backend
    python tools/demo_car.py --backend http://127.0.0.1:8000 --track 12 --camera 8081
    # then on the dashboard: Live Mode; Camera page -> http://127.0.0.1:8081/stream
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import random
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import tracks, vcu_config  # noqa: E402

STAGED = {k.name for k in vcu_config.KEYS.values() if k.apply == "staged"}


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return json.loads(r.read())


class DemoVcu:
    def __init__(self, track_id: int, vmax_kmh: float = 16.96, hw: str = "pcb_rev_b", start_running: bool = False):
        self.cfg = dict(vcu_config.DEFAULTS, trackId=track_id, dynoVmaxCkmh=int(vmax_kmh * 100),
                        dynoStartPermille=250, hwProfile=1 if hw == "pcb_rev_b" else 0,
                        wheelPpr=1)
        self.hw = hw
        self.state = "IDLE"
        self.run = "off"
        self.laps_done = 0
        if start_running:            # the old Stage 2d behaviour: already on the rollers, running
            self.cfg["driveMode"] = 0
            self.run = "bench_laps"
        self.staged: dict[str, int] = {}
        self.vmax = vmax_kmh
        self.t = 0.0
        self.pos = 0.0
        self.lap = 0
        self.lap_start = 0.0
        self.last_lap_ms = 0
        self.speed = 0.0
        self.temp = 29.0
        self.wing = {"frontLeft": 0.0, "frontRight": 0.0, "rearLeft": 0.0, "rearRight": 0.0}
        self.target = dict(self.wing)
        self.steer = 0.0
        self.acks: list[dict] = []
        self.report_i = 0
        self.t_cal = 0.0

    def write(self, key: int, value: int) -> None:
        k = vcu_config.KEYS.get(key)
        running = self.run != "off"
        if k is None:
            status = 4
        elif key == 8:                                          # e-stop / reset
            if value == 1:
                self.state, self.run = "EMERGENCY_STOP", "off"
                status = 0
            elif value == 2 and self.state == "EMERGENCY_STOP":
                self.state = "IDLE"
                status = 0
            else:
                status = 3 if value == 2 else 2
        elif key == 6:                                          # run command
            if value == 0:
                self.run = "off"
                status = 0
            elif value not in (1, 2):
                status = 2
            elif self.cfg["driveMode"] != 0 or self.state != "IDLE" or running:
                status = 3
            else:
                self.run = "bench_laps" if value == 1 else "dyno_cal"
                self.laps_done = 0
                self.lap_start, self.t_cal = self.t, self.t
                status = 0
        elif key == 5 and (running or self.state not in ("IDLE",)):
            status = 3
        elif vcu_config.validate(key, value, dict(self.cfg, **self.staged)):
            status = 2
        elif k.apply == "idle_only" and running and k.name in ("trackId", "lapLengthCm"):
            status = 3
        elif k.name in STAGED:
            self.staged[k.name] = value
            status = 1
        else:
            self.cfg[k.name] = value
            status = 0
        self.acks.append({"key": key, "status": status, "value": value})

    def step(self, dt: float) -> dict:
        self.t += dt
        t = tracks.get(self.cfg["trackId"])
        c = self.cfg
        s = tracks.sample(t, self.pos)
        cap, floor = c["dutyCapPermille"] / 1000, c["dutyFloorPermille"] / 1000
        if self.run == "dyno_cal" and self.t - self.t_cal > 8.0:
            self.run = "off"
        moving = self.run in ("bench_laps", "dyno_cal") and self.state != "EMERGENCY_STOP"
        if self.run == "dyno_cal":
            duty = min(cap, 0.15 + (self.t - self.t_cal) * 0.1)
        elif moving:
            duty = floor + (cap - floor) * max(0.0, min(1.0, s["speedKmh"] / t["model"]["vmaxKmh"]))
        else:
            duty = 0.0
        straight = tracks.straight_at(t, self.pos, c["straightOpenDelayM"], c["straightCloseEarlyM"])
        front = (c["straightFrontCdeg"] if straight else c["cornerFrontCdeg"]) / 100
        rear = (c["straightRearCdeg"] if straight else c["cornerRearCdeg"]) / 100
        lat = 0 if straight else max(-10, min(10, s["latG"] * c["asymGainCdegPerG"] / 100))
        self.target = {"frontLeft": front - lat, "frontRight": front + lat, "rearLeft": rear - lat, "rearRight": rear + lat}
        slew = c["wingSlewDps"] * dt
        for k in self.wing:
            lag = 0.85 if k.startswith("rear") else 1.0
            self.wing[k] += max(-slew * lag, min(slew * lag, self.target[k] - self.wing[k]))
        st = max(-30, min(30, s["steerDeg"] * c["steerGainPermille"] / 1000))
        self.steer += max(-300 * dt, min(300 * dt, st - self.steer))
        v_t = duty * self.vmax if duty > 0.25 else 0
        self.speed += (v_t - self.speed) * min(1, dt / (0.35 if v_t > 0 else 0.6))
        if self.speed < 0.02:
            self.speed = 0.0
        if self.run == "bench_laps":
            self.pos += self.speed / 3.6 * dt / (c["lapLengthCm"] / 100)
        if self.pos >= 1:
            self.pos -= 1
            self.lap += 1
            self.laps_done += 1
            self.last_lap_ms = int((self.t - self.lap_start) * 1000)
            self.lap_start = self.t
            if self.staged:
                self.cfg.update(self.staged)
                self.staged = {}
            if c.get("runLaps") and self.laps_done >= c["runLaps"]:
                self.run = "off"
        cur = 0.18 + 1.55 * duty + 0.03 * random.random()
        volt = 12.15 - 0.42 * cur
        self.temp += (cur * cur * 0.012 - (self.temp - 27) * 0.004) * dt
        keys = [k for k in vcu_config.KEYS]
        self.report_i += 1
        rep = {vcu_config.KEYS[k].name: (self.cfg[vcu_config.KEYS[k].name]) for k in keys}
        rep["pending"] = bool(self.staged)
        acks, self.acks = self.acks, []
        vib = 18 + self.speed * 14 + 6 * random.random()
        state = self.state if self.state == "EMERGENCY_STOP" else ("DRIVE" if self.run != "off" else "IDLE")
        rev_b = self.hw == "pcb_rev_b"
        vcu = {"driveMode": "track" if c["driveMode"] else "dyno", "hwProfile": self.hw, "killSwitchFitted": rev_b,
               "motorPower": (self.run != "off" and state != "EMERGENCY_STOP") if rev_b else None,
               "remoteRun": self.run != "off", "tcActive": False, "tcCutPct": 0, "groundLinkOk": True,
               "aeroStraight": straight, "encoderFitted": rev_b, "knob": 0, "runLaps": c.get("runLaps", 0)}
        return {
            "schemaVersion": 1, "timestamp": round(self.t * 1000), "demoFeed": True,
            "speed": self.speed, "voltage": volt, "current": cur, "motorCurrent": cur, "motorTemp": self.temp,
            "batteryTemp": 27.5, "battery": 100, "imu": {"roll": 0.1, "pitch": -0.4, "yaw": 12.0},
            "steering": self.steer, "wing": dict(self.wing),
            # like the VCU: the open-loop front SG90s report their (rate-limited) command as "measured"
            "wingTarget": dict(self.target, frontLeft=self.wing["frontLeft"], frontRight=self.wing["frontRight"]),
            "throttle": duty * 100, "brake": 0, "gForceLat": (random.random() - 0.5) * vib / 1000,
            "gForceLong": (random.random() - 0.5) * vib / 1000, "rpm": self.speed / 3.6 / (math.pi * 0.07) * 60,
            "distance": 0, "wheelAngle": 0, "vcuState": state, "vcu": vcu,
            "derived": {"powerW": volt * cur},
            "wheels": {"fl": 0.0, "fr": 0.0, "rl": self.speed, "rr": self.speed * 0.985},
            "gyroZ": 0.0, "vibrationMg": vib, "odometerM": None, "aeroMode": 1, "lastAiReason": "accepted",
            "track": {"id": t["id"], "pos": self.pos, "lap": self.lap, "currentLapMs": int((self.t - self.lap_start) * 1000),
                      "lastLapMs": self.last_lap_ms, "profileDutyPct": None, "latGModel": None,
                      "runActive": self.run == "bench_laps",
                      "straightMode": straight, "brakingZone": s["braking"], "configPending": bool(self.staged),
                      "benchRun": self.run == "bench_laps", "sector": s["sector"], "stalled": False},
            "runState": self.run, "dynoCal": "hold_cap" if self.run == "dyno_cal" else "done", "vcuConfig": rep,
            "configAcks": acks,
        }


def camera_server(port: int) -> None:
    from PIL import Image, ImageDraw

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=frame")
            self.end_headers()
            k = 0
            try:
                while True:
                    im = Image.new("RGB", (640, 480), (18, 18, 22))
                    d = ImageDraw.Draw(im)
                    x = 320 + 220 * math.sin(k / 15)
                    d.rectangle([x - 40, 200, x + 40, 280], fill=(220, 40, 40))
                    d.text((20, 20), f"DEMO CAMERA - not the ESP32-CAM   frame {k}", fill=(230, 230, 230))
                    b = io.BytesIO()
                    im.save(b, "JPEG", quality=70)
                    j = b.getvalue()
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n" + f"Content-Length: {len(j)}\r\n\r\n".encode() + j + b"\r\n")
                    k += 1
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"demo camera: http://127.0.0.1:{port}/stream")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--track", type=int, default=12)
    ap.add_argument("--hz", type=float, default=5.0, help="frame rate (the LoRa uplink is ~5 Hz)")
    ap.add_argument("--camera", type=int, default=0, help="serve a demo MJPEG stream on this port")
    ap.add_argument("--duration", type=float, default=0.0)
    ap.add_argument("--hw", choices=["pcb_rev_b", "prototype"], default="prototype")
    ap.add_argument("--running", action="store_true", help="start already in DYNO mode, running laps (old behaviour)")
    a = ap.parse_args()
    import websockets
    if a.camera:
        camera_server(a.camera)
    sid = _post(f"{a.backend}/api/sessions", {"mode": "testing", "track": "demo feed"})["id"]
    vcu = DemoVcu(a.track, hw=a.hw, start_running=a.running)
    ws_url = a.backend.replace("http", "ws") + "/ws/telemetry"
    t0 = time.monotonic()
    last_cmd = 0.0
    async with websockets.connect(ws_url) as ws:
        async def drain():
            async for _ in ws:
                pass
        asyncio.create_task(drain())
        print(f"demo car -> {ws_url} session {sid} (Ctrl+C to stop)")
        while a.duration <= 0 or time.monotonic() - t0 < a.duration:
            if time.monotonic() - last_cmd > 0.25:
                last_cmd = time.monotonic()
                try:
                    for c in (await asyncio.to_thread(_get, f"{a.backend}/api/vehicle/commands/next"))["commands"]:
                        vcu.write(int(c["key"]), int(c["value"]))
                except Exception:
                    pass
            frame = vcu.step(1.0 / a.hz)
            await ws.send(json.dumps({"type": "frame", "sessionId": sid, "snapshot": {
                "frame": frame, "ai": None, "faults": [],
                "diagnostics": {"can": "connected", "lora": "connected", "packetLoss": 1.2, "cpu": 0, "memory": 0,
                                "watchdog": "ok", "ping": 0, "packetRate": a.hz, "refreshRate": a.hz, "uptimeS": 0,
                                "temperatureC": 0, "canNodes": [],
                                "loraLink": {"rssi": -61, "snr": 9.0, "spreadingFactor": 7, "bandwidthKhz": 250,
                                             "txPowerDbm": 10}}}}))
            await asyncio.sleep(1.0 / a.hz)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
