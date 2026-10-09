#!/usr/bin/env python3
"""Build a reduced SF2 from the Salamander Grand Piano V3 SFZ set.

Usage: build_piano_sf2.py SRC_DIR OUT.sf2 --layers 2,7,10,13,15 --tail 8 [--mono] [--rate 44100]

Works with any package of the set (44.1 kHz/16 bit, 48 kHz/24 bit, WAV or FLAC): the sample files are located
through the paths written in the SFZ, and everything is converted to --rate (default 44100) and 16 bit.

Keeps only the chosen velocity layers, trims every sample to --tail seconds
with a 2 s fade-out, optionally downmixes to mono, and writes a plain SF2
(no loops, release/pedal/resonance noise samples are skipped).
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys

NOTE_RE = re.compile(r'^([A-G]#?\d)v(\d+)\.(?:wav|flac)$')


def parse_sfz(path):
    """Return {note_name: {layer: dict(lokey, hikey, root, tune, release)}}."""
    release = 1.0
    skip = False
    notes = {}
    for raw in open(path, encoding='utf-8', errors='replace'):
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        if line.startswith('<group>'):
            skip = 'trigger=release' in line or 'on_locc64' in line
            m = re.search(r'ampeg_release=([\d.]+)', line)
            release = float(m.group(1)) if m else 1.0
            continue
        if not line.startswith('<region>') or skip:
            continue
        kv = dict(re.findall(r'(\w+)=(\S+)', line))
        relpath = kv['sample'].replace('\\', '/')
        fname = relpath.split('/')[-1]
        m = NOTE_RE.match(fname)
        if not m:
            continue
        note, layer = m.group(1), int(m.group(2))
        notes.setdefault(note, {})[layer] = dict(
            file=relpath,
            lokey=int(kv['lokey']),
            hikey=int(kv['hikey']),
            root=int(kv.get('pitch_keycenter', 60)),
            tune=int(kv.get('tune', 0)),
            release=release,
        )
    return notes


def layer_ranges(chosen, centers, gamma=1.0):
    """Split 1..127 between chosen layers at midpoints of their original centers.

    gamma < 1 makes the instrument more sensitive: input velocity v is treated as
    127*(v/127)**gamma, so soft playing reaches louder layers.
    """
    chosen = sorted(chosen)
    cs = [centers[l] for l in chosen]
    mids = [(cs[i] + cs[i + 1]) / 2 for i in range(len(cs) - 1)]
    bounds = [0] + [int(round(127 * (m / 127) ** (1 / gamma))) for m in mids] + [127]
    out = {}
    for i, l in enumerate(chosen):
        lo = 1 if i == 0 else bounds[i] + 1
        out[l] = (lo, bounds[i + 1])
    return out


def _filters(tail, dur, extra=None):
    f = []
    if dur > tail:
        f.append('afade=t=out:st=%s:d=2' % (tail - 2))
    if extra:
        f.append(extra)
    return ['-af', ','.join(f)] if f else []


def measure_peak(path, tail, mono, dur, rate=44100):
    cmd = ['ffmpeg', '-v', 'info', '-i', path, '-t', str(tail), '-ar', str(rate)]
    if mono:
        cmd += ['-ac', '1']
    cmd += _filters(tail, dur, 'volumedetect') + ['-f', 'null', '-']
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stderr
    return float(re.search(r'max_volume: (-?[\d.]+) dB', out).group(1))


def decode(path, tail, mono, dur, gain_db=0.0, rate=44100):
    cmd = ['ffmpeg', '-v', 'error', '-i', path, '-t', str(tail), '-ar', str(rate)]
    if mono:
        cmd += ['-ac', '1']
    extra = 'volume=%.2fdB' % gain_db if abs(gain_db) > 0.01 else None
    cmd += _filters(tail, dur, extra) + ['-f', 's16le', '-']
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def pad_str(s, n):
    b = s.encode('ascii', 'replace')[:n - 1]
    return b + b'\0' * (n - len(b))


def chunk(tag, data):
    if len(data) % 2:
        data += b'\0'
    return tag + struct.pack('<I', len(data)) + data


def lst(tag, data):
    return chunk(b'LIST', tag + data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--sfz', default='SalamanderGrandPianoV3Retuned.sfz')
    ap.add_argument('--layers', required=True)
    ap.add_argument('--tail', type=float, default=8)
    ap.add_argument('--mono', action='store_true')
    ap.add_argument('--rate', type=int, default=44100, help='output sample rate, Hz (sources are resampled)')
    ap.add_argument('--name', default='Salamander Grand Lite')
    ap.add_argument('--gamma', type=float, default=1.0)
    ap.add_argument('--peak-lo', type=float, default=None, help='target peak (dBFS) of softest chosen layer')
    ap.add_argument('--peak-hi', type=float, default=None, help='target peak (dBFS) of loudest chosen layer')
    ap.add_argument('--peak-first', type=float, default=None, help='override target peak of the softest layer')
    ap.add_argument('--stretch', type=float, default=1.0, help='scale distance of every layer target below peak-hi')
    ap.add_argument('--veltrack', type=int, default=100, help='velocity->attenuation amount, cB')
    args = ap.parse_args()

    chosen = [int(x) for x in args.layers.split(',')]
    notes = parse_sfz(os.path.join(args.src, args.sfz))
    # sample paths in the SFZ are relative to the SFZ file itself
    wavdir = os.path.dirname(os.path.join(args.src, args.sfz))

    # original velocity centers (from the 16-layer SFZ ranges)
    hivels = [26, 34, 36, 43, 46, 50, 56, 64, 72, 80, 88, 96, 104, 112, 120, 127]
    centers = {}
    lo = 1
    for i, hi in enumerate(hivels):
        centers[i + 1] = (lo + hi) / 2
        lo = hi + 1
    vel = layer_ranges(chosen, centers, args.gamma)
    print('layer -> velocity range:', vel)

    # ---- samples ----
    pcm = bytearray()
    shdr = []          # tuples
    zones = []         # (key, vel, release, tune, pan, sample_id)
    n_points = 0

    def add_sample(name, data, rate, root, ctype, link):
        nonlocal n_points
        start = n_points
        pts = len(data) // 2
        end = start + pts
        pcm.extend(data)
        pcm.extend(b'\0' * 92)         # 46 zero points of padding
        n_points = end + 46
        shdr.append([name, start, end, start + pts // 2, end - 8, rate, root, 0, link, ctype])
        return len(shdr) - 1

    durs = {}
    peaks = {}
    layer_gain = {l: 0.0 for l in chosen}
    if args.peak_lo is not None and args.peak_hi is not None:
        ordered = sorted(chosen)
        for note in notes:
            for layer in ordered:
                info = notes[note].get(layer)
                if not info:
                    continue
                path = os.path.join(wavdir, *info['file'].split('/'))
                d = float(subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                    capture_output=True, text=True, check=True).stdout.strip())
                durs[path] = d
                peaks[(note, layer)] = measure_peak(path, args.tail, args.mono, d, args.rate)
        for i, l in enumerate(ordered):
            frac = i / max(1, len(ordered) - 1)
            target = args.peak_lo + (args.peak_hi - args.peak_lo) * frac
            if i == 0 and args.peak_first is not None:
                target = args.peak_first
            target = args.peak_hi - (args.peak_hi - target) * args.stretch
            layer_gain[l] = target - max(v for (n, ll), v in peaks.items() if ll == l)
        print('layer gains (dB):', {l: round(g_, 1) for l, g_ in layer_gain.items()})

    for note in sorted(notes, key=lambda n: notes[n][min(notes[n])]['root']):
        for layer in chosen:
            info = notes[note].get(layer)
            if not info:
                print('missing', note, layer, file=sys.stderr)
                continue
            path = os.path.join(wavdir, *info['file'].split('/'))
            dur = durs.get(path)
            if dur is None:
                dur = float(subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                    capture_output=True, text=True, check=True).stdout.strip())
            raw = decode(path, args.tail, args.mono, dur, layer_gain[layer], args.rate)
            key = (info['lokey'], info['hikey'])
            rel = info['release']
            if args.mono:
                sid = add_sample('%sv%d' % (note, layer), raw, args.rate, info['root'], 1, 0)
                zones.append((key, vel[layer], rel, info['tune'], 0, sid))
            else:
                # de-interleave stereo
                left = bytearray()
                right = bytearray()
                for i in range(0, len(raw) - 3, 4):
                    left += raw[i:i + 2]
                    right += raw[i + 2:i + 4]
                lid = len(shdr)
                add_sample('%sv%dL' % (note, layer), bytes(left), args.rate, info['root'], 4, lid + 1)
                rid = add_sample('%sv%dR' % (note, layer), bytes(right), args.rate, info['root'], 2, lid)
                zones.append((key, vel[layer], rel, info['tune'], -500, lid))
                zones.append((key, vel[layer], rel, info['tune'], 500, rid))
        print('.', end='', flush=True)
    print()

    # ---- instrument zones ----
    GEN_PAN, GEN_REL, GEN_FINE, GEN_SAMPLEID = 17, 38, 52, 53
    GEN_KEY, GEN_VEL, GEN_INST = 43, 44, 41

    def g(oper, amount):
        if oper in (GEN_KEY, GEN_VEL):
            return struct.pack('<HBB', oper, amount[0], amount[1])
        return struct.pack('<Hh' if amount < 0 else '<HH', oper, amount)

    def tc(seconds):
        import math
        return int(round(1200 * math.log2(seconds)))

    ibag = b''
    igen = b''
    imod = b''
    n_gen = 0
    n_mod = 0

    # global zone: modulators + default release
    ibag += struct.pack('<HH', n_gen, n_mod)
    igen += g(GEN_REL, tc(0.7)); n_gen += 1
    mods = [
        (0x0502, 48, 0),     # disable default velocity->attenuation
        (0x0102, 8, 0),      # disable default velocity->filter cutoff
        (0x0102, 48, args.veltrack),   # gentle linear velocity->attenuation
    ]
    for src, dest, amt in mods:
        imod += struct.pack('<HHhHH', src, dest, amt, 0, 0); n_mod += 1

    for key, vr, rel, tune, pan, sid in zones:
        ibag += struct.pack('<HH', n_gen, n_mod)
        igen += g(GEN_KEY, key); igen += g(GEN_VEL, vr)
        n_gen += 2
        if pan:
            igen += g(GEN_PAN, pan); n_gen += 1
        if rel >= 5:
            igen += g(GEN_REL, tc(rel)); n_gen += 1
        if tune:
            igen += g(GEN_FINE, tune); n_gen += 1
        igen += g(GEN_SAMPLEID, sid); n_gen += 1
    ibag += struct.pack('<HH', n_gen, n_mod)       # terminal
    igen += struct.pack('<HH', 0, 0)
    imod += b'\0' * 10

    inst = pad_str('Salamander Piano', 20) + struct.pack('<H', 0)
    inst += pad_str('EOI', 20) + struct.pack('<H', len(zones) + 1)

    phdr = pad_str('Salamander Piano', 20) + struct.pack('<HHHIII', 0, 0, 0, 0, 0, 0)
    phdr += pad_str('EOP', 20) + struct.pack('<HHHIII', 255, 255, 1, 0, 0, 0)
    pbag = struct.pack('<HH', 0, 0) + struct.pack('<HH', 1, 0)
    pmod = b'\0' * 10
    pgen = struct.pack('<HH', GEN_INST, 0) + struct.pack('<HH', 0, 0)

    sh = b''
    for name, st, en, ls, le, rate, root, corr, link, ctype in shdr:
        sh += pad_str(name, 20) + struct.pack('<IIIIIBbHH', st, en, ls, le, rate, root, corr, link, ctype)
    sh += pad_str('EOS', 20) + struct.pack('<IIIIIBbHH', 0, 0, 0, 0, 0, 0, 0, 0, 0)

    info_blk = chunk(b'ifil', struct.pack('<HH', 2, 1))
    info_blk += chunk(b'isng', b'EMU8000\0')
    info_blk += chunk(b'INAM', args.name.encode('ascii', 'replace') + b'\0')
    info_blk += chunk(b'ICMT', b'Reduced from Salamander Grand Piano V3 by Alexander Holm (CC-BY)\0')

    sdta = lst(b'sdta', chunk(b'smpl', bytes(pcm)))
    pdta = lst(b'pdta',
               chunk(b'phdr', phdr) + chunk(b'pbag', pbag) + chunk(b'pmod', pmod) + chunk(b'pgen', pgen) +
               chunk(b'inst', inst) + chunk(b'ibag', ibag) + chunk(b'imod', imod) + chunk(b'igen', igen) +
               chunk(b'shdr', sh))
    body = b'sfbk' + lst(b'INFO', info_blk) + sdta + pdta
    with open(args.out, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', len(body)) + body)
    print('samples: %d  zones: %d  sf2: %.1f MB (sample data %.1f MB)' % (
        len(shdr), len(zones), os.path.getsize(args.out) / 1e6, len(pcm) / 1e6))


if __name__ == '__main__':
    main()
