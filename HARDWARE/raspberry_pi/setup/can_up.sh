#!/usr/bin/env bash
# =============================================================================
# can_up.sh — bring up every MCP2515 CAN interface at 500 kbit/s and find out
# which one the ESP32-C6 VCU is actually wired to.
#
#   sudo ./can_up.sh            bring up + detect (prints the --can-channel to use)
#   sudo ./can_up.sh --quiet    bring up only (used by the systemd service)
#
# Why this exists:
#   * Linux names can0/can1 in PROBE ORDER, not by overlay name or by the label
#     on the HAT's terminal block. On this Pi, can0 was measured as spi0.1.
#     The VCU may therefore be on can1 even though it is screwed into "CAN0".
#   * `restart-ms 100` makes the MCP2515 recover from BUS-OFF by itself.
#     Without it a bus-off (e.g. from a missing ground) keeps the link dead
#     until someone runs `ip link set down/up`.
#   * txqueuelen is deliberately left at the default (10): a long queue would
#     replay stale driver commands after a bus outage.
# =============================================================================
set -u
BITRATE=500000
QUIET=0; [[ "${1:-}" == "--quiet" ]] && QUIET=1

if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi

IFACES=$(ls /sys/class/net | grep -E '^can[0-9]+$' || true)
if [[ -z "$IFACES" ]]; then
  echo "No canX interfaces. Check /boot/firmware/config.txt ([all] section):"
  echo "  dtparam=spi=on + the two mcp2515 overlays with the CORRECT oscillator, then reboot."
  echo "  dmesg | grep -i mcp251  shows whether the chips were found."
  exit 1
fi

for i in $IFACES; do
  ip link set "$i" down 2>/dev/null
  ip link set "$i" type can bitrate $BITRATE restart-ms 100
  ip link set "$i" up
done
[[ $QUIET -eq 1 ]] && exit 0

echo "== interface -> SPI chip-select (spi0.0 = CE0, spi0.1 = CE1)"
for i in $IFACES; do
  dev=$(basename "$(readlink -f /sys/class/net/$i/device)")
  state=$(ip -details link show "$i" | grep -o 'state [A-Z-]*' | tail -1)
  echo "  $i  -> $dev   $state"
done
echo "   (check the oscillator value in config.txt matches the crystal printed on the HAT;"
echo "    a mismatch gives the wrong bitrate even though loopback tests pass)"

if ! command -v candump >/dev/null; then echo "install can-utils: sudo apt install can-utils"; exit 0; fi

echo "== listening 2 s on each interface for VCU telemetry (IDs 100/110/120/121)"
FOUND=""
for i in $IFACES; do
  n=$(timeout 2 candump -n 200 "$i" 2>/dev/null | grep -cE ' (100|110|120|121) ')
  echo "  $i: $n VCU frames"
  [[ "$n" -gt 0 && -z "$FOUND" ]] && FOUND=$i
done

if [[ -n "$FOUND" ]]; then
  echo
  echo "VCU found on $FOUND  ->  run the gateway with:  --can-channel $FOUND"
else
  echo
  echo "No VCU frames on any interface. In order:"
  echo "  1. Common ground: ESP32-C6 GND to the HAT's G terminal (and Pi/VCU supplies share GND)."
  echo "  2. VCU powered and running? Its console 'status' shows a TWAI line (TEC >= 128 = nobody ACKs)."
  echo "  3. Oscillator in config.txt == crystal on the HAT (8/12/16 MHz)."
  echo "  4. CANH-CANH, CANL-CANL; ~60 ohm across the pair with everything OFF."
  echo "  5. ip -details -statistics link show <canX>   (look at bus-errors / state)"
  exit 2
fi
