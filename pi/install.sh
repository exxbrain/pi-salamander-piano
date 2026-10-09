#!/bin/bash
# Установка фортепиано на Raspberry Pi (DietPi / Raspberry Pi OS Lite / Debian). Запускать от root.
#
#   sudo ./install.sh --piano /путь/piano-standard.sf2 --mech /путь/mech.sf2 [--device plughw:Headphones] [--gain 1.0]
#
# Повторный запуск безопасен: /etc/piano-synth.conf не перезаписывается,
# меняются только те значения, которые вы указали флагами.
set -euo pipefail

PREFIX=/opt/piano-synth
CONF=/etc/piano-synth.conf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIANO="" MECH="" DEVICE="" GAIN=""

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --piano)  PIANO="$2"; shift 2 ;;
    --mech)   MECH="$2";  shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --gain)   GAIN="$2";  shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Неизвестный параметр: $1"; usage 1 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "Запустите от root: sudo $0 ..."; exit 1; }
if [ -n "$PIANO" ] && [ ! -f "$PIANO" ]; then echo "Нет файла: $PIANO"; exit 1; fi
if [ -n "$MECH" ] && [ ! -f "$MECH" ]; then echo "Нет файла: $MECH"; exit 1; fi
if [ ! -f "$CONF" ] && { [ -z "$PIANO" ] || [ -z "$MECH" ]; }; then
  echo "При первой установке нужны оба параметра: --piano и --mech"; usage 1
fi

echo "==> Пакеты"
apt-get update -qq
apt-get install -y fluidsynth alsa-utils python3-mido python3-rtmidi

echo "==> Файлы в $PREFIX"
install -d "$PREFIX/soundfonts"
install -m 755 "$HERE/piano-fx.py" "$PREFIX/piano-fx.py"
[ -n "$PIANO" ] && install -m 644 "$PIANO" "$PREFIX/soundfonts/piano.sf2"
[ -n "$MECH" ]  && install -m 644 "$MECH"  "$PREFIX/soundfonts/mech.sf2"

echo "==> Конфигурация $CONF"
[ -f "$CONF" ] || install -m 644 "$HERE/piano-synth.conf" "$CONF"
set_conf() {  # KEY VALUE
  if grep -q "^$1=" "$CONF"; then sed -i "s|^$1=.*|$1=$2|" "$CONF"; else echo "$1=$2" >> "$CONF"; fi
}
[ -n "$PIANO" ]  && set_conf PIANO_SF2 "$PREFIX/soundfonts/piano.sf2"
[ -n "$MECH" ]   && set_conf MECH_SF2  "$PREFIX/soundfonts/mech.sf2"
[ -n "$DEVICE" ] && set_conf ALSA_DEVICE "$DEVICE"
[ -n "$GAIN" ]   && set_conf GAIN "$GAIN"

echo "==> Службы systemd"
# старые названия из ручных установок: освободить звуковую карту и MIDI
for old in fluidsynth-live midi-autoconnect; do
  if systemctl list-unit-files "$old.service" 2>/dev/null | grep -q "$old"; then
    systemctl disable --now "$old" 2>/dev/null || true
  fi
done
pkill -x fluidsynth 2>/dev/null || true
install -m 644 "$HERE/piano-synth.service" /etc/systemd/system/piano-synth.service
install -m 644 "$HERE/piano-fx.service"    /etc/systemd/system/piano-fx.service
systemctl daemon-reload
systemctl enable piano-synth piano-fx
systemctl restart piano-synth
sleep 2
systemctl restart piano-fx

echo "==> Проверка"
if grep -q '^ALSA_DEVICE=.*Headphones' "$CONF" && ! aplay -l 2>/dev/null | grep -qi headphones; then
  echo "ВНИМАНИЕ: звуковая карта Headphones не найдена. Включите штатный звук:"
  echo "  dtparam=audio=on в /boot/config.txt (в DietPi: dietpi-config -> Audio Options), затем reboot."
fi
if command -v vcgencmd >/dev/null; then
  thr="$(vcgencmd get_throttled | cut -d= -f2)"
  echo "vcgencmd get_throttled = $thr"
  [ "$thr" = "0x0" ] || echo "ВНИМАНИЕ: питание недостаточно (должно быть 0x0). См. README, раздел «Питание»."
fi
echo
systemctl --no-pager --lines=0 status piano-synth piano-fx || true
echo
echo "Готово. Загрузка банков занимает десятки секунд, потом клавиатура подключится сама."
echo "Журнал:  journalctl -u piano-synth -u piano-fx -f"
