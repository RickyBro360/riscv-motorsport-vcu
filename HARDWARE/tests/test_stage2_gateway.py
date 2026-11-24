"""
test_stage2_gateway.py — Stage-2 Pi gateway + ground bridge behaviour, run
for real (asyncio, UDP sockets, python-can virtual CAN bus) with a fake VCU.

Covers what the Stage-2 rewrite of raspberry_pi/main.py run() is FOR:
  * Pi heartbeat 0x190 keeps flowing at ~10 Hz even while the backend is
    unreachable (the VCU raises CAN_TIMEOUT after 500 ms of silence)
  * drive_client packets -> CAN 0x118, values intact, bad CRC rejected
  * full-rate CSV log written on the Pi
  * MODE B: LoRa uplink decimated to ~5 Hz, and a CONFIG_WRITE queued on the
    ground bridge reaches the VCU as CAN 0x180 through the downlink slot
  * codec parity between main.py, bridge.py and drive_client.py
Real radios / can0 are NOT exercised (no hardware in CI).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import socket
import struct
import sys
import time
from pathlib import Path

import pytest

HARDWARE_DIR = Path(__file__).resolve().parent.parent
can = pytest.importorskip("can")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pi():
    return _load("s2_pi_main", HARDWARE_DIR / "raspberry_pi" / "main.py")


@pytest.fixture(scope="module")
def bridge():
    return _load("s2_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")


@pytest.fixture(scope="module")
def drive_client():
    return _load("s2_drive_client", HARDWARE_DIR / "Ground Station" / "drive_client.py")


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeVcu:
    """Sends VCU telemetry on a python-can virtual bus, records what the Pi sends."""

    def __init__(self, channel: str):
        self.bus = can.Bus(channel=channel, interface="virtual", receive_own_messages=False)
        self.rx: list[tuple[float, int, bytes]] = []

    async def run(self, duration_s: float):
        t0 = time.monotonic()
        next_tx = t0
        while time.monotonic() - t0 < duration_s:
            now = time.monotonic()
            if now >= next_tx:
                next_tx += 0.02
                self.bus.send(can.Message(arbitration_id=0x100, data=bytes([3, 0, 0, 0, 0]), is_extended_id=False))
                self.bus.send(can.Message(arbitration_id=0x130, data=struct.pack("<Hh", 11850, 3456),
                                          is_extended_id=False))  # 11.85 V, 34.56 A
            while (msg := self.bus.recv(timeout=0)) is not None:
                self.rx.append((time.monotonic() - t0, msg.arbitration_id, bytes(msg.data)))
            await asyncio.sleep(0.003)
        self.bus.shutdown()

    def frames(self, can_id: int):
        return [(t, d) for t, i, d in self.rx if i == can_id]


# ------------------------------------------------------------------ parity
def test_drive_packet_identical_in_client_and_gateway(pi, drive_client):
    for args in [(0, 0, 0, 0), (255, -1000, 1000, 1), (17, 432, -87, 6), (3, 5000, -5000, 2)]:
        a = drive_client.drive_packet_encode(*args)
        assert a == pi.drive_packet_encode(*args) and len(a) == 10
        d = pi.drive_packet_decode(a)
        assert d["steering_permille"] == max(-1000, min(1000, args[1]))
    bad = bytearray(pi.drive_packet_encode(1, 100, 100, 1))
    bad[4] ^= 0x01
    assert pi.drive_packet_decode(bytes(bad)) is None


def test_slip_interop_both_directions(pi, bridge):
    payloads = [bytes([0xC0, 0xDB, 0xDC, 0xDD, 0x00, 0xC0]), b"plain", bytes(range(256))[:75]]
    stream = b"".join(pi.slip_encode(p) for p in payloads)
    dec = bridge.SlipDecoder()
    got = []
    for i in range(0, len(stream), 7):  # arbitrary USB chunking
        got += dec.feed(stream[i:i + 7])
    assert got == payloads
    dec2 = pi.SlipDecoder()
    assert dec2.feed(b"".join(bridge.slip_encode(p) for p in payloads)) == payloads


def test_lora_v3_currents_survive_above_32A(pi, bridge):
    vt = pi.VehicleTelemetry(battery_current_a=34.56, motor_current_a=-40.01, battery_voltage_v=11.85)
    pkt = pi.LoRaPacket(version=pi.LORA_PROTOCOL_VERSION, msg_type=pi.LoRaMsgType.TELEMETRY_COMPACT,
                        sequence=1, timestamp_ms=5, payload=pi.lora_encode_telemetry_compact(vt))
    decoded = bridge.lora_decode(pi.lora_encode(pkt))
    d = bridge.lora_decode_telemetry_compact(decoded.payload)
    assert d["battery_current_a"] == pytest.approx(34.56, abs=0.006)
    # v3 dropped the separate motor-current field: there is no motor current sensor,
    # it was always a copy of the pack current.
    assert d["motor_current_a"] == d["battery_current_a"]
    assert pi.LORA_PROTOCOL_VERSION == bridge.LORA_PROTOCOL_VERSION == 3


def test_lora_v3_compact_round_trip_every_field(pi, bridge):
    vt = pi.VehicleTelemetry(
        vehicle_state_index=5, fault_flags=0x8003, speed_kmh=3.47, steering_angle_deg=-12.3, throttle_pct=62,
        brake_pct=100, aero_mode=1, last_ai_reason_index=6, accel_x_g=0.123, accel_y_g=-0.456, accel_z_g=0.987,
        roll_deg=1.5, pitch_deg=-0.7, yaw_deg=271.4, battery_voltage_v=11.912, battery_current_a=1.87,
        motor_temperature_c=41.3, battery_temperature_c=29.9,
        wing_lf_deg=5.2, wing_rf_deg=30.4, wing_lr_deg=35.0, wing_rr_deg=0.0,
        wing_target_lf_deg=5.0, wing_target_rf_deg=30.0, wing_target_lr_deg=35.2, wing_target_rr_deg=44.8,
        wheel_speed_fl_kmh=3.41, wheel_speed_fr_kmh=3.45, wheel_speed_rl_kmh=3.52, wheel_speed_rr_kmh=3.50,
        track_pos=0.73215, lap=7, track_id=12, track_flags=0x0B | (2 << 5), run_state=2, cal_phase=3,
        current_lap_ms=45670, last_lap_ms=83120, config_report_key=17, config_report_value=-1234567,
        vibration_mg=87.0, gyro_z_dps=-3.4)
    payload = pi.lora_encode_telemetry_compact(vt)
    assert len(payload) == pi.LORA_MAX_PAYLOAD == 64
    for mod in (pi, bridge):
        d = mod.lora_decode_telemetry_compact(payload)
        assert d["state_index"] == 5 and d["fault_flags"] == 0x8003
        assert d["speed_kmh"] == pytest.approx(3.47) and d["steering_angle_deg"] == pytest.approx(-12.3)
        assert d["brake_pct"] == 100.0 and d["aero_mode"] == 1 and d["last_ai_reason_index"] == 6
        assert d["wing_rf_deg"] == pytest.approx(30.4) and d["wing_target_rr_deg"] == pytest.approx(44.8)
        assert d["wheel_speed_rl_kmh"] == pytest.approx(3.52)
        assert d["track_pos"] == pytest.approx(0.73215, abs=2e-5) and d["lap"] == 7 and d["track_id"] == 12
        assert d["track_flags"] == 0x0B | (2 << 5) and d["run_state"] == 2 and d["cal_phase"] == 3
        assert d["current_lap_ms"] == 45670 and d["last_lap_ms"] == 83120
        assert d["config_report_key"] == 17 and d["config_report_value"] == -1234567
        assert d["vibration_mg"] == 87.0 and d["gyro_z_dps"] == pytest.approx(-3.4)
    frame = bridge.compact_to_dashboard_frame(bridge.lora_decode_telemetry_compact(payload), 1000)
    assert frame["track"]["straightMode"] and frame["track"]["configPending"] and frame["track"]["sector"] == 2
    assert frame["wingTarget"]["rearRight"] == pytest.approx(44.8) and frame["runState"] == "bench_laps"
    assert frame["lastAiReason"] == "out_of_range"


def test_config_ack_codec_and_key_tables_match(pi, bridge):
    import re as _re
    assert pi.lora_decode_config_ack(pi.lora_encode_config_ack(17, 1, 700)) == {"key": 17, "status": 1, "value": 700}
    assert bridge.lora_decode_config_ack(pi.lora_encode_config_ack(15, 2, -50)) == {"key": 15, "status": 2, "value": -50}
    assert pi.LoRaMsgType.CONFIG_ACK == bridge.LoRaMsgType.CONFIG_ACK == 9
    assert pi.CONFIG_KEY_NAMES == bridge.CONFIG_KEY_NAMES
    assert pi.CONFIG_STATUS_NAMES == bridge.CONFIG_STATUS_NAMES
    text = (HARDWARE_DIR / "esp32" / "main_updated_corrected__1_" / "vcu_logic.h").read_text()
    fw = {int(m.group(2)) for m in _re.finditer(r"CFG_KEY_(\w+)\s*=\s*(\d+),", text)}
    assert fw == set(pi.CONFIG_KEY_NAMES), "vcu_logic.h ConfigKey and the Python key table disagree"


def test_bridge_downlink_types_decode_on_pi(pi, bridge):
    assert pi.lora_decode_driver_command(bridge.lora_encode_driver_command(-250, 700, 1)) == \
        {"steering_permille": -250, "throttle_permille": 700, "flags": 1}
    assert pi.lora_decode_config_write(bridge.lora_encode_config_write(1, 2)) == {"key": 1, "value": 2}
    assert pi.LoRaMsgType.DRIVER_COMMAND == bridge.LoRaMsgType.DRIVER_COMMAND == 7
    assert pi.LoRaMsgType.CONFIG_WRITE == bridge.LoRaMsgType.CONFIG_WRITE == 8


def test_lora_air_time_budget(pi):
    n = pi.LORA_HEADER_LEN + len(pi.lora_encode_telemetry_compact(pi.VehicleTelemetry())) + pi.LORA_CRC_LEN
    toa = pi.lora_time_on_air_ms(n, 7, 250_000, 5, 8)
    assert 20 < toa < 80  # SF7/BW250: tens of ms
    assert toa * pi.DEFAULT_SETTINGS.lora.uplink_rate_hz / 1000 < 0.5  # leaves room for the downlink


# ------------------------------------------------------------------ MODE A
def test_mode_a_heartbeat_drive_link_and_log_with_backend_down(pi, tmp_path):
    channel = f"virtual_s2a_{time.time_ns()}"
    drive_port = _free_udp_port()
    args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--url", "ws://127.0.0.1:9/ws/telemetry",   # nothing listens: backend DOWN
        "--drive-port", str(drive_port), "--log-dir", str(tmp_path), "--duration-s", "1.6",
    ])
    vcu = FakeVcu(channel)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    async def driver():
        await asyncio.sleep(0.3)
        for seq in range(40):  # 0.8 s at 50 Hz
            tx.sendto(pi.drive_packet_encode(seq, -300, 150, pi.DRIVER_FLAG_ARM), ("127.0.0.1", drive_port))
            await asyncio.sleep(0.02)
        tx.sendto(b"\xd7\x01garbage!!", ("127.0.0.1", drive_port))  # must be rejected

    async def main():
        await asyncio.gather(pi.run(args), vcu.run(1.8), driver())

    asyncio.run(main())
    tx.close()

    hb = vcu.frames(0x190)
    assert 12 <= len(hb) <= 20, f"heartbeat count {len(hb)} over 1.6 s (expected ~16 at 10 Hz)"
    gaps = [b[0] - a[0] for a, b in zip(hb, hb[1:])]
    assert max(gaps) < 0.3, f"heartbeat gap {max(gaps):.2f}s — VCU CAN timeout is 0.5 s"
    assert [d[0] for _, d in hb[:5]] == list(range(5))
    assert all(d[1] & 0x02 == 0 for _, d in hb), "backend_ok bit must be clear while backend is down"

    drv = vcu.frames(0x118)
    assert len(drv) >= 30, f"only {len(drv)} driver commands forwarded"
    seq, steer, thr, flags = struct.unpack("<BhhB", drv[-1][1])
    assert (steer, thr, flags) == (-300, 150, pi.DRIVER_FLAG_ARM)

    logs = list(tmp_path.glob("vehicle_*.csv"))
    assert len(logs) == 1
    rows = list(csv.DictReader(logs[0].open()))
    assert len(rows) >= 15
    assert float(rows[-1]["battery_voltage_v"]) == pytest.approx(11.85, abs=0.01)
    assert float(rows[-1]["battery_current_a"]) == pytest.approx(34.56, abs=0.01)


# ------------------------------------------------------------------ MODE B
def test_mode_b_uplink_decimation_and_config_write_via_lora_substitute(pi, bridge, monkeypatch):
    channel = f"virtual_s2b_{time.time_ns()}"
    ground_port, pi_radio_port = _free_udp_port(), _free_udp_port()
    pi_args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-interface", "socketcan", "--can-channel", channel,
        "--ground-link", "lora", "--lora-backend", "udp", "--ground-host", "127.0.0.1",
        "--ground-port", str(ground_port), "--radio-listen-port", str(pi_radio_port),
        "--drive-port", "0", "--no-log", "--duration-s", "2.6",
    ])
    br_args = argparse.Namespace(radio="udp", serial_port="", serial_baud=115200, listen_host="127.0.0.1",
                                 listen_port=ground_port, pi_host="127.0.0.1", pi_port=pi_radio_port,
                                 url="ws://127.0.0.1:9/ws/telemetry", http_url=None, no_backend=True,
                                 session_id=None, ai_hz=5.0, aero_mode="ai", poll_rate=100.0,
                                 duration_s=2.8, verbose=False)
    uplinks = []
    real = bridge.compact_to_dashboard_frame
    monkeypatch.setattr(bridge, "compact_to_dashboard_frame",
                        lambda d, ts, schema_version=1, **kw: (uplinks.append(d), real(d, ts, schema_version, **kw))[1])
    vcu = FakeVcu(channel)

    async def main():
        await asyncio.gather(bridge.run(br_args), pi.run(pi_args), vcu.run(2.9))

    asyncio.run(main())

    assert 8 <= len(uplinks) <= 15, f"{len(uplinks)} LoRa uplinks in ~2.5 s — expected ~5 Hz, not 20 Hz"
    assert uplinks[-1]["battery_current_a"] == pytest.approx(34.56, abs=0.01)
    cfg = vcu.frames(0x180)
    assert len(cfg) == 3, "aero-mode CONFIG_WRITE is queued 3x on the bridge, one per downlink slot"
    assert all(struct.unpack("<Bi", d[:5]) == (1, 1) for _, d in cfg)
    hb = vcu.frames(0x190)
    assert any(d[1] & 0x01 for _, d in hb), "heartbeat lora_ok bit should set once downlink arrives"


def test_pi_config_rotation_sends_changed_values_first(pi):
    iface = pi.MockCanInterface()
    src = pi.CanTelemetrySource(iface)
    for k, v in [(12, 3000), (13, 3500), (17, 850)]:
        iface.send(pi.CanFrame(can_id=0x182, data=struct.pack("<BiB", k, v, 0)))
    src.next(0.0)
    first = [src.next_config_report() for _ in range(3)]
    assert sorted(first) == [(12, 3000), (13, 3500), (17, 850)]      # each new key once, in arrival order
    assert [src.next_config_report()[0] for _ in range(6)] == [12, 13, 17, 12, 13, 17]   # then plain rotation
    iface.send(pi.CanFrame(can_id=0x181, data=struct.pack("<BBi", 13, 0, 4000)))     # ACK: applied
    src.next(0.1)
    assert src.next_config_report() == (13, 4000), "a just-applied value must go out on the next uplink"
    assert src.take_acks() == [{"key": 13, "status": 0, "value": 4000}]
