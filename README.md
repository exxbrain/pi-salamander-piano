# pi-salamander-piano

English | [Русский](README.ru.md)

A digital piano on a Raspberry Pi 3: **Salamander Grand Piano V3** (Yamaha C5) through FluidSynth, plus
**piano mechanics** - hammer and damper noise, string resonance, pedal noise - which the SF2 format cannot
express by itself, so a small bridge program adds them.

```
USB MIDI keyboard ─► piano-fx.py ─► FluidSynth ─► audio output
                        │             ├─ piano.sf2  (the piano, channel 0)
                        └─ mechanics ─►└─ mech.sf2   (channels 1-6)
```

Everything starts by itself when the Pi powers on, and keyboards connect automatically: the bridge rescans the MIDI
ports every 2 seconds. Connecting a keyboard while the Pi is running is supported by design, but only connecting at boot
was tested on a Pi.

## Tested on

- Raspberry Pi 3 Model B (1 GB), DietPi (Debian, FluidSynth 2.4.4), Native Instruments KL Essential 49 mk3 keyboard,
  onboard 3.5 mm output;
- building the banks: macOS (Apple Silicon), FluidSynth 2.6.1, ffmpeg.

Other boards, keyboards and sound cards were not tested. I recommend a USB sound card (see "Audio output and latency"),
but this setup was not measured with one.

## What you need

| | |
|---|---|
| A Pi 3 with DietPi or Raspberry Pi OS Lite | Wi-Fi or Ethernet for installing packages (`apt`) |
| **A 5.1 V / 2.5 A power supply and a short cable** | see "Power supply" - this is not a formality |
| A USB MIDI keyboard | class-compliant, no drivers needed |
| A Mac or Linux machine to build the banks | `python3`, `ffmpeg` (with `ffprobe`), `curl`, `tar` with xz; ~2 GB of free space |

## Quick start

### 1. Build the sound banks (on a Mac/Linux)

The banks themselves are not stored in the repository (hundreds of megabytes, and Salamander's license is CC-BY, see
[NOTICE.md](NOTICE.md)). A script makes them from the original archive:

```bash
scripts/make-soundfonts.sh -p standard -o build      # downloads ~412 MB and builds
```

If you already have Salamander unpacked (a folder with `SalamanderGrandPianoV3Retuned.sfz`), point to it: `-s /path/to/folder`.

Result: `build/mech.sf2` (36 MB) and `build/piano-<preset>.sf2`.

#### Choosing the sample package (`-v`)

Salamander is published by [FreePats](https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html) in several packages. The builders
find the sample files through the paths written in the SFZ, and convert everything to 44.1 kHz / 16 bit, so the
resulting banks have the same size whichever package you take; the source only changes how faithful the conversion is
and how much you download.

```bash
scripts/make-soundfonts.sh -p standard -v 48k24 -o build     # take the 48 kHz / 24 bit WAV package
```

| `-v` | Package | Download | Status |
|---|---|---|---|
| `44k16` (default) | SFZ WAV, 44.1 kHz 16 bit | 412 MB | built and used on a real Pi |
| `48k24` | SFZ WAV, 48 kHz 24 bit | 1.26 GB | checked on a copy of the `44k16` set converted to this format (the rendered audio matches the `44k16` build, difference 67 dB below the signal); the real 1.26 GB download was **not** tried |
| `48k24-flac` | SFZ FLAC, 48 kHz 24 bit (2020-06-02 package) | 742 MB | **experimental**: the code path (FLAC, other folders) is tested on a synthetic set, but the real archive's layout and SFZ file name were not inspected; if the SFZ is not found, pass `-n NAME` |

For any other Salamander SFZ package use `-u URL` (and `-n NAME` if its SFZ file is named differently). The layer
table in the builder assumes the 16 velocity layers of the V3 set. FreePats also offers a ready-made SF2 of Salamander;
it is not used here because it has no mechanics presets and no layer reduction.

| Preset | Memory on the Pi | What it is | `GAIN` | Tested on a Pi 3 |
|---|---|---|---|---|
| `standard` | ~390 MB | 8 velocity layers, stereo, 11 s tails | 1.0 | yes, works |
| `soft` | ~390 MB | same, but soft notes are noticeably quieter (wider dynamics) | 1.0 | no, only on the Mac |
| `lite` | ~250 MB | 6 layers, stereo, 9 s tails | 1.0 | a close variant (CPU load ~30%) |
| `small` | ~135 MB | 6 layers, **mono**, 10 s tails | 2.0 | no, only on the Mac |

