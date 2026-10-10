#!/bin/bash
# Installs the piano module on DietPi.
#
#  * First boot, automatically: copy the contents of the installer folder to the SD card's boot partition
#    (Automation_Custom_Script.sh at the top, this script and the .deb in piano-synth/) and set
#    AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0 in dietpi.txt. DietPi runs it once after the first-boot setup.
#  * A DietPi that is already running: copy the piano-synth/ folder to it and run   sudo bash install-piano.sh
#
# Sound banks are not part of the installer (hundreds of MB): put piano.sf2 and mech.sf2 into piano-synth/banks/
# next to this script and they are installed too; otherwise install them later with piano-synth-install-banks.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0"; exit 1; }
LOG="${PIANO_INSTALL_LOG:-/var/log/piano-synth-install.log}"      # overridable for tests
exec > >(tee -a "$LOG") 2>&1
echo "== piano-synth installer $(date -u +%FT%TZ)"
DEB="$(ls "$HERE"/piano-synth_*_all.deb 2>/dev/null | sort | tail -1 || true)"
[ -n "$DEB" ] || { echo "No piano-synth_*_all.deb next to the installer"; exit 1; }

echo "== 1/4 Package and its dependencies (needs the network)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y "$DEB"

echo "== 2/4 Onboard audio (the 3.5 mm jack is off by default on DietPi)"
CONFIG="${PIANO_BOOT_CONFIG:-/boot/firmware/config.txt}"
[ -f "$CONFIG" ] || CONFIG="${PIANO_BOOT_CONFIG:-/boot/config.txt}"
if [ -f "$CONFIG" ]; then
  if grep -q '^dtparam=audio=on' "$CONFIG"; then
    echo "already on in $CONFIG"
  elif grep -q '^dtparam=audio=' "$CONFIG"; then
    sed -i.bak 's/^dtparam=audio=.*/dtparam=audio=on/' "$CONFIG" && rm -f "$CONFIG.bak"; echo "switched on in $CONFIG (takes effect after a reboot)"; REBOOT=1
  else
    echo 'dtparam=audio=on' >> "$CONFIG"; echo "added to $CONFIG (takes effect after a reboot)"; REBOOT=1
  fi
fi

echo "== 3/4 Sound banks"
if [ -f "$HERE/banks/piano.sf2" ] && [ -f "$HERE/banks/mech.sf2" ]; then
  piano-synth-install-banks --piano "$HERE/banks/piano.sf2" --mech "$HERE/banks/mech.sf2"
else
  echo "No banks in $HERE/banks (piano.sf2 + mech.sf2). Build them with scripts/make-soundfonts.sh and install with:"
  echo "  sudo piano-synth-install-banks --piano piano.sf2 --mech mech.sf2"
fi

echo "== 4/4 Done"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Web interface: http://${IP:-<this-device>}:8080/"
echo "Log: $LOG"
if [ "${REBOOT:-0}" = 1 ]; then echo "Reboot once so the onboard audio switches on:  sudo reboot"; fi
