#!/usr/bin/env python3
"""Build the DietPi installer bundle (standard library only):

  dist/piano-synth-dietpi-<version>.zip
  dist/piano-synth-dietpi-<version>.tar.gz

Contents: the piano-synth .deb, the installer script, DietPi's Automation_Custom_Script.sh and a README. Copy the
contents of boot-partition/ to a freshly flashed DietPi SD card, or run the installer on a DietPi that is already up.
The version comes from the VERSION file. Sound banks are not included (hundreds of MB).
"""
import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

README = """piano-synth {version}: installer for DietPi
==========================================

What it does: installs the piano module (FluidSynth, the mechanics bridge, the web interface) and starts it on every boot.

A) On a fresh SD card (automatic, at the first boot)
  1. Flash DietPi for your Raspberry Pi as usual.
  2. Copy the CONTENTS of the folder boot-partition/ to the SD card's boot partition (the small FAT partition):
        Automation_Custom_Script.sh      -> the top level of the partition
        piano-synth/                     -> next to it
  3. Optional: put your sound banks into piano-synth/banks/ as piano.sf2 and mech.sf2 (see piano-synth/banks/README.txt).
  4. In dietpi.txt on the same partition set
        AUTO_SETUP_AUTOMATED=1
        AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0
     plus your network settings (the installer needs the internet for the packages: use Ethernet).
     dietpi.txt.example lists these lines.
  5. Insert the card, power on, wait. The log is /var/log/piano-synth-install.log.
  6. Open http://<address of the device>:8080/ from a phone or a computer in the same network.

B) On a DietPi that is already running
  Copy the folder boot-partition/piano-synth/ to the device and run:   sudo bash piano-synth/install-piano.sh

Notes
  * The installer switches the onboard audio on (dtparam=audio=on); a reboot may be needed once.
  * Without banks the services wait; install them later:   sudo piano-synth-install-banks --piano piano.sf2 --mech mech.sf2
  * HONEST STATUS: the .deb and the scripts are tested; the first-boot hook (AUTO_SETUP_CUSTOM_SCRIPT_EXEC) is written from
    DietPi's documentation and has NOT been tried on a real first boot by the author. Check dietpi.com/docs if it does not run.
"""

DIETPI_TXT = """# Lines to set in dietpi.txt on the boot partition (leave the rest of the file as it is)
AUTO_SETUP_AUTOMATED=1
AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0
# Your own values:
# AUTO_SETUP_NET_ETHERNET_ENABLED=1
# AUTO_SETUP_GLOBAL_PASSWORD=<choose a password, do not keep the default>
# AUTO_SETUP_NET_HOSTNAME=piano
"""

BANKS_README = """Put the sound banks here, built with scripts/make-soundfonts.sh:

    piano.sf2    (for example build/piano-standard.sf2, renamed)
    mech.sf2

The installer copies them to /opt/piano-synth/soundfonts and starts the synthesizer.
"""


def main():
    version = open(os.path.join(ROOT, 'VERSION')).read().strip()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist'))
    ap.add_argument('--version', default=version)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    mtime = int(os.environ.get('SOURCE_DATE_EPOCH', time.time()))

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'build-deb.py'), '--version', args.version,
                        '--out', tmp], check=True, stdout=subprocess.DEVNULL)
        deb_name = 'piano-synth_%s_all.deb' % args.version
        with open(os.path.join(tmp, deb_name), 'rb') as f:
            deb = f.read()

    def read(*parts):
        with open(os.path.join(ROOT, *parts), 'rb') as f:
            return f.read()

    top = 'piano-synth-dietpi-%s' % args.version
    files = [  # (path in the bundle, content, mode)
        ('README.txt', README.format(version=args.version).encode(), 0o644),
        ('dietpi.txt.example', DIETPI_TXT.encode(), 0o644),
        ('boot-partition/Automation_Custom_Script.sh', read('dietpi', 'Automation_Custom_Script.sh'), 0o755),
        ('boot-partition/piano-synth/install-piano.sh', read('dietpi', 'install-piano.sh'), 0o755),
        ('boot-partition/piano-synth/%s' % deb_name, deb, 0o644),
        ('boot-partition/piano-synth/banks/README.txt', BANKS_README.encode(), 0o644),
    ]

    tar_path = os.path.join(args.out, top + '.tar.gz')
    with tarfile.open(tar_path, 'w:gz') as tf:
        for path, data, mode in files:
            ti = tarfile.TarInfo('%s/%s' % (top, path))
            ti.size, ti.mode, ti.mtime = len(data), mode, mtime
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            tf.addfile(ti, io.BytesIO(data))

    zip_path = os.path.join(args.out, top + '.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, data, mode in files:
            zi = zipfile.ZipInfo('%s/%s' % (top, path), date_time=time.gmtime(mtime)[:6])
            zi.external_attr = (0o100000 | mode) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, data)

    for p in (zip_path, tar_path):
        print('%s  (%.1f KB)' % (p, os.path.getsize(p) / 1024))


if __name__ == '__main__':
    main()