The whole file is read into RAM, and a Pi 3 has 1 GB. I did not measure the free memory after loading `standard`
precisely; check yourself (`free -m`, the `available` column). If the Pi runs out of memory or CPU, use `lite` or `small`.

### 2. Copy to the Pi

Preferably **over Ethernet** (large files are slow over Wi-Fi, and with a weak power supply the connection drops).
`scp` to DietPi needs the `-O` flag (there is no SFTP server):

```bash
scp -O build/piano-standard.sf2 build/mech.sf2 root@<Pi-IP>:/root/
scp -O -r pi root@<Pi-IP>:/root/
```

If the copy keeps breaking, `rsync` can resume (on the Pi: `apt install rsync`):
`rsync -avP --partial -e ssh build/piano-standard.sf2 root@<Pi-IP>:/root/`.

### 3. Install (on the Pi)

```bash
ssh root@<Pi-IP>
cd /root/pi
./install.sh --piano /root/piano-standard.sf2 --mech /root/mech.sf2 --gain 1.0
```

The audio output is chosen automatically: a USB sound card if one is plugged in, otherwise the onboard jack (to force one, add
`--device plughw:Headphones` or `--device plughw:<card id from aplay -l>`). The script installs the packages
(`fluidsynth`, `alsa-utils`, `python3-mido`, `python3-rtmidi`), puts the files in `/opt/piano-synth`,
writes the settings to `/etc/piano-synth.conf` and enables two services: `piano-synth` (FluidSynth) and `piano-fx` (the bridge).
Loading the banks takes tens of seconds. Running it again is safe: the configuration is not overwritten.

## Installing as a .deb package (instead of `install.sh`)

The same services, configuration and dependencies, as an ordinary Debian/DietPi package:

```bash
python3 scripts/build-deb.py --version 1.0.0                                # -> dist/piano-synth_1.0.0_all.deb
python3 scripts/build-deb.py --version 1.0.0 --banks build/piano-standard.sf2 build/mech.sf2
                                                                            # + dist/piano-synth-soundfonts_1.0.0_all.deb
scp -O dist/*.deb root@<Pi-IP>:/root/
ssh root@<Pi-IP> "apt install -y ./piano-synth_1.0.0_all.deb ./piano-synth-soundfonts_1.0.0_all.deb"
```

- `piano-synth` - the program, two services, `/etc/piano-synth.conf` (a conffile: your edits survive upgrades),
  dependencies (`fluidsynth`, `alsa-utils`, `python3-mido`, `python3-rtmidi`) and the `piano-synth-install-banks` command.
  If services of a manual setup (`fluidsynth-live`, `midi-autoconnect`) are left on the Pi, the package disables them.
- `piano-synth-soundfonts` - the banks (`piano.sf2`, `mech.sf2`) in `/opt/piano-synth/soundfonts`. They are hundreds of
  megabytes; instead you can copy the files and run
  `sudo piano-synth-install-banks --piano piano.sf2 --mech mech.sf2 [--device plughw:U24] [--gain 1.0]`.
- Removal: `apt remove piano-synth` (the services stop and are disabled); `apt purge` also deletes the configuration.

The packages are built without `dpkg-deb` (pure Python), so building works on a Mac. Their structure is covered by tests
(`tests/test_deb.py`), but, at the time of writing, installing on a real Pi and checking with `dpkg` itself have not been
done. You can first inspect a package on the Pi:
`dpkg-deb --info piano-synth_1.0.0_all.deb && dpkg-deb --contents piano-synth_1.0.0_all.deb`.

## Power supply (read this)

The Pi 3 is very sensitive to its power. Check:

```bash
vcgencmd get_throttled     # must be 0x0
```

Any other value (for example `0x50005`) means the voltage sagged. Effects we saw: clicks in the sound, Wi-Fi dropping
and SSH breaking while copying large files, reboots when a USB device was unplugged. The cure is a **5.1 V / 2.5 A supply
and a short, thick cable** (up to 1 m, 20 AWG wires or thicker, no switch). A long thin cord is the most common cause.

## Audio output and latency

- **Onboard 3.5 mm:** enable `dtparam=audio=on` in `/boot/config.txt` (DietPi: `dietpi-config` -> Audio Options), device
  `plughw:Headphones`. The bcm2835 driver enlarges the buffer period to 444 samples by itself, so the latency is about
  30 ms (with `PERIODS=3`); `PERIODS=2` gives ~20 ms but may click. The output is somewhat noisy.
