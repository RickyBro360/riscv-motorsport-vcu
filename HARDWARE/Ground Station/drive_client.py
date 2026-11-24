#!/usr/bin/env python3
"""
=============================================================================
drive_client.py — driver input (gamepad / keyboard) -> Raspberry Pi -> VCU
=============================================================================

Sends 10-byte DRIVE packets over UDP (Wi-Fi) at 50 Hz to the Pi gateway
(HARDWARE/raspberry_pi/main.py, --drive-port, default 5020). The Pi forwards
each fresh packet to CAN 0x118 DRIVER_COMMAND. It never replays old ones.

SAFETY MODEL — read before driving
  * The VCU decides. It only enters DRIVE after ARM is held with neutral
    throttle for 300 ms, and it cuts the motor 250 ms after the last packet.
  * ARM is a DEADMAN: it is sent only while you hold it (button/key). Let go
    and the VCU disarms to IDLE.
  * E-STOP latches on the VCU. To reset: release ARM, then press RESET.
  * Close this program, lose Wi-Fi, or crash -> packets stop -> motor off.
  * First runs: wheels OFF the ground. Nothing here is hardware-validated.

Controls
  Gamepad (pygame):   left stick X = steering, right trigger = throttle,
                      left trigger = brake/reverse, hold A (btn 0) = ARM,
                      B (btn 1) = E-STOP, X (btn 2) = E-STOP RESET
  Keyboard (pygame window focused):
                      A/D or ←/→ = steer, W/↑ = throttle, S/↓ = brake,
                      hold SPACE = ARM, E = E-STOP, R = RESET
  --input test        no controls: steering sine sweep, throttle 0, ARM off.
                      Use it to verify the link and the steering servo.

Packet (little-endian): magic 0xD7, version 1, seq u8, steering i16 (‰),
throttle i16 (‰), flags u8 (bit0 ARM, bit1 ESTOP, bit2 ESTOP_RESET),
CRC16-CCITT(0xFFFF) u16 over the first 8 bytes. Identical to
drive_packet_encode() in raspberry_pi/main.py (checked by HARDWARE/tests).

Usage:
    pip install pygame
    python drive_client.py --pi 10.49.218.178                 # gamepad if present, else keyboard
    python drive_client.py --pi 10.49.218.178 --input test    # link check, no motor
    python drive_client.py --pi 127.0.0.1 --max-throttle 0.3   # limit throttle while learning
=============================================================================
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import time

DRIVE_MAGIC = 0xD7
DRIVE_VERSION = 1
FLAG_ARM, FLAG_ESTOP, FLAG_ESTOP_RESET = 0x01, 0x02, 0x04
_FMT = "<BBBhhB"


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def drive_packet_encode(seq: int, steering_permille: int, throttle_permille: int, flags: int) -> bytes:
    body = struct.pack(_FMT, DRIVE_MAGIC, DRIVE_VERSION, seq & 0xFF,
                       max(-1000, min(1000, int(steering_permille))),
                       max(-1000, min(1000, int(throttle_permille))), flags & 0xFF)
    return body + struct.pack("<H", crc16_ccitt(body))


def shape(v: float, deadzone: float = 0.08, expo: float = 0.3) -> float:
    """Deadzone + cubic expo so small stick movements give fine control."""
    if abs(v) < deadzone:
        return 0.0
    s = math.copysign((abs(v) - deadzone) / (1.0 - deadzone), v)
    return (1 - expo) * s + expo * s ** 3


class TestPatternInput:
    def __init__(self):
        self.t0 = time.monotonic()

    def read(self) -> tuple[float, float, int, bool]:
        t = time.monotonic() - self.t0
        return 0.6 * math.sin(2 * math.pi * 0.25 * t), 0.0, 0, False


class PygameInput:
    """Gamepad if one is connected (and --input is not 'keyboard'), else a
    small focused window read as a keyboard."""

    def __init__(self, want: str):
        try:
            import pygame
        except ImportError:
            raise SystemExit("pygame is required for gamepad/keyboard input: pip install pygame "
                             "(or use --input test)")
        self.pg = pygame
        pygame.init()
        pygame.joystick.init()
        self.js = None
        if want in ("auto", "gamepad") and pygame.joystick.get_count() > 0:
            self.js = pygame.joystick.Joystick(0)
            self.js.init()
            print(f"Gamepad: {self.js.get_name()}")
        elif want == "gamepad":
            raise SystemExit("No gamepad found")
        self.screen = pygame.display.set_mode((420, 140))
        pygame.display.set_caption("RC VCU drive client — keep this window focused")
        self.font = pygame.font.SysFont(None, 22)
        self.steer_kb = 0.0
        self.throttle_kb = 0.0

    def read(self) -> tuple[float, float, int, bool]:
        pg = self.pg
        flags, quit_req = 0, False
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                quit_req = True
            elif ev.type == pg.KEYDOWN and ev.key == pg.K_e:
                flags |= FLAG_ESTOP
            elif ev.type == pg.KEYDOWN and ev.key == pg.K_r:
                flags |= FLAG_ESTOP_RESET
            elif ev.type == pg.JOYBUTTONDOWN and ev.button == 1:
                flags |= FLAG_ESTOP
            elif ev.type == pg.JOYBUTTONDOWN and ev.button == 2:
                flags |= FLAG_ESTOP_RESET
        if self.js is not None:
            steer = shape(self.js.get_axis(0))
            n = self.js.get_numaxes()
            # triggers rest at -1 on most SDL mappings -> map to 0..1
            rt = (self.js.get_axis(5) + 1) / 2 if n > 5 else 0.0
            lt = (self.js.get_axis(4) + 1) / 2 if n > 4 else 0.0
            throttle = shape(rt, 0.05, 0.0) - shape(lt, 0.05, 0.0)
            if self.js.get_button(0):
                flags |= FLAG_ARM
        else:
            keys = pg.key.get_pressed()
            target_s = (1.0 if (keys[pg.K_d] or keys[pg.K_RIGHT]) else 0.0) - \
                       (1.0 if (keys[pg.K_a] or keys[pg.K_LEFT]) else 0.0)
            target_t = (1.0 if (keys[pg.K_w] or keys[pg.K_UP]) else 0.0) - \
                       (1.0 if (keys[pg.K_s] or keys[pg.K_DOWN]) else 0.0)
            self.steer_kb += max(-0.1, min(0.1, target_s - self.steer_kb))       # 5 /s ramp at 50 Hz
            self.throttle_kb += max(-0.04, min(0.04, target_t - self.throttle_kb))  # 2 /s ramp
            steer, throttle = self.steer_kb, self.throttle_kb
            if keys[pg.K_SPACE]:
                flags |= FLAG_ARM
            if not pg.key.get_focused():
                self.throttle_kb = 0.0
                steer, throttle, flags = 0.0, 0.0, flags & ~FLAG_ARM  # unfocused = hands off
        return steer, throttle, flags, quit_req

    def show(self, text: str) -> None:
        self.screen.fill((20, 20, 24))
        for i, line in enumerate(text.split("\n")):
            self.screen.blit(self.font.render(line, True, (230, 230, 230)), (10, 10 + 22 * i))
        self.pg.display.flip()


def main() -> None:
    p = argparse.ArgumentParser(description="Driver input -> Pi gateway drive link (UDP)")
    p.add_argument("--pi", required=True, help="Raspberry Pi IP address")
    p.add_argument("--port", type=int, default=5020, help="Pi --drive-port")
    p.add_argument("--input", choices=["auto", "gamepad", "keyboard", "test"], default="auto")
    p.add_argument("--rate", type=float, default=50.0, help="Packets per second (VCU timeout is 250 ms)")
    p.add_argument("--max-throttle", type=float, default=0.5,
                   help="Scale throttle to this fraction (0..1). The VCU adds its own duty cap.")
    p.add_argument("--invert-steering", action="store_true")
    p.add_argument("--duration-s", type=float, default=0.0, help="Stop after N s (0 = until closed)")
    args = p.parse_args()

    if args.rate < 10:
        sys.exit("--rate below 10 Hz would trip the VCU's 250 ms command timeout on any lost packet")
    src = TestPatternInput() if args.input == "test" else PygameInput(args.input)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period, seq = 1.0 / args.rate, 0
    start = next_t = time.monotonic()
    print(f"Sending to {args.pi}:{args.port} at {args.rate:.0f} Hz — Ctrl+C / close window to stop")
    try:
        while args.duration_s <= 0 or time.monotonic() - start < args.duration_s:
            steer, throttle, flags, quit_req = src.read()
            if quit_req:
                break
            if args.invert_steering:
                steer = -steer
            throttle = max(-1.0, min(1.0, throttle)) * max(0.0, min(1.0, args.max_throttle))
            pkt = drive_packet_encode(seq, round(steer * 1000), round(throttle * 1000), flags)
            try:
                sock.sendto(pkt, (args.pi, args.port))
            except OSError as exc:
                print(f"send failed: {exc}")
            seq = (seq + 1) & 0xFF
            if isinstance(src, PygameInput):
                src.show(f"steer {steer:+.2f}  throttle {throttle:+.2f}\n"
                         f"ARM {'HELD' if flags & FLAG_ARM else 'released'}   "
                         f"{'E-STOP sent' if flags & FLAG_ESTOP else ''}\nseq {seq}")
            next_t += period
            time.sleep(max(0.0, next_t - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        # three explicit "hands off" packets; the VCU would time out anyway
        for _ in range(3):
            sock.sendto(drive_packet_encode(seq, 0, 0, 0), (args.pi, args.port))
            seq = (seq + 1) & 0xFF
        print("Stopped — VCU will disarm (ARM released / command timeout).")


if __name__ == "__main__":
    main()
