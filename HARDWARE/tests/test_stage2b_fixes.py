"""
test_stage2b_fixes.py — regression tests for the Stage-2b bring-up audit fixes.
No hardware: LoRaRF and pyserial are replaced by in-process fakes.

  * SX1278SpiRadio: SPI clock lowered to 1 MHz, STANDBY before every TX,
    signed SNR, default TX power 10 dBm
  * serial radios (Pi + ground): '#' status lines from the ESP32 modem are
    not treated as packets; ground port opened with DTR/RTS de-asserted
  * heartbeat ground-link bit in MODE A (Wi-Fi) follows backend_ok, so the
    VCU no longer raises a permanent LORA_TIMEOUT warning
  * --laptop shorthand and the missing-laptop-IP guard
  * network constants match the real network (Pi 10.49.218.178)
  * VCU firmware keeps VCU_HAVE_ADS1115 / fbWired wiring consistent
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import time
import types
from pathlib import Path

import pytest

HARDWARE_DIR = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pi():
    return _load("s2b_pi_main", HARDWARE_DIR / "raspberry_pi" / "main.py")


@pytest.fixture(scope="module")
def bridge():
    return _load("s2b_bridge", HARDWARE_DIR / "Ground Station" / "bridge.py")


# ---------------------------------------------------------------- fake LoRaRF
class _FakeSX127x:
    REG_FIFO, REG_FIFO_ADDR_PTR, REG_IRQ_FLAGS = 0x00, 0x0D, 0x12
    REG_FIFO_RX_CURRENT_ADDR, REG_RX_NB_BYTES, REG_PKT_SNR_VALUE = 0x10, 0x13, 0x19
    IRQ_RX_DONE, IRQ_CRC_ERR = 0x40, 0x20
    TX_POWER_PA_BOOST, TX_POWER_RFO = 0x80, 0x00
    RX_GAIN_BOOSTED, RX_GAIN_AUTO, HEADER_EXPLICIT, RX_CONTINUOUS = 1, 0, 0, 0xFFFFFF
    instances: list = []

    def __init__(self):
        self.calls: list = []
        self.regs = {self.REG_IRQ_FLAGS: 0, 0x42: 0x12}   # RegVersion of a real SX1278
        self.spi_speed = None
        _FakeSX127x.instances.append(self)

    def setSpi(self, bus, cs, speed=7_800_000):
        self.spi_speed = speed

    def begin(self, bus, cs, reset, irq, txen, rxen):
        self.setSpi(bus, cs)
        return True

    def __getattr__(self, name):  # record every other API call
        def f(*a, **k):
            self.calls.append((name, a))
            return True
        return f

    def readRegister(self, addr):
        self.calls.append(("readRegister", (addr,)))
        return self.regs.get(addr, 0)

    def writeRegister(self, addr, val):
        self.calls.append(("writeRegister", (addr, val)))
        self.regs[addr] = val

    def packetRssi(self):
        return -80.0


@pytest.fixture()
def sx(pi, monkeypatch):
    fake = types.ModuleType("LoRaRF")
    fake.SX127x = _FakeSX127x
    monkeypatch.setitem(sys.modules, "LoRaRF", fake)
    _FakeSX127x.instances.clear()
    radio = pi.SX1278SpiRadio(pi.DEFAULT_SETTINGS.lora)
    return radio, _FakeSX127x.instances[0]


def test_sx1278_wrong_regversion_stops_with_wiring_message(pi, monkeypatch):
    """Stage 2d fix #6: LoRaRF.begin() never reads the chip; the gateway must."""
    fake = types.ModuleType("LoRaRF")

    class Dead(_FakeSX127x):
        def __init__(self):
            super().__init__()
            self.regs[0x42] = 0x00          # MISO floating / module unpowered
    fake.SX127x = Dead
    monkeypatch.setitem(sys.modules, "LoRaRF", fake)
    with pytest.raises(SystemExit, match="RegVersion"):
        pi.SX1278SpiRadio(pi.DEFAULT_SETTINGS.lora)


def test_sx1278_spi_clock_is_lowered(sx, pi):
    _, r = sx
    assert r.spi_speed == pi.SX1278_SPI_HZ == 1_000_000


def test_sx1278_standby_before_tx(sx):
    radio, r = sx
    r.calls.clear()
    radio._tx(b"\x01\x02\x03")
    names = [c[0] for c in r.calls]
    assert "standby" in names and "beginPacket" in names
    assert names.index("standby") < names.index("beginPacket"), names
    assert names[-1] == "request", "must return to RX-continuous after TX"


def test_sx1278_snr_is_signed(sx):
    radio, r = sx
    r.regs.update({r.REG_IRQ_FLAGS: r.IRQ_RX_DONE, r.REG_RX_NB_BYTES: 0, r.REG_PKT_SNR_VALUE: 0xF6})
    radio._rx()
    assert radio.snr_db == pytest.approx(-2.5)  # 0xF6 = -10 -> -2.5 dB
    r.regs.update({r.REG_IRQ_FLAGS: r.IRQ_RX_DONE, r.REG_PKT_SNR_VALUE: 0x28})
    radio._rx()
    assert radio.snr_db == pytest.approx(10.0)


