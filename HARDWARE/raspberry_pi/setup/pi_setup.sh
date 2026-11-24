#!/usr/bin/env bash
# =============================================================================
# pi_setup.sh — one-time Raspberry Pi 4B setup for the gateway (Pi OS Bookworm)
#   sudo ./pi_setup.sh
# Installs packages, disables Wi-Fi power save, checks config.txt. Idempotent.
# =============================================================================
set -u
if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi

echo "== packages"
apt-get update -q
apt-get install -y -q can-utils python3-can python3-websockets python3-serial \
                      python3-spidev python3-rpi.gpio iw
# LoRaRF is not packaged; --no-deps so pip does not try to rebuild spidev/RPi.GPIO
pip3 install --break-system-packages --no-deps LoRaRF

echo "== Wi-Fi power save OFF (it adds 100-300 ms spikes; the VCU drops driver commands after 250 ms)"
iw dev wlan0 set power_save off 2>/dev/null || true
CON=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: '$2=="wlan0"{print $1}' | head -1)
if [[ -n "$CON" ]]; then
  nmcli connection modify "$CON" 802-11-wireless.powersave 2 && echo "  persistent for connection '$CON'"
else
  echo "  WARNING: no active NetworkManager Wi-Fi connection found; power save only off until reboot"
fi
iw dev wlan0 get power_save 2>/dev/null

echo "== /boot/firmware/config.txt check"
CFG=/boot/firmware/config.txt
[[ -f $CFG ]] || CFG=/boot/config.txt
python3 - "$CFG" <<'PY'
import sys, re
lines = open(sys.argv[1]).read().splitlines()
section = "all"; active = []
for ln in lines:
    s = ln.strip()
    m = re.match(r"\[(.+)\]", s)
    if m: section = m.group(1).lower(); continue
    if s and not s.startswith("#") and section in ("all", "pi4"):
        active.append(s)
def has(p): return any(re.search(p, a) for a in active)
ok = True
for pat, msg in [(r"^dtparam=spi=on", "dtparam=spi=on"),
                 (r"^dtoverlay=mcp2515-can0", "dtoverlay=mcp2515-can0,..."),
                 (r"^dtoverlay=mcp2515-can1", "dtoverlay=mcp2515-can1,... (2-channel HAT)"),
                 (r"^dtoverlay=spi1-1cs", "dtoverlay=spi1-1cs (Ra-02 on SPI1)")]:
    print(("  OK      " if has(pat) else "  MISSING ") + msg); ok &= has(pat)
for a in active:
    if a.startswith("dtoverlay=mcp2515"):
        print("  ->", a)
print("  Make sure oscillator= matches the crystal printed on the HAT (8/12/16 MHz).")
print("  Lines under [pi5] or [cm4] are ignored on a Pi 4." )
sys.exit(0 if ok else 1)
PY
echo "== SPI devices"; ls /dev/spidev* 2>/dev/null || echo "  none — SPI overlays not active (reboot after editing config.txt)"
echo "Done. Reboot if config.txt was changed, then: sudo ./can_up.sh"
