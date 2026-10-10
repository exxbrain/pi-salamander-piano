# Plan: sound banks from a USB flash drive

Status: **plan only, nothing is built yet.** Written before the work so the decisions can be argued about first.

## Goal

The owner plugs a USB flash drive that contains sound banks into the module, picks a bank in the web interface, and the module plays it,
also after a reboot and with the drive removed. No SSH, no card reader.

## What we already have to build on

- FluidSynth loads a bank completely into RAM when it starts, so a bank does not have to stay on the flash drive afterwards.
- `PIANO_SF2` / `MECH_SF2` in `/etc/piano-synth.conf` select the banks; `piano-synth-run` passes them to FluidSynth.
- The web interface and its API (`pi/piano-web.py`, `pi/pianoconf.py`) with strict validation and an atomic config writer.
- Upgrades with backup and automatic rollback (`deploy/upgrade-on-pi.sh`): the same idea is needed for switching banks.
- The SF2 container is simple (RIFF); `tools/build_*_sf2.py` already write it, so reading the list of presets is a small job.

## Design decisions (proposed)

| Question | Proposal | Why |
|---|---|---|
| Play from the drive or copy? | **Import** (copy to the card, verify), then play from the card | the drive can be removed; a slow or flaky drive cannot cause dropouts; one source of truth |
| Where do banks live? | `/var/lib/piano-synth/banks/<id>/bank.sf2` + `meta.json` (name, size, SHA-256, presets, source) | survives upgrades (the package never touches it); easy to list and delete |
| How is the current bank chosen? | `PIANO_SF2` points into the library; one more setting selects the preset (bank:program) | multi-preset banks (General MIDI) need a choice; ours has a single piano |
| What may be on the drive? | only `*.sf2` / `*.sf3` inside a `piano-banks/` folder at the drive root, optional `bank.json` for name, author, licence | a fixed place and format; nothing else is ever read |
| Switching | copy, verify, switch the symlink, restart the synthesizer (~45 s), **roll back automatically** if it does not come back | a bad bank must never leave the module silent |
| Mounting | udev rule + `systemd-mount`, read-only, `nosuid,nodev,noexec`, under `/media/piano-usb/` | read-only and non-executable by construction; unmounts when the drive is pulled |
| Filesystems | FAT32 and exFAT first (what flash drives ship with); ext4 for free; NTFS only if `ntfs-3g` is wanted | FAT32 cannot hold files over 4 GB, irrelevant here (a Pi 3 cannot load such a bank) |
| Memory | refuse a bank larger than the free RAM minus a margin (about 450 MB on a Pi 3 with 1 GB) and say why | a too large bank means swapping, then the kernel killing FluidSynth |

## Phases

**0. Spike on the real Pi (find out before building)**
- Does DietPi have an automounter or `systemd-mount`? Which filesystem drivers are present (exFAT, NTFS)?
- Measure: does copying a 400 MB bank from a flash drive make the USB audio interface click? The Pi 3 has one shared USB 2.0 bus
  (the Ethernet chip and all ports sit behind it). Keyboard + audio interface + flash drive is also a power budget: check `vcgencmd get_throttled`.
- Does FluidSynth keep playing when the file it loaded from is deleted or its drive unplugged afterwards?

**1. Bank library and SF2 reader** (no hardware needed, fully testable on the Mac)
- `pi/sf2info.py`: read the header and the preset list with bounded reads; reject truncated files, wrong magic, absurd sizes.
- `pi/banklib.py`: import (copy to a temporary name, verify size and SHA-256, atomic rename), list, delete, choose the current bank.
- A small command line `piano-synth-banks list | import FILE | use ID | delete ID`.

**2. Detection and mounting**
- udev rule: on a USB block device with a filesystem, run a helper that mounts it read-only and writes `/run/piano-synth/usb.json`
  (label, free space, banks found); on removal it clears the file.
- Import from the mounted drive through the command line, with the checks of phase 1.

**3. Web interface**
- A "Banks" card: the drive and the banks found on it, **Import**, the library with **Use** and **Delete**, import progress.
- API: list, import, use, delete (every step validated, no paths accepted from the page: only ids the server itself produced).
- Switching with the automatic rollback described above; a clear message while the synthesizer restarts.

**4. Hardening and packaging**
- Run the web service and the synthesizer as an unprivileged user with systemd sandboxing; only the small mount/import helper needs root.
- The DietPi installer installs the needed packages (exFAT tools, `systemd-mount` if missing); tests and documentation (English and Russian).

**5. Later, optional:** SFZ banks (needs a converter, or a different player), importing the mechanics bank the same way, exporting
settings to the drive, auto-import on plug-in (off by default).

## Risks

| Risk | What reduces it |
|---|---|
| A crafted file attacks the parser (in our reader, or in FluidSynth itself, which is C code reading untrusted data) | strict size limits and bounded reads in our reader; mount `noexec`; run FluidSynth unprivileged (phase 4); accept files only from the fixed folder; never run anything from the drive |
| Copying makes the audio click (shared USB bus) | measure in the spike; limit copy speed; or require "not playing" and warn in the page |
| A bank is too big or broken and the module goes silent | size check before switching; automatic rollback to the previous bank |
| The drive is pulled while copying | copy to a temporary name and rename only after verification; a half file is never used |
| Weak power with three USB devices | measure; the page shows `get_throttled`; recommend a powered hub |
| Bank licences | the module ships no banks; show the licence from `bank.json` when present; the user answers for what they import |

## What the tests will cover (phases 1 to 3)

Valid and corrupt SF2 files (truncated, wrong magic, huge declared sizes), path safety (symlinks, `..`, odd names, non-ASCII labels), the library
(import twice, delete the current bank, disk full), the switch with a forced failure to prove the rollback, the web API validation, and the
mount helper with fakes. On the hardware, by hand: FAT32 / exFAT / ext4, labels with spaces, pulling the drive during a copy, a too large bank,
a bad bank, clicks while copying, reboot persistence.

## Decisions (owner, 2026-10-10)

| # | Question | Decision |
|---|---|---|
| 1 | Which banks? | **SF2 only** (SF3 comes for free). SFZ is out of scope. The mechanics bank stays ours (`mech.sf2`); imported banks are pianos |
| 2 | Import or play from the drive? | **Import**: copy to the card, then the drive can be removed |
| 3 | Filesystems | *Not answered, assumed:* FAT32 and exFAT (ext4 comes free); NTFS only if it turns out to be needed |
| 4 | Switching | **Only from the web page** for now (no hardware button) |
| 5 | Auto-import | **Ask first**: nothing is copied until the owner presses Import on the page |
| 6 | Hardening (no root) | *Not answered, assumed:* later, as phase 4 of this feature; phases 1 to 3 do not depend on it |

What this changes in the plan: phase 5 shrinks (no SFZ, no auto-import); phase 4 stays but is not a blocker; phase 3 has no hardware-button path.

## Next step

Phase 0 on the real Pi (read-only checks and one measurement, see above), in parallel with phase 1 (bank library and SF2 reader),
which needs no hardware and can be built and tested on the Mac.
