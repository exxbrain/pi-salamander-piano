#!/bin/bash
# Build the sound banks from the original Salamander Grand Piano V3 (run on a Mac/Linux, not on the Pi).
#
#   scripts/make-soundfonts.sh [-p PRESET] [-o OUTPUT_DIR] [-s SALAMANDER_DIR]
#
# Presets (size = memory the bank takes on a Pi 3):
#   standard  ~390 MB  8 layers, stereo, 11 s tails. The main one (gain 1.0)
#   soft      ~390 MB  same, but soft notes are quieter (wider dynamics) (gain 1.0)
#   lite      ~250 MB  6 layers, stereo, 9 s tails (gain 1.0)
#   small     ~135 MB  6 layers, MONO, 10 s tails (gain 2.0)
#
# Without -s the archive (~412 MB) is downloaded from freepats.zenvoid.org.
# Needs: python3, ffmpeg (with ffprobe), curl, tar with xz support.
set -euo pipefail

PRESET=standard
OUT=./build
SRC=""
URL='http://freepats.zenvoid.org/Piano/SalamanderGrandPiano/SalamanderGrandPianoV3+20161209_44khz16bit.tar.xz'

while getopts "p:o:s:h" opt; do
  case "$opt" in
    p) PRESET="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    s) SRC="$OPTARG" ;;
    h) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) exit 1 ;;
  esac
done

case "$PRESET" in
  standard) ARGS="--layers 1,2,4,6,8,10,12,15 --tail 11 --gamma 0.85 --peak-lo -17 --peak-hi -1.5 --veltrack 55" ;;
  soft)     ARGS="--layers 1,2,4,6,8,10,12,15 --tail 11 --gamma 0.85 --peak-lo -17 --peak-hi -1.5 --stretch 1.6 --veltrack 55" ;;
  lite)     ARGS="--layers 2,5,8,10,13,15 --tail 9 --gamma 0.7 --peak-lo -12 --peak-hi -1.5 --veltrack 55" ;;
  small)    ARGS="--layers 2,5,8,10,13,15 --tail 10 --mono --gamma 0.6 --peak-lo -9 --peak-hi -1.5 --veltrack 40" ;;
  *) echo "Unknown preset: $PRESET (standard | soft | lite | small)"; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for tool in python3 ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "$tool not found (brew install ffmpeg / apt install ffmpeg)"; exit 1; }
done
mkdir -p "$OUT"

if [ -z "$SRC" ]; then
  ARCHIVE="$OUT/salamander.tar.xz"
  if [ ! -d "$OUT/salamander" ]; then
    echo "==> Downloading Salamander Grand Piano V3 (~412 MB)"
    curl -L -C - --fail -o "$ARCHIVE" "$URL"
    mkdir -p "$OUT/salamander"
    tar -xf "$ARCHIVE" -C "$OUT/salamander"
  fi
  SRC="$OUT/salamander"
fi

SFZ="$(find "$SRC" -name SalamanderGrandPianoV3Retuned.sfz -print -quit)"
[ -n "$SFZ" ] || { echo "SalamanderGrandPianoV3Retuned.sfz not found in $SRC"; exit 1; }
SFZ_DIR="$(dirname "$SFZ")"
[ -d "$SFZ_DIR/44.1khz16bit" ] || { echo "No 44.1khz16bit folder next to the .sfz file"; exit 1; }
echo "==> Source: $SFZ_DIR"

echo "==> Mechanics (mech.sf2, ~36 MB)"
python3 -I "$HERE/tools/build_mech_sf2.py" "$SFZ_DIR" "$OUT/mech.sf2"

echo "==> Piano, preset $PRESET (a few minutes)"
# shellcheck disable=SC2086
python3 -I "$HERE/tools/build_piano_sf2.py" "$SFZ_DIR" "$OUT/piano-$PRESET.sf2" $ARGS --name "Salamander $PRESET"

echo
ls -lh "$OUT"/mech.sf2 "$OUT/piano-$PRESET.sf2"
GAIN=1.0; [ "$PRESET" = small ] && GAIN=2.0
echo
echo "Next (on the Pi, from the pi/ folder of the repository):"
echo "  sudo ./install.sh --piano piano-$PRESET.sf2 --mech mech.sf2 --gain $GAIN"
