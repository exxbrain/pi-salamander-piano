# Troubleshooting

English | [Русский](TROUBLESHOOTING.ru.md)

All of these cases actually happened during development.

## Service status and logs

```bash
systemctl status piano-synth piano-fx
journalctl -u piano-synth -n 30 --no-pager
journalctl -u piano-fx -n 30 --no-pager
aconnect -l          # the "FLUID Synth" client must exist and the keyboard must be connected
vcgencmd get_throttled
free -m
```

The `piano-fx` log should contain lines like `synth: FLUID Synth ...` and `input: <your keyboard> ...`.

## No sound

| Symptom | Cause and fix |
|---|---|
| `aplay -l` says "no soundcards found" | Onboard audio is off: `dtparam=audio=on` in `/boot/config.txt` (DietPi: `dietpi-config` -> Audio Options), then `reboot` |
| `piano-synth` restarts all the time, the restart counter keeps growing | FluidSynth exits at once when its input is closed. Always start it through `/opt/piano-synth/piano-synth-run` (the service does): it keeps a root-only pipe open as FluidSynth's input. Do not use `-s` (server mode): that opens a control port on every network interface |
| The log shows `Out of memory`, the process is `Killed` | The bank does not fit into memory. Use the `lite` or `small` preset |
| `aconnect -l` does not show "FLUID Synth" | `piano-synth` is not running or is still loading the banks (tens of seconds after boot) |
| The keyboard plays but there are no mechanics | `mech.sf2` is not loaded (check `MECH_SF2` in `/etc/piano-synth.conf`) or `piano-fx` is not running |
| There is sound but it is very quiet | Raise `GAIN` (1.0 for stereo, 2.0 for the mono `small`) and the ALSA volume (`alsamixer`, F6, Headphones ~90%) |
| Chords sound harsh or distorted | `GAIN` is too high, lower it |
| The log says `Failed to find an audio format supported by alsa` | The card was opened directly (`hw:...`) and does not accept 16-bit stereo (USB interfaces often offer only 32-bit / 4 channels). Use the `plughw:` form or leave `ALSA_DEVICE=auto` |
| The sound comes from the wrong output after plugging or unplugging a USB card | Run `sudo systemctl restart piano-synth` (the automatic udev restart is untested), or set `ALSA_DEVICE` explicitly |

## Clicks and latency

- First check `vcgencmd get_throttled`. If it is not `0x0`, it is the power supply, not the settings (see the README).
- Clicks: raise `PERIODS` to 3-4 (or `PERIOD_SIZE`).
- Latency: `PERIODS=2` on the onboard output, or better a USB sound card.
- Load: `htop` (if the `fluidsynth` thread sits at 100%, use `lite`/`small`, lower `POLYPHONY`, keep `REVERB=0`).

## Network and SSH during installation

| Symptom | Fix |
|---|---|
| `scp: /usr/lib/sftp-server: No such file or directory` | DietPi has no SFTP server: add `-O` (`scp -O ...`) |
| `No route to host` in the first second, then ping works | Probably the Pi is waking from Wi-Fi power saving (the first packets are lost). Try again; Ethernet is better |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | The system was reinstalled and the key changed: `ssh-keygen -R <IP>` |
| `Permission denied` | Log in as `root` (not the host name); DietPi's default password is `dietpi`, change it |
| The connection drops while copying large files or when a USB device is unplugged | A power sag. Use a short cable and a 5.1 V / 2.5 A supply, use Ethernet, and do not unplug USB devices while the Pi is on |

## The mechanics sound wrong

| You want | Option in `FX_ARGS` |
|---|---|
| Quieter or louder hammer knocks | `--hammer-boost` (dB) |
| Soft playing without / with knocks | larger / smaller `--hammer-exp` |
| More or less liveliness in the knock | `--random` |
| Louder or quieter pedal and resonance | `--pedal-boost`, `--res-boost` |

In the hammer recordings (`rel*.wav`) the sound does not rise instantly: an audible level appears 23-81 ms after the
start of the file. That is a property of the samples themselves and has nothing to do with the system's latency.
The samples are not trimmed in the build.
