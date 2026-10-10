#!/bin/bash
# Runs ON THE PI as root: restores the piano-synth files saved by upgrade-on-pi.sh (the version before the upgrade).
BACKUP="${PIANO_BACKUP:-/root/piano-synth-backup}"
[ -d "$BACKUP/opt" ] || { echo "No backup in $BACKUP"; exit 1; }

systemctl stop piano-web piano-fx piano-synth 2>/dev/null
# program files and units as they were (the banks were never touched)
cp -a "$BACKUP/opt/." /opt/piano-synth/
[ -e "$BACKUP/piano-synth.conf" ] && cp -a "$BACKUP/piano-synth.conf" /etc/piano-synth.conf
for u in piano-synth piano-fx; do
  [ -e "$BACKUP/units/$u.service" ] && cp -a "$BACKUP/units/$u.service" "/lib/systemd/system/$u.service"
done
# an older version had no web interface: take its pieces away again
if [ ! -e "$BACKUP/units/piano-web.service" ]; then
  systemctl disable piano-web 2>/dev/null
  rm -f /lib/systemd/system/piano-web.service /opt/piano-synth/piano-web.py /opt/piano-synth/pianoconf.py
  rm -rf /opt/piano-synth/web
fi
systemctl daemon-reload
systemctl restart piano-synth
sleep 45
systemctl restart piano-fx
sleep 3
systemctl is-active piano-synth piano-fx
cat "$BACKUP/version.txt" 2>/dev/null
echo "Rolled back the files. (dpkg still lists the newer version; reinstall a package later to make them agree.)"
