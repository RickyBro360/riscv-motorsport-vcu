"""
camera_relay.py — ESP32-CAM relay, recorder and downloads (Stage 2d).

Why the backend sits in the video path now: the ESP32-CAM sketch serves ONE stream
client at a time (a second viewer gets 503). To show the live feed on the dashboard
AND record it, the backend is that one client. It re-serves the frames to any number
of dashboards (GET /api/camera/stream) and, while recording, writes every frame into
an MJPEG .avi (no re-encoding: the JPEGs from the camera go straight into the file).
On stop, the recording is also converted to .mp4 if an ffmpeg binary is available
(imageio-ffmpeg from requirements.txt, or a system ffmpeg).

A telemetry CSV covering exactly the recording window can be downloaded next to each
video, so video and data line up in the report.

FW 2.2: the ESP32-CAM ANNOUNCES itself. Every 10 s the sketch POSTs its stream URL to
/api/camera/announce, so the relay connects on its own — no IP to copy from the serial
monitor, and a camera that got a new DHCP address is followed automatically. A URL typed
by the operator on the Camera page is "pinned" and is only replaced by an announcement
while it is not delivering frames.

Endpoints (all under /api/camera):
  POST /source {"url": "http://<cam-ip>:81/stream"}   start relaying (url "" = stop)
  POST /announce {"url", "ip"?, "rssi"?, "name"?}     the ESP32-CAM registering itself
  GET  /relay                                          relay + recorder status
  GET  /stream                                         live MJPEG for <img src>
  GET  /snapshot                                       latest frame as .jpg
  POST /record/start {"sessionId"?}  POST /record/stop
  GET  /recordings                                     list
  GET  /recordings/{name}                              download (.avi / .mp4)
  GET  /recordings/{name}/telemetry.csv                telemetry for the same window
  DELETE /recordings/{name}
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.db import get_conn, loads

logger = logging.getLogger("camera.relay")
router = APIRouter(prefix="/api/camera", tags=["camera"])

REC_DIR = Path(os.environ.get("VCU_RECORDINGS_DIR", Path(__file__).resolve().parent.parent / "data" / "recordings"))
BOUNDARY = "frame"
_NAME_RE = re.compile(r"^rec_\d{8}_\d{6}(_\d+)?\.(avi|mp4)$")


# =============================================================================
# MJPEG parsing
# =============================================================================
class MjpegParser:
    """Incremental multipart/x-mixed-replace parser. Uses Content-Length when the
    part header has one (the project's ESP32-CAM sketch sends it), and falls back
    to JPEG SOI/EOI markers otherwise."""

    def __init__(self, max_buf: int = 4 * 1024 * 1024):
        self.buf = bytearray()
        self.max_buf = max_buf

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buf += chunk
        frames: list[bytes] = []
        while True:
            soi = self.buf.find(b"\xff\xd8")
            if soi < 0:
                if len(self.buf) > 4096:
                    del self.buf[:-4096]
                break
            head = bytes(self.buf[max(0, soi - 256):soi])
            m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
            if m:
                n = int(m.group(1))
                if len(self.buf) - soi < n:
                    break
                frames.append(bytes(self.buf[soi:soi + n]))
                del self.buf[:soi + n]
                continue
            eoi = self.buf.find(b"\xff\xd9", soi + 2)
            if eoi < 0:
                break
            frames.append(bytes(self.buf[soi:eoi + 2]))
            del self.buf[:eoi + 2]
        if len(self.buf) > self.max_buf:
            self.buf.clear()
        return frames


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the first SOF marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg
    return None


# =============================================================================
# AVI (MJPG) writer
# =============================================================================
class AviMjpegWriter:
    """Minimal RIFF AVI 1.0 with one MJPG video stream and an idx1 index. Plays in
    VLC, Windows Media Player / Films & TV, and converts cleanly with ffmpeg."""

    def __init__(self, path: Path):
        self.path = path
        self.f = open(path, "wb")
        self.index: list[tuple[int, int]] = []
        self.size: tuple[int, int] | None = None
        self.t0 = time.monotonic()
        self._write_headers(0, 0, 0, 640, 480)
        self.movi_start = self.f.tell()             # offset of 'movi' fourcc
        self.f.write(b"LIST\x00\x00\x00\x00movi")

    def _write_headers(self, frames: int, usec: int, rate_x1000: int, w: int, h: int) -> None:
        avih = struct.pack("<IIIIIIIIIIIIII", usec, 0, 0, 0x10, frames, 0, 1, 0, w, h, 0, 0, 0, 0)
        strh = (b"vids" + b"MJPG" + struct.pack("<IHHIIIIIIIIhhhh", 0, 0, 0, 0, 1000, rate_x1000, 0, frames,
                                                0, 0xFFFFFFFF, 0, 0, 0, w, h))
        strf = struct.pack("<IiiHH4sIiiII", 40, w, h, 1, 24, b"MJPG", w * h * 3, 0, 0, 0, 0)
        strl = b"strl" + b"strh" + struct.pack("<I", len(strh)) + strh + b"strf" + struct.pack("<I", len(strf)) + strf
        hdrl = b"hdrl" + b"avih" + struct.pack("<I", len(avih)) + avih + b"LIST" + struct.pack("<I", len(strl)) + strl
        self.f.seek(0)
        self.f.write(b"RIFF\x00\x00\x00\x00AVI " + b"LIST" + struct.pack("<I", len(hdrl)) + hdrl)

    def write(self, jpeg: bytes) -> None:
        if self.size is None:
            self.size = jpeg_size(jpeg)
        off = self.f.tell() - self.movi_start - 8
        self.f.write(b"00dc" + struct.pack("<I", len(jpeg)) + jpeg)
        if len(jpeg) % 2:
            self.f.write(b"\x00")
        self.index.append((off, len(jpeg)))

    @property
    def frames(self) -> int:
        return len(self.index)

    def close(self) -> dict:
        duration = max(0.001, time.monotonic() - self.t0)
        n = len(self.index)
        fps = n / duration if n else 1.0
        end_movi = self.f.tell()
        self.f.write(b"idx1" + struct.pack("<I", 16 * n))
        for off, ln in self.index:
            self.f.write(b"00dc" + struct.pack("<III", 0x10, off, ln))
        riff_end = self.f.tell()
        w, h = self.size or (640, 480)
        self._write_headers(n, int(1e6 / fps), int(round(fps * 1000)), w, h)
        self.f.seek(self.movi_start + 4)
        self.f.write(struct.pack("<I", end_movi - self.movi_start - 8))
        self.f.seek(4)
        self.f.write(struct.pack("<I", riff_end - 8))
        self.f.close()
        return {"frames": n, "durationS": round(duration, 2), "fps": round(fps, 2), "width": w, "height": h}


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


# =============================================================================
# relay
# =============================================================================
class CameraRelay:
    def __init__(self) -> None:
        self.url: str | None = None
        self.task: asyncio.Task | None = None
        self.latest: bytes | None = None
        self.latest_at: float | None = None
        self.frames = 0
        self.errors = 0
        self.connected = False
        self.last_error: str | None = None
        self._fps_window: list[float] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.recorder: AviMjpegWriter | None = None
        self.rec_meta: dict | None = None
        self._rec_lock = threading.Lock()
        self.origin: str | None = None            # "operator" (typed on the Camera page) | "announce" | "env"
        self.announced: dict | None = None        # the last announcement from the camera

    # --- source ---
    async def announce(self, info: dict) -> bool:
        """The camera told us where it is. Returns True if the relay (re)connected to it."""
        info = dict(info, at=time.time())
        self.announced = info
        url = info["url"]
        if self.url == url:
            return False
        pinned = self.origin == "operator" and self.connected
        if pinned:
            return False
        await self.set_source(url, origin="announce")
        return True

    async def set_source(self, url: str | None, origin: str = "operator") -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        self.url = url or None
        self.origin = origin if self.url else None
        self.connected = False
        if self.url:
            self.task = asyncio.create_task(self._run(self.url))

    async def _run(self, url: str) -> None:
        import httpx
        backoff = 1.0
        while True:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=10.0)) as client:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            raise RuntimeError(f"HTTP {resp.status_code} "
                                               + ("(camera busy: another client — close any browser tab pointing "
                                                  "straight at the ESP32-CAM)" if resp.status_code == 503 else ""))
                        self.connected, self.last_error, backoff = True, None, 1.0
                        parser = MjpegParser()
                        async for chunk in resp.aiter_bytes():
                            for jpeg in parser.feed(chunk):
                                self._publish(jpeg)
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:  # network, camera reboot, busy
                self.connected = False
                self.errors += 1
                self.last_error = str(exc) or exc.__class__.__name__
                logger.warning("camera relay: %s — retry in %.0fs", self.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)

    def _publish(self, jpeg: bytes) -> None:
        t = time.time()
        self.latest, self.latest_at = jpeg, t
        self.frames += 1
        self._fps_window = [x for x in self._fps_window if t - x < 2.0] + [t]
        with self._rec_lock:
            if self.recorder is not None:
                self.recorder.write(jpeg)
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(jpeg)

    def status(self) -> dict:
        ann = None
        if self.announced:
            ann = dict(self.announced, ageS=round(time.time() - self.announced["at"], 1))
        return {"url": self.url, "origin": self.origin, "announced": ann,
                "connected": self.connected, "frames": self.frames, "errors": self.errors,
                "lastError": self.last_error, "fps": round(len(self._fps_window) / 2.0, 1),
                "lastFrameAgeS": round(time.time() - self.latest_at, 2) if self.latest_at else None,
                "viewers": len(self.subscribers),
                "recording": self.rec_meta if self.recorder is not None else None,
                "mp4Available": _ffmpeg() is not None}

    # --- recording ---
    def start_recording(self, session_id: str | None) -> dict:
        with self._rec_lock:
            if self.recorder is not None:
                return self.rec_meta or {}
            REC_DIR.mkdir(parents=True, exist_ok=True)
            base = time.strftime("rec_%Y%m%d_%H%M%S")
            name, k = f"{base}.avi", 1
            while (REC_DIR / name).exists():
                name, k = f"{base}_{k}.avi", k + 1
            self.recorder = AviMjpegWriter(REC_DIR / name)
            self.rec_meta = {"name": name, "startedAt": time.time(), "sessionId": session_id, "source": self.url}
            return dict(self.rec_meta)

    def stop_recording(self) -> dict | None:
        with self._rec_lock:
            if self.recorder is None:
                return None
            info = self.recorder.close()
            meta = dict(self.rec_meta or {}, **info, endedAt=time.time())
            self.recorder, self.rec_meta = None, None
        (REC_DIR / (meta["name"] + ".json")).write_text(json.dumps(meta), encoding="utf-8")
        ff = _ffmpeg()
        if ff and meta["frames"] > 0:
            threading.Thread(target=_to_mp4, args=(ff, REC_DIR / meta["name"]), daemon=True).start()
        return meta


def _to_mp4(ff: str, avi: Path) -> None:
    mp4 = avi.with_suffix(".mp4")
    tmp = avi.with_suffix(".mp4.part")
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(avi), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-f", "mp4", str(tmp)], check=True, timeout=600)
        tmp.replace(mp4)
    except Exception as exc:  # conversion is a convenience; the .avi is the recording
        logger.warning("mp4 conversion failed for %s: %s", avi.name, exc)
        tmp.unlink(missing_ok=True)


relay = CameraRelay()


# =============================================================================
# routes
# =============================================================================
class SourceIn(BaseModel):
    url: str = ""


class RecordIn(BaseModel):
    sessionId: str | None = None


class AnnounceIn(BaseModel):
    url: str
    ip: str | None = None
    rssi: int | None = None
    name: str | None = None
    framesSent: int | None = None
    streaming: bool | None = None


@router.post("/source")
async def set_source(body: SourceIn) -> dict:
    url = body.url.strip()
    if url and not re.match(r"^https?://", url):
        raise HTTPException(status_code=422, detail="url must start with http:// (e.g. http://10.49.218.243:81/stream)")
    await relay.set_source(url or None)
    return relay.status()


@router.post("/announce")
async def announce(body: AnnounceIn) -> dict:
    url = body.url.strip()
    if not re.match(r"^https?://[^/\s]+(:\d+)?/", url):
        raise HTTPException(status_code=422, detail="url must look like http://<ip>:81/stream")
    changed = await relay.announce(body.model_dump() | {"url": url})
    return {"accepted": True, "relayUrl": relay.url, "switched": changed}


@router.get("/relay")
def relay_status() -> dict:
    return relay.status()


@router.get("/snapshot")
def snapshot() -> Response:
    if relay.latest is None:
        raise HTTPException(status_code=404, detail="no frame yet — set the camera source first")
    return Response(content=relay.latest, media_type="image/jpeg",
                    headers={"Content-Disposition": f"inline; filename=snapshot_{time.strftime('%Y%m%d_%H%M%S')}.jpg",
                             "Cache-Control": "no-store"})


@router.get("/stream")
async def stream() -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    relay.subscribers.add(q)
    if relay.latest is not None:
        q.put_nowait(relay.latest)

    async def gen():
        try:
            while True:
                try:
                    jpeg = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    continue
                yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                       .encode() + jpeg + b"\r\n")
        finally:
            relay.subscribers.discard(q)

    return StreamingResponse(gen(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store"})


@router.post("/record/start")
def record_start(body: RecordIn | None = None) -> dict:
    if not relay.url:
        raise HTTPException(status_code=409, detail="set the camera source before recording")
    return relay.start_recording(body.sessionId if body else None)


@router.post("/record/stop")
def record_stop() -> dict:
    meta = relay.stop_recording()
    if meta is None:
        raise HTTPException(status_code=409, detail="not recording")
    return meta


def _recordings() -> list[dict]:
    if not REC_DIR.exists():
        return []
    out = []
    for p in sorted(REC_DIR.glob("rec_*.avi"), reverse=True):
        meta_p = p.with_name(p.name + ".json")
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {"name": p.name}
        mp4 = p.with_suffix(".mp4")
        meta["sizeBytes"] = p.stat().st_size
        meta["files"] = [{"name": p.name, "format": "avi", "sizeBytes": p.stat().st_size}]
        if mp4.exists():
            meta["files"].append({"name": mp4.name, "format": "mp4", "sizeBytes": mp4.stat().st_size})
        meta["inProgress"] = relay.rec_meta is not None and relay.rec_meta.get("name") == p.name
        out.append(meta)
    return out


@router.get("/recordings")
def list_recordings() -> list[dict]:
    return _recordings()


def _safe(name: str) -> Path:
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="no such recording")
    p = REC_DIR / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="no such recording")
    return p


@router.get("/recordings/{name}")
def download(name: str) -> FileResponse:
    p = _safe(name)
    if relay.rec_meta and relay.rec_meta.get("name") == name:
        raise HTTPException(status_code=409, detail="still recording — stop first")
    return FileResponse(p, media_type="video/mp4" if name.endswith(".mp4") else "video/x-msvideo", filename=name)


_CSV_COLUMNS = [
    ("timestamp_ms", lambda f: f.get("timestamp")), ("vcu_state", lambda f: f.get("vcuState")),
    ("speed_kmh", lambda f: f.get("speed")), ("steering_deg", lambda f: f.get("steering")),
    ("throttle_pct", lambda f: f.get("throttle")), ("voltage_v", lambda f: f.get("voltage")),
    ("current_a", lambda f: f.get("current")), ("motor_temp_c", lambda f: f.get("motorTemp")),
    ("battery_temp_c", lambda f: f.get("batteryTemp")),
    ("g_long", lambda f: f.get("gForceLong")), ("g_lat", lambda f: f.get("gForceLat")),
    ("roll_deg", lambda f: (f.get("imu") or {}).get("roll")), ("pitch_deg", lambda f: (f.get("imu") or {}).get("pitch")),
    ("wheel_fl", lambda f: (f.get("wheels") or {}).get("fl")), ("wheel_fr", lambda f: (f.get("wheels") or {}).get("fr")),
    ("wheel_rl", lambda f: (f.get("wheels") or {}).get("rl")), ("wheel_rr", lambda f: (f.get("wheels") or {}).get("rr")),
    ("flap_lf", lambda f: (f.get("wing") or {}).get("frontLeft")),
    ("flap_rf", lambda f: (f.get("wing") or {}).get("frontRight")),
    ("flap_lr", lambda f: (f.get("wing") or {}).get("rearLeft")),
    ("flap_rr", lambda f: (f.get("wing") or {}).get("rearRight")),
    ("flap_target_lf", lambda f: (f.get("wingTarget") or {}).get("frontLeft")),
    ("flap_target_rf", lambda f: (f.get("wingTarget") or {}).get("frontRight")),
    ("track_id", lambda f: (f.get("track") or {}).get("id")), ("lap_pos", lambda f: (f.get("track") or {}).get("pos")),
    ("lap", lambda f: (f.get("track") or {}).get("lap")),
    ("straight_mode", lambda f: (f.get("track") or {}).get("straightMode")),
    ("vibration_mg", lambda f: f.get("vibrationMg")), ("run_state", lambda f: f.get("runState")),
]


@router.get("/recordings/{name}/telemetry.csv")
def recording_telemetry(name: str) -> Response:
    p = _safe(name.replace(".mp4", ".avi"))
    meta_p = p.with_name(p.name + ".json")
    if not meta_p.exists():
        raise HTTPException(status_code=409, detail="recording has no metadata (still in progress?)")
    meta = json.loads(meta_p.read_text())
    with get_conn() as conn:
        rows = conn.execute("SELECT received_at, frame_json FROM telemetry_frames WHERE received_at BETWEEN ? AND ? "
                            "ORDER BY received_at", (meta["startedAt"], meta["endedAt"])).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["video_time_s"] + [c for c, _ in _CSV_COLUMNS])
    for r in rows:
        f = loads(r["frame_json"]) or {}
        w.writerow([round(r["received_at"] - meta["startedAt"], 3)] + [g(f) for _, g in _CSV_COLUMNS])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={p.stem}_telemetry.csv"})


@router.delete("/recordings/{name}")
def delete_recording(name: str) -> dict:
    p = _safe(name)
    if relay.rec_meta and relay.rec_meta.get("name") == name:
        raise HTTPException(status_code=409, detail="still recording")
    for q in (p, p.with_suffix(".mp4"), p.with_name(p.name + ".json")):
        q.unlink(missing_ok=True)
    return {"deleted": name}
