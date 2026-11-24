import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_DB = Path(__file__).resolve().parent / "test_vcu_camera.db"
os.environ["VCU_DB_PATH"] = str(TEST_DB)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.camera_signal import camera_state  # noqa: E402


def _reset_state():
    camera_state.phone_connected = False
    camera_state.viewer_count = 0


def test_camera_status_starts_disconnected():
    _reset_state()
    client = TestClient(app)
    r = client.get("/api/camera/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "SIMULATED"
    assert body["connected"] is False


def test_phone_connect_flips_status_and_relays_offer_to_viewer():
    _reset_state()
    client = TestClient(app)

    with client.websocket_connect("/ws/camera-signal?role=viewer") as viewer:
        # Viewer joins first, before any phone — told phone-disconnected.
        first = viewer.receive_json()
        assert first["type"] == "phone-disconnected"

        with client.websocket_connect("/ws/camera-signal?role=phone") as phone:
            # Viewer is notified a phone joined.
            joined = viewer.receive_json()
            assert joined["type"] == "phone-connected"

            # REST status reflects it live, without any video ever crossing
            # the backend.
            status = client.get("/api/camera/status").json()
            assert status["connected"] is True
            assert status["mode"] == "LIVE"

            # Phone sends an SDP offer; only the viewer(s) should get it,
            # verbatim, backend does not alter payloads.
            phone.send_json({"type": "offer", "sdp": "v=0 fake-sdp"})
            relayed = viewer.receive_json()
            assert relayed == {"type": "offer", "sdp": "v=0 fake-sdp"}

            # Viewer answers back; only the phone should get it.
            viewer.send_json({"type": "answer", "sdp": "v=0 fake-answer"})
            answer = phone.receive_json()
            assert answer == {"type": "answer", "sdp": "v=0 fake-answer"}

        # Phone socket closed (context exit) -> viewer told it's gone.
        gone = viewer.receive_json()
        assert gone["type"] == "phone-disconnected"

    status = client.get("/api/camera/status").json()
    assert status["connected"] is False
    assert status["mode"] == "SIMULATED"


def test_unrelated_message_types_are_dropped_not_relayed():
    _reset_state()
    client = TestClient(app)
    with client.websocket_connect("/ws/camera-signal?role=viewer") as viewer:
        viewer.receive_json()  # initial phone-disconnected
        with client.websocket_connect("/ws/camera-signal?role=phone") as phone:
            viewer.receive_json()  # phone-connected notice
            phone.send_json({"type": "not-a-real-type", "payload": "junk"})
            # Follow with a real message so we can assert the junk one
            # never arrived at the viewer (no interleaved extra message).
            phone.send_json({"type": "ice-candidate", "candidate": {"foo": "bar"}})
            only = viewer.receive_json()
            assert only == {"type": "ice-candidate", "candidate": {"foo": "bar"}}
