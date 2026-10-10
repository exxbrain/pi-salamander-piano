#!/bin/bash
# Runs ON THE PI as root (started by scripts/deploy-to-pi.sh): upgrades the piano-synth package from the .deb in this folder.
# Your /etc/piano-synth.conf is kept. A backup is made first; if the synthesizer or the bridge does not come back, the old
# version is restored automatically (rollback-on-pi.sh does the same by hand).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEB="$(ls "$HERE"/piano-synth_*_all.deb 2>/dev/null | sort -V | tail -1 || true)"
BACKUP="${PIANO_BACKUP:-/root/piano-synth-backup}"

rollback() {
  echo; echo "!! $1 -> rolling back"
  bash "$HERE/rollback-on-pi.sh"
  exit 1
}

[ -n "$DEB" ] || { echo "No piano-synth_*_all.deb next to this script"; exit 1; }

echo "== 1/6 Checking the package with dpkg"
dpkg-deb --info "$DEB" >/dev/null || { echo "dpkg rejects the package"; exit 1; }
dpkg-deb --info "$DEB" | grep -E '^ (Package|Version):' || true
for p in fluidsynth alsa-utils python3-mido python3-rtmidi; do
  dpkg -s "$p" >/dev/null 2>&1 || { echo "Dependency missing: $p (apt-get install -y $p)"; exit 1; }
done

echo "== 2/6 Backup into $BACKUP"
rm -rf "$BACKUP"; mkdir -p "$BACKUP/opt" "$BACKUP/units"
cp -a /opt/piano-synth/. "$BACKUP/opt/" 2>/dev/null || true
rm -rf "$BACKUP/opt/soundfonts"                 # banks are big and the upgrade does not touch them
cp -a /etc/piano-synth.conf "$BACKUP/" 2>/dev/null || true
for u in piano-synth piano-fx piano-web; do
  [ -e "/lib/systemd/system/$u.service" ] && cp -a "/lib/systemd/system/$u.service" "$BACKUP/units/" || true
done
dpkg -s piano-synth 2>/dev/null | grep '^Version' | tee "$BACKUP/version.txt" || echo "(not installed before)" | tee "$BACKUP/version.txt"

echo "== 3/6 Installing (keeping your configuration)"
dpkg -i --force-confold "$DEB" || rollback "dpkg failed"

echo "== 4/6 Starting (the banks load for ~40 s)"
systemctl daemon-reload
systemctl enable piano-synth piano-fx piano-web >/dev/null 2>&1
systemctl restart piano-synth
sleep 45
systemctl is-active --quiet piano-synth || rollback "the synthesizer is not running"
systemctl restart piano-fx
systemctl restart piano-web
sleep 4
systemctl is-active --quiet piano-fx || rollback "the bridge is not running"

echo "== 5/6 Checks"
echo "-- services:"; systemctl is-active piano-synth piano-fx piano-web
echo "-- audio output:"; journalctl -u piano-synth --no-pager | grep 'audio output' | tail -1
echo "-- bridge:"; journalctl -u piano-fx -n 3 --no-pager | tail -3
echo "-- network ports (9800 must be ABSENT, 8080 present):"; ss -ltn | grep -E ':(9800|8080)\b' || echo "(none listed)"
echo "-- control pipe:"; ls -l /run/piano-synth.cmd
echo "-- power:"; vcgencmd get_throttled 2>/dev/null || true

echo "== 6/6 Web interface"
IP="$(hostname -I | awk '{print $1}')"
curl -s -m 5 "http://127.0.0.1:8080/api/ping" && echo
echo "Open  http://$IP:8080/  on your phone or computer."
echo "If anything is wrong: bash $HERE/rollback-on-pi.sh"