def test_default_tx_power_matches_ground_bridge(pi):
    ino = (HARDWARE_DIR / "Ground Station" / "ground_radio_bridge" / "ground_radio_bridge.ino").read_text()
    m = re.search(r"LORA_POWER_DBM\s*=\s*(\d+)", ino)
    assert m and int(m.group(1)) == pi.DEFAULT_SETTINGS.lora.tx_power_dbm == 10
    fm = re.search(r"LORA_FREQ_MHZ\s*=\s*([\d.]+)f", ino)
    assert float(fm.group(1)) * 1e6 == pi.DEFAULT_SETTINGS.lora.frequency_hz


# ----------------------------------------------------------- serial modems
class _FakeSerial:
    def __init__(self, data: bytes = b""):
        self._data = bytearray(data)
        self.dtr = self.rts = None
        self.opened_with = None
        self.port = self.baudrate = self.timeout = None

    @property
    def in_waiting(self):
        return len(self._data)

    def read(self, n):
        out = bytes(self._data[:n]); del self._data[:n]; return out

    def open(self):
        self.opened_with = (self.dtr, self.rts)

    def reset_input_buffer(self):
        self._data.clear()

    def write(self, b):
        return len(b)


def _slip(pi, payload: bytes) -> bytes:
    return pi.slip_encode(payload)


def test_pi_serial_radio_drops_status_lines(pi):
    radio = pi.SerialSlipRadio.__new__(pi.SerialSlipRadio)
    pi._VehicleRadioBase.__init__(radio)
    pkt = b"\xa5" + bytes(range(20))
    radio._ser = _FakeSerial(b"# ground_radio_bridge ready\r\n" + _slip(pi, pkt) + b"# tx=1 rx=2\r\n" + b"\xc0")
    radio._dec = pi.SlipDecoder()
    assert radio._rx() == [pkt]


def test_ground_serial_radio_opens_without_reset_and_drops_status(bridge, monkeypatch):
    fake_ser = _FakeSerial()
    serial_mod = types.ModuleType("serial")
    serial_mod.Serial = lambda: fake_ser
    serial_mod.SerialException = OSError
    monkeypatch.setitem(sys.modules, "serial", serial_mod)
    monkeypatch.setattr(bridge.time, "sleep", lambda s: None)
    radio = bridge.GroundSerialRadio("COM9", 115200)
    assert fake_ser.opened_with == (False, False), "DTR/RTS must be de-asserted before open"
    pkt = b"\xa5" + bytes(range(12))
    fake_ser._data += b"\xc0# tx=3 rx=4\r\n" + bridge.slip_encode(pkt)
    assert radio._rx() == [pkt]


# --------------------------------------------------------- MODE A heartbeat
def test_mode_a_heartbeat_ground_bit_follows_backend(pi):
    can = pytest.importorskip("can")
    channel = f"virtual_s2b_{time.time_ns()}"
    args = pi.build_arg_parser().parse_args([
        "--source", "can", "--can-channel", channel, "--no-backend", "--no-log",
        "--drive-port", "0", "--duration-s", "0.6"])
    bus = can.Bus(channel=channel, interface="virtual")
    seen = []

    async def listen():
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.8:
            m = bus.recv(timeout=0)
            if m is not None and m.arbitration_id == 0x190:
                seen.append(bytes(m.data))
            await asyncio.sleep(0.005)

    async def main():
        await asyncio.gather(pi.run(args), listen())

    asyncio.run(main())
    bus.shutdown()
    assert seen, "no heartbeat"
    # --no-backend: ground link (Wi-Fi) is down -> bit0 clear. The old code left it
    # clear ALWAYS in MODE A; the fix ties it to backend_ok (set when connected).
    assert all(d[1] & 0x01 == 0 for d in seen)
    src = (HARDWARE_DIR / "raspberry_pi" / "main.py").read_text()
    assert "lora_ok = st.backend_ok" in src


# ---------------------------------------------------------------- CLI/network
def test_laptop_shorthand_and_guard(pi, monkeypatch):
    seen = {}

    async def fake_run(a):
        seen["url"] = a.url
    monkeypatch.setattr(pi, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["main.py", "--laptop", "10.118.112.50"])
    pi.main()
    assert seen["url"] == "ws://10.118.112.50:8000/ws/telemetry"
    if "SET_LAPTOP_IP" in pi.BACKEND_WS_URL:
        monkeypatch.setattr(sys, "argv", ["main.py"])
        with pytest.raises(SystemExit):
            pi.main()


def test_network_constants(pi):
    assert pi.THIS_PI_IP == "10.49.218.178"
    for f in [HARDWARE_DIR / "Ground Station" / "drive_client.py",
              HARDWARE_DIR / "esp32" / "main_updated_corrected__1_" / "main_updated_corrected__1_.ino"]:
        assert "10.118.113.178" not in f.read_text(encoding="utf-8", errors="replace"), f


# ------------------------------------------------------------ firmware config
def test_firmware_ads_fallback_is_wired_through():
    ino = (HARDWARE_DIR / "esp32" / "main_updated_corrected__1_" / "main_updated_corrected__1_.ino").read_text()
    assert re.search(r"^#define VCU_HAVE_ADS1115 [01]$", ino, re.M)
    # without the ADS1115 the INA260 must feed both the voltage AND the freshness stamp
    assert re.search(r"#if !VCU_HAVE_ADS1115\s+s\.battery_v = bv; s\.power_ms = now;", ino)
    # every feedback path is gated by fbWired()
    for pat in [r"if \(!fbWired\(i\)\) continue;", r"fbWired\(i\) \? servoFeedbackToAngle",
                r"if \(!fbWired\(id\)\)"]:
        assert re.search(pat, ino), pat
