"""
Stage 2d backend tests: circuit maps, digital twin, VCU tunables and the approval
outbox, the position-aware AI engineer, and the ESP32-CAM relay/recorder.
No hardware: telemetry frames are synthesised in the exact shape the Pi gateway /
ground bridge produce, and the camera is a local MJPEG server.
"""
import io
import json
import os
import re
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("VCU_DB_PATH", str(Path(__file__).resolve().parent / "test_stage2d.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import ai_engineer, camera_relay, tracks, twin, vcu_config  # noqa: E402
from app.db import DB_PATH  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    if DB_PATH.exists():
        DB_PATH.unlink()
    monkeypatch.setattr(camera_relay, "REC_DIR", tmp_path / "recordings")
    yield
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _session(client) -> str:
    return client.post("/api/sessions", json={"mode": "testing"}).json()["id"]


def _frame(t_ms, pos=0.0, lap=0, current=1.0, voltage=11.9, motor_temp=30.0, rear=3.0, run=True,
           track_id=12, cfg=None, acks=None, wing=None, target=None, last_lap_ms=0, stalled=False, pending=False):
    w = wing or {"frontLeft": 5, "frontRight": 5, "rearLeft": 5, "rearRight": 5}
    return {"frame": {
        "timestamp": t_ms, "speed": rear, "voltage": voltage, "current": current, "motorTemp": motor_temp,
        "batteryTemp": 28.0, "steering": 0.0, "throttle": 60, "brake": 0, "gForceLat": 0, "gForceLong": 0,
        "imu": {"roll": 0, "pitch": 0, "yaw": 0}, "wing": w, "wingTarget": target or w,
        "wheels": {"fl": 0.0, "fr": 0.0, "rl": rear, "rr": rear}, "vibrationMg": 40.0,
        "vcuState": "IDLE", "runState": "bench_laps" if run else "off", "lastAiReason": "accepted",
        "track": {"id": track_id, "pos": pos, "lap": lap, "runActive": run, "straightMode": False,
                  "currentLapMs": 1000, "lastLapMs": last_lap_ms, "stalled": stalled, "configPending": pending},
        "vcuConfig": dict(cfg or {"trackId": track_id, "lapLengthCm": 6000}, pending=pending),
        "configAcks": acks or [],
    }, "faults": []}


def _post(client, sid, frames):
    r = client.post(f"/api/sessions/{sid}/frames", json={"frames": frames})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ tables agree
def test_config_keys_match_firmware_and_gateway():
    text = (ROOT / "HARDWARE" / "esp32" / "main_updated_corrected__1_" / "vcu_logic.h").read_text()
    fw = {int(m.group(2)): m.group(1) for m in re.finditer(r"CFG_KEY_(\w+)\s*=\s*(\d+),", text)}
    assert set(fw) == set(vcu_config.KEYS)
    pi = (ROOT / "HARDWARE" / "raspberry_pi" / "main.py").read_text()
    for k in vcu_config.KEYS.values():
        assert f'{k.key}: "{k.name}"' in pi, k.name
    # bounds in the firmware ConfigStore::write for the per-lap tunables
    for m in re.finditer(r"case (CFG_KEY_\w+): field = &t\.\w+; lo = (-?\d+); hi = (\d+);", text):
        num = int(re.search(rf"{m.group(1)}\s*=\s*(\d+),", text).group(1))
        assert (vcu_config.KEYS[num].lo, vcu_config.KEYS[num].hi) == (int(m.group(2)), int(m.group(3))), m.group(1)


# ------------------------------------------------------------------ tracks + twin
def test_tracks_endpoint_lists_2026_calendar(client):
    r = client.get("/api/tracks").json()
    codes = [t["code"] for t in r["tracks"]]
    assert len(codes) == 24 and codes[r["defaultTrackId"]] == "ITA"
    for c in ("AUS", "MON", "GBR", "BEL", "ESP", "LVG", "ABU"):
        assert c in codes
    t = client.get("/api/tracks/12").json()
    assert t["name"] == "Monza" and len(t["polyline"]) == len(t["series"]["speedKmh"]) == 600
    assert "vcuTable" not in t
    assert client.get("/api/tracks/99").status_code == 404


def test_twin_reproduces_reference_lap_and_optimiser_never_worse():
    for tid in (5, 12, 9):
        t = tracks.get(tid)
        sim = twin.simulate_config(tid, vcu_config.DEFAULTS)
        assert sim["lapTimeS"] == pytest.approx(t["model"]["lapTimeS"], rel=0.03)
        opt = twin.optimise_aero(tid, vcu_config.DEFAULTS)
        assert opt["bestLapS"] <= opt["baselineLapS"] + 1e-9
    # more flap in corner mode is not free: drag keeps rising while downforce saturates
    lo, _ = twin.aero_coeffs(40, 40)
    hi, _ = twin.aero_coeffs(45, 45)
    _, d40 = twin.aero_coeffs(40, 40)
    _, d45 = twin.aero_coeffs(45, 45)
    assert (hi - lo) / lo < (d45 - d40) / d40


# ------------------------------------------------------------------ outbox + ACK
def test_operator_change_round_trip_with_ack(client):
    sid = _session(client)
    r = client.post("/api/vehicle/config", json={"name": "cornerFrontCdeg", "value": 38})
    assert r.status_code == 200 and r.json()["value"] == 3800 and r.json()["status"] == "queued"
    assert client.post("/api/vehicle/config", json={"name": "cornerFrontCdeg", "value": 50}).status_code == 422
    assert client.post("/api/vehicle/config", json={"name": "dutyFloorPermille", "value": 90}).status_code == 422
    nxt = client.get("/api/vehicle/commands/next").json()["commands"]
    assert nxt == [{"id": nxt[0]["id"], "key": 12, "value": 3800}]
    assert client.get("/api/vehicle/commands/next").json()["commands"] == []   # not resent within 3 s
    # the VCU stages it (a lap is running) ...
    _post(client, sid, [_frame(1000, acks=[{"key": 12, "status": 1, "value": 3800}], pending=True)])
    cmd = client.get("/api/vehicle/commands").json()[0]
    assert cmd["status"] == "staged_next_lap"
    # ... and applies it at the start/finish line (report shows the value, nothing pending)
    cfg = {"trackId": 12, "lapLengthCm": 6000, "cornerFrontCdeg": 3800}
    _post(client, sid, [_frame(1200, cfg=cfg, pending=False)])
    assert client.get("/api/vehicle/commands").json()[0]["status"] == "applied"


def test_lost_ack_is_recovered_from_config_report(client):
    sid = _session(client)
    client.post("/api/vehicle/config", json={"name": "wingSlewDps", "value": 90})
    client.get("/api/vehicle/commands/next")
    _post(client, sid, [_frame(1000, cfg={"trackId": 12, "wingSlewDps": 90})])
    assert client.get("/api/vehicle/commands").json()[0]["status"] == "applied"


def test_rejected_by_vcu_is_reported_not_hidden(client):
    sid = _session(client)
    client.post("/api/vehicle/track", json={"trackId": 5})
    client.get("/api/vehicle/commands/next")
    _post(client, sid, [_frame(1000, acks=[{"key": 2, "status": 3, "value": 5}])])
    assert client.get("/api/vehicle/commands").json()[0]["status"] == "rejected_state"


# ------------------------------------------------------------------ live aero
def test_live_aero_calls_corner_mode_before_the_braking_point():
    t = tracks.get(12)
    z = t["straightZones"][0]
    cfg = vcu_config.DEFAULTS
    # a point inside the first straight zone, just before it ends
    end = z["end"] if z["end"] > z["start"] else z["end"] + 1.0
    lap_len_m = cfg["lapLengthCm"] / 100
    rear_kmh = 3.6
    ahead_frac = (rear_kmh / 3.6) / lap_len_m * ai_engineer.LOOKAHEAD_S
    pos = (end - ahead_frac * 0.5) % 1.0
    assert tracks.straight_at(t, pos)
    f = _frame(0, pos=pos, rear=rear_kmh)["frame"]
    rec = ai_engineer.live_aero_recommendation(f, cfg)
    assert rec["positionAware"] and rec["transitionAhead"] and not rec["straightModeAhead"]
    assert rec["recommendedWing"]["frontLeft"] >= 20        # corner mode already
    assert "corner mode now" in rec["reason"]
    assert ai_engineer.live_aero_recommendation(_frame(0, run=False)["frame"]) is None


def test_recommendation_endpoint_is_position_aware_while_running(client):
    sid = _session(client)
    _post(client, sid, [_frame(1000, pos=0.4)])
    r = client.get("/api/ai/recommendation").json()
    assert r["available"] and r.get("positionAware") and r["trackCode"] == "ITA"
    assert set(r["recommendedWing"]) == {"frontLeft", "frontRight", "rearLeft", "rearRight"}
    _post(client, sid, [_frame(1100, run=False)])
    assert not client.get("/api/ai/recommendation").json().get("positionAware")


# ------------------------------------------------------------------ AI engineer
def _running_window(client, sid, current=2.05, voltage=10.6, temp_start=40.0, temp_rate=0.02, n=120):
    frames = []
    for k in range(n):
        frames.append(_frame(1000 + k * 200, pos=(k * 0.004) % 1.0, current=current + 0.05 * (k % 3),
                             voltage=voltage - 0.02 * (k % 3), motor_temp=temp_start + temp_rate * k * 0.2))
    _post(client, sid, frames)


def test_engineer_infers_and_proposes_then_approval_reaches_the_car(client):
    sid = _session(client)
    _running_window(client, sid)
    e = client.get("/api/ai/engineer").json()
    assert e["available"] and e["running"] and e["trackCode"] == "ITA"
    ids = {i["id"] for i in e["insights"]}
    assert {"position", "supply", "thermal"} <= ids
    supply = next(i for i in e["insights"] if i["id"] == "supply")
    assert supply["severity"] in ("warning", "critical") and supply["metrics"]["p90A"] > 2.0
    kinds = {s["kind"] for s in e["suggestions"]}
    assert "duty_cap_supply" in kinds and "aero_optimise" in kinds
    # polling again does not duplicate open proposals
    e2 = client.get("/api/ai/engineer").json()
    assert len([s for s in e2["suggestions"] if s["kind"] == "duty_cap_supply"]) == 1

    s = next(s for s in e2["suggestions"] if s["kind"] == "duty_cap_supply")
    assert s["status"] == "proposed" and s["body"]["changes"][0]["name"] == "dutyCapPermille"
    new_cap = s["body"]["changes"][0]["wire"]
    assert new_cap < 1000          # FW 2.3.7: default cap 100 %
    a = client.post(f"/api/ai/suggestions/{s['id']}/approve").json()
    assert a["status"] == "approved" and a["commands"][0]["origin"] == f"ai:{s['id']}"
    assert client.post(f"/api/ai/suggestions/{s['id']}/approve").status_code == 409   # only once
    # gateway picks it up, VCU applies it
    nxt = client.get("/api/vehicle/commands/next").json()["commands"]
    assert {"key": 17, "value": new_cap} == {k: nxt[0][k] for k in ("key", "value")}
    _post(client, sid, [_frame(30000, acks=[{"key": 17, "status": 0, "value": new_cap}],
                               cfg={"trackId": 12, "dutyCapPermille": new_cap})])
    e3 = client.get("/api/ai/engineer").json()
    done = next(x for x in e3["suggestions"] if x["id"] == s["id"])
    assert done["status"] == "applied" and done["outcome"]["before"]["p90CurrentA"] > 2.0


def test_reject_and_cooldown(client):
    sid = _session(client)
    _running_window(client, sid)
    e = client.get("/api/ai/engineer").json()
    s = next(s for s in e["suggestions"] if s["kind"] == "aero_optimise")
    assert client.post(f"/api/ai/suggestions/{s['id']}/reject").json()["status"] == "rejected"
    e2 = client.get("/api/ai/engineer").json()
    assert not [x for x in e2["suggestions"] if x["kind"] == "aero_optimise" and x["status"] == "proposed"]


def test_vcu_rejection_marks_suggestion_failed(client):
    sid = _session(client)
    _running_window(client, sid)
    s = next(s for s in client.get("/api/ai/engineer").json()["suggestions"] if s["kind"] == "duty_cap_supply")
    client.post(f"/api/ai/suggestions/{s['id']}/approve")
    client.get("/api/vehicle/commands/next")
    _post(client, sid, [_frame(40000, acks=[{"key": 17, "status": 2, "value": 0}])])
    e = client.get("/api/ai/engineer").json()
    x = next(x for x in e["suggestions"] if x["id"] == s["id"])
    assert x["status"] == "failed" and "rejected range" in x["outcome"]["failure"]


def test_station_settings(client):
    assert client.get("/api/station/settings").json()["powerSource"] == "adapter"
    assert client.post("/api/station/settings", json={"powerSource": "lipo"}).json()["powerSource"] == "lipo"
    assert client.post("/api/station/settings", json={"powerSource": "mains"}).status_code == 422


def test_twin_endpoint(client):
    sid = _session(client)
    _post(client, sid, [_frame(1000, pos=0.3)])
    r = client.get("/api/twin").json()
    assert r["trackCode"] == "ITA" and r["modelled"] and r["forces"]["downforceN"] > 0
    assert len(r["speedKmh"]) == 600


# ------------------------------------------------------------------ camera relay
def _jpeg(w=64, h=48, shade=0) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), (shade % 255, 80, 160)).save(b, "JPEG")
    return b.getvalue()