- **USB sound card / audio interface:** the best option. Just plug it in: `ALSA_DEVICE=auto` (the default) prefers a USB card and
  uses the smaller buffer `USB_PERIOD_SIZE=64` x `USB_PERIODS=3` (~12 ms). The device is opened through ALSA's `plughw` layer, which
  matters: many interfaces accept only 24/32-bit audio or 4 channels, and opening them directly (`hw:`) fails with
  `Failed to find an audio format supported by alsa`.
  Tested: a Zoom U-24 (it reports `S32_LE`, 4 channels, 44.1-96 kHz) on a Pi 3 with `64 x 3`: no clicks by ear, latency not
  measured. The card is picked when the service starts (it waits `USB_WAIT` seconds for a USB card at boot). Re-picking when you
  plug or unplug a card is done by a udev rule that was written from the documentation and **not tested on hardware**;
  if it does not work, run `sudo systemctl restart piano-synth` after changing the interface.

## Configuration

Everything is in `/etc/piano-synth.conf`. After editing: `sudo systemctl restart piano-synth piano-fx`.

| Setting | What it does |
|---|---|
| `GAIN` | overall volume (0.2 is FluidSynth's own default; these banks want 1.0, the mono preset `small` wants 2.0) |
| `POLYPHONY` | number of voices; stereo uses two voices per note, and the mechanics add more |
| `ALSA_DEVICE` | `auto` (USB card if present, otherwise onboard) or an explicit device such as `plughw:U24` |
| `PERIOD_SIZE`, `PERIODS` / `USB_PERIOD_SIZE`, `USB_PERIODS`, `USB_WAIT` | buffer for the onboard output / for a USB card, and how long to wait for it at boot |
| `REVERB` | 1 enables FluidSynth's reverb (higher CPU load) |
| `FX_ARGS` | mechanics options, see below |

### Tuning the mechanics (`FX_ARGS`)

| Option | Default | Meaning |
|---|---|---|
| `--hammer-boost DB` | 0 | hammer-noise level; 0 is as in the SFZ (very quiet), +6 is clearly louder |
| `--hammer-exp N` | 0.8 | dependence of the knock on touch: amplitude ~ (velocity/127)^N. Larger = soft playing is more silent. 0.6-1.0 is gentle, 2.0 is steep |
| `--random R` | 1.0 | randomness of knock and pedal: 0 off, 1 normal, 2 strong. Level varies, and for the hammer the sample of a neighbouring key is picked |
| `--res-boost DB` | 0 | string-resonance level |
| `--pedal-boost DB` | 0 | pedal-noise level |

## How it works

In the SFZ original the mechanics are triggered by `trigger=release` (a key is released) and `on_locc64` (the pedal) rules.
SF2 has no such mechanism, so:

- `tools/build_piano_sf2.py` builds the **piano** without mechanics. Of the 16 velocity layers it keeps a few (chosen by hand,
  spread over the velocity scale), shortens the tails with a smooth fade-out, matches the layer levels and adds a mild
  dependence of volume on velocity. The sensitivity curve (`--gamma`) shifts the ranges so that a soft touch lands in a louder layer;
- `tools/build_mech_sf2.py` builds the **mechanics** as six separate presets (hammer, resonance L/S/V3, pedal down/up).
  A preset's volume is set through the note velocity: attenuation in dB = 60 * (1 - velocity/127);
- `pi/piano-fx.py` reads the keyboards, forwards everything to FluidSynth on channel 0 and, when a key is released or the
  pedal moves, sends the mechanics "notes" on channels 1-6. Levels follow the SFZ rules (the group's base volume,
  `amp_veltrack`, `rt_decay` - attenuation per second the key was held). With the pedal down, the string resonance is
  deferred until the pedal is released.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

They check the mechanics logic (`FXCore`), the structure of the `.deb` packages, and that the SF2 builders accept the
different sample packages (44.1 kHz/16 bit and 48 kHz/24 bit, WAV and FLAC, different folder layouts; this part needs
`ffmpeg` and is skipped without it). No hardware is needed. The ALSA exchange is not covered by tests (checked on a real Pi),
and the audio quality of a built bank was checked by hand through FluidSynth.

## If something does not work

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Licenses

The repository's scripts are MIT ([LICENSE](LICENSE)). The Salamander Grand Piano V3 sounds are CC-BY, by Alexander Holm
([NOTICE.md](NOTICE.md)). The `.sf2` files built from them are derivative works, and distributing them requires
attribution.
