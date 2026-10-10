#!/bin/bash
# DietPi runs this file once after the first-boot setup when dietpi.txt has AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0.
# It only hands over to the installer that sits next to it on the boot partition.
for dir in /boot/piano-synth /boot/firmware/piano-synth; do
  if [ -f "$dir/install-piano.sh" ]; then
    exec bash "$dir/install-piano.sh"
  fi
done
echo "piano-synth: install-piano.sh not found on the boot partition" >&2
exit 1