class _Cam(BaseHTTPRequestHandler):
    frames = [_jpeg(shade=i * 20) for i in range(5)]

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=123456789000000000000987654321")
        self.end_headers()
        try:
            for k in range(400):
                j = self.frames[k % len(self.frames)]
                self.wfile.write(b"\r\n--123456789000000000000987654321\r\n")
                self.wfile.write(f"Content-Type: image/jpeg\r\nContent-Length: {len(j)}\r\n\r\n".encode())
                self.wfile.write(j)
                time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


def test_mjpeg_parser_handles_split_chunks():
    j = _jpeg()
    stream = b"--b\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(j) + j + b"\r\n"
    p = camera_relay.MjpegParser()
    out = []
    for i in range(0, len(stream) * 3, 7):
        out += p.feed((stream * 3)[i:i + 7])
    assert out == [j, j, j]
    assert camera_relay.jpeg_size(j) == (64, 48)


def test_camera_relay_live_snapshot_record_download(client, tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Cam)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/stream"
    try:
        sid = _session(client)
        assert client.post("/api/camera/source", json={"url": "ftp://x"}).status_code == 422
        st = client.post("/api/camera/source", json={"url": url}).json()
        assert st["url"] == url
        t0 = time.time()
        while time.time() - t0 < 5 and client.get("/api/camera/relay").json()["frames"] < 5:
            time.sleep(0.05)
        assert client.get("/api/camera/relay").json()["connected"]
        snap = client.get("/api/camera/snapshot")
        assert snap.status_code == 200 and snap.content[:2] == b"\xff\xd8"

        rec = client.post("/api/camera/record/start", json={"sessionId": sid}).json()
        _post(client, sid, [_frame(1000 + k * 100, pos=k * 0.01) for k in range(5)])
        time.sleep(0.8)
        meta = client.post("/api/camera/record/stop").json()
        assert meta["frames"] >= 10 and meta["width"] == 64 and meta["height"] == 48
        lst = client.get("/api/camera/recordings").json()
        assert lst[0]["name"] == rec["name"]
        avi = client.get(f"/api/camera/recordings/{rec['name']}")
        assert avi.status_code == 200 and avi.content[:4] == b"RIFF" and avi.content[8:12] == b"AVI "
        riff_size = struct.unpack("<I", avi.content[4:8])[0]
        assert riff_size == len(avi.content) - 8
        assert b"idx1" in avi.content and avi.content.count(b"00dc") >= 2 * meta["frames"]
        csv_r = client.get(f"/api/camera/recordings/{rec['name']}/telemetry.csv")
        assert csv_r.status_code == 200 and csv_r.text.splitlines()[0].startswith("video_time_s,")
        assert len(csv_r.text.splitlines()) >= 6
        # the recorded file decodes with ffmpeg when one is available
        ff = camera_relay._ffmpeg()
        if ff:
            import subprocess
            p = tmp_path / "check.avi"
            p.write_bytes(avi.content)
            r = subprocess.run([ff, "-v", "error", "-i", str(p), "-f", "null", "-"], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
        assert client.get("/api/camera/recordings/../../etc/passwd").status_code == 404
        assert client.delete(f"/api/camera/recordings/{rec['name']}").json()["deleted"] == rec["name"]
    finally:
        client.post("/api/camera/source", json={"url": ""})
        srv.shutdown()


# ------------------------------------------------------------------ servo set (2026-10-01)
def test_flap_tracking_uses_rear_feedback_channels(client):
    """Rear MG996R pair has feedback; the front SG90s report their command as 'measured'
    (that is what the VCU does since FW 2.1.1), so only RL/RR get tracking insights."""
    sid = _session(client)
    frames = []
    for i in range(60):
        tgt_val = 5.0 if (i // 15) % 2 == 0 else 30.0
        target = {"frontLeft": tgt_val, "frontRight": tgt_val, "rearLeft": tgt_val, "rearRight": tgt_val}
        lagged = tgt_val - (4.0 if i % 15 < 3 else 1.0)      # rear flaps settle ~1 deg short, slow to respond
        wing = {"frontLeft": tgt_val, "frontRight": tgt_val, "rearLeft": lagged, "rearRight": lagged}
        frames.append(_frame(i * 200, pos=i / 60, wing=wing, target=target))
    _post(client, sid, frames)
    ids = {x["id"] for x in ai_engineer.analyse(sid)["insights"]}
    assert {"flap_RL", "flap_RR"} <= ids
    assert not ids & {"flap_FL", "flap_FR", "flap_LF", "flap_RF"}


def test_firmware_feedback_wiring_matches_pcb():
    ino = (ROOT / "HARDWARE" / "esp32" / "main_updated_corrected__1_" / "main_updated_corrected__1_.ino").read_text()
    assert re.search(r"#define VCU_FB_WIRED_MASK 0x18\b", ino)            # LR + RR
    assert "FB_ADC_PINS[SERVO_COUNT] = {-1, -1, -1, PIN_FB_LR, PIN_FB_RR}" in ino
    prof = (ROOT / "HARDWARE" / "esp32" / "main_updated_corrected__1_" / "vcu_hw_profile.h").read_text()
    assert re.search(r"#define VCU_PIN_FB_LR 0\b", prof) and re.search(r"#define VCU_PIN_FB_RR 1\b", prof)
    assert "PIN_FB_LR = VCU_PIN_FB_LR;" in ino and "PIN_FB_RR = VCU_PIN_FB_RR;" in ino
    assert '"MG995", "SG90", "SG90", "MG996R", "MG996R"' in ino
