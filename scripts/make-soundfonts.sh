#!/bin/bash
# Сборка звуковых банков из оригинального Salamander Grand Piano V3 (запускать на Mac/Linux, не на Pi).
#
#   scripts/make-soundfonts.sh [-p PRESET] [-o ВЫХОДНАЯ_ПАПКА] [-s ПАПКА_С_SALAMANDER]
#
# Пресеты (размер = объём в оперативной памяти Pi 3):
#   standard  ~390 МБ  8 слоёв, стерео, хвосты 11 с. Основной (gain 1.0)
#   soft      ~390 МБ  то же, но тихие ноты тише (шире динамика) (gain 1.0)
#   lite      ~250 МБ  6 слоёв, стерео, хвосты 9 с (gain 1.0)
#   small     ~135 МБ  6 слоёв, МОНО, хвосты 10 с (gain 2.0)
#
# Если -s не указан, архив (~412 МБ) скачивается с freepats.zenvoid.org.
# Нужны: python3, ffmpeg (вместе с ffprobe), curl, tar с поддержкой xz.
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
  *) echo "Неизвестный пресет: $PRESET (standard | soft | lite | small)"; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for tool in python3 ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "Не найден $tool (brew install ffmpeg / apt install ffmpeg)"; exit 1; }
done
mkdir -p "$OUT"

if [ -z "$SRC" ]; then
  ARCHIVE="$OUT/salamander.tar.xz"
  if [ ! -d "$OUT/salamander" ]; then
    echo "==> Скачиваю Salamander Grand Piano V3 (~412 МБ)"
    curl -L -C - --fail -o "$ARCHIVE" "$URL"
    mkdir -p "$OUT/salamander"
    tar -xf "$ARCHIVE" -C "$OUT/salamander"
  fi
  SRC="$OUT/salamander"
fi

SFZ="$(find "$SRC" -name SalamanderGrandPianoV3Retuned.sfz -print -quit)"
[ -n "$SFZ" ] || { echo "В $SRC не найден SalamanderGrandPianoV3Retuned.sfz"; exit 1; }
SFZ_DIR="$(dirname "$SFZ")"
[ -d "$SFZ_DIR/44.1khz16bit" ] || { echo "Рядом с .sfz нет папки 44.1khz16bit"; exit 1; }
echo "==> Исходники: $SFZ_DIR"

echo "==> Механика (mech.sf2, ~36 МБ)"
python3 -I "$HERE/tools/build_mech_sf2.py" "$SFZ_DIR" "$OUT/mech.sf2"

echo "==> Рояль, пресет $PRESET (несколько минут)"
# shellcheck disable=SC2086
python3 -I "$HERE/tools/build_piano_sf2.py" "$SFZ_DIR" "$OUT/piano-$PRESET.sf2" $ARGS --name "Salamander $PRESET"

echo
ls -lh "$OUT"/mech.sf2 "$OUT/piano-$PRESET.sf2"
GAIN=1.0; [ "$PRESET" = small ] && GAIN=2.0
echo
echo "Дальше (на Pi, из папки pi/ репозитория):"
echo "  sudo ./install.sh --piano piano-$PRESET.sf2 --mech mech.sf2 --gain $GAIN"
