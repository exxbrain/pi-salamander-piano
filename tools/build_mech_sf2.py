#!/usr/bin/env python3
"""Build Salamander-mech.sf2: piano mechanics (hammer/damper noise, string resonance, pedal).

Six presets in bank 0:
  prog 1 Hammer   (rel1..rel88, one key each, 21..108)
  prog 2 ResL     (harmL*, keys 20..88)
  prog 3 ResS     (harmS*)
  prog 4 ResV3    (harmV3*)
  prog 5 PedalDn  (pedalD1 -> key 60, pedalD2 -> key 61)
  prog 6 PedalUp  (pedalU1 -> key 60, pedalU2 -> key 61)

Every instrument maps velocity linearly to attenuation: attenuation_dB = 60 * (1 - vel/127),
so the companion program can request any attenuation 0..60 dB through the note velocity.
Samples are stereo, split into L/R SF2 samples panned hard left/right.
"""
import array
import os
import re
import struct
import subprocess
import sys

SRC = sys.argv[1]
OUT = sys.argv[2]
SFZ = os.path.join(SRC, 'SalamanderGrandPianoV3Retuned.sfz')
WAV = os.path.join(SRC, '44.1khz16bit')


def parse():
    inst = {'Hammer': [], 'ResL': [], 'ResS': [], 'ResV3': [], 'PedalDn': [], 'PedalUp': []}
    for raw in open(SFZ, encoding='utf-8', errors='replace'):
        line = raw.strip()
        if not line.startswith('<region>'):
            continue
        kv = dict(re.findall(r'(\w+)=(\S+)', line))
        f = kv['sample'].replace('\\', '/').split('/')[-1]
        if re.match(r'^rel\d+\.wav$', f):
            k = int(kv['lokey'])
            inst['Hammer'].append(dict(file=f, lo=k, hi=k, root=k, tune=0))
        elif f.startswith('harm'):
            name = {'harmL': 'ResL', 'harmS': 'ResS', 'harmV3': 'ResV3'}[re.match(r'^(harm(?:V3|L|S))', f).group(1)]
            inst[name].append(dict(file=f, lo=int(kv['lokey']), hi=int(kv['hikey']),
                                   root=int(kv.get('pitch_keycenter', 60)), tune=int(kv.get('tune', 0))))
        elif f.startswith('pedal'):
            name = 'PedalDn' if f.startswith('pedalD') else 'PedalUp'
            key = 60 if f.endswith('1.wav') else 61
            inst[name].append(dict(file=f, lo=key, hi=key, root=key, tune=0))
    return inst


def pad(s, n):
    b = s.encode('ascii', 'replace')[:n - 1]
    return b + b'\0' * (n - len(b))


def chunk(tag, data):
    if len(data) % 2:
        data += b'\0'
    return tag + struct.pack('<I', len(data)) + data


def lst(tag, data):
    return chunk(b'LIST', tag + data)


def decode(path):
    cmd = ['ffmpeg', '-v', 'error', '-i', path, '-ac', '2', '-ar', '44100', '-f', 's16le', '-']
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    a = array.array('h')
    a.frombytes(raw[:len(raw) // 4 * 4])
    return a[0::2].tobytes(), a[1::2].tobytes()


def main():
    inst = parse()
    pcm = bytearray()
    shdr = []
    n_points = 0

    def add(name, data, root, ctype, link):
        nonlocal n_points
        start = n_points
        pts = len(data) // 2
        end = start + pts
        pcm.extend(data)
        pcm.extend(b'\0' * 92)
        n_points = end + 46
        shdr.append((name, start, end, start + pts // 2, end - 8, 44100, root, 0, link, ctype))
        return len(shdr) - 1

    zones = {}  # inst name -> list of (lo, hi, tune, pan, sid)
    for name, regs in inst.items():
        zones[name] = []
        for r in regs:
            left, right = decode(os.path.join(WAV, r['file']))
            lid = len(shdr)
            base = os.path.splitext(r['file'])[0][:16]
            add(base + 'L', left, r['root'], 4, lid + 1)
            rid = add(base + 'R', right, r['root'], 2, lid)
            zones[name].append((r['lo'], r['hi'], r['tune'], -500, lid))
            zones[name].append((r['lo'], r['hi'], r['tune'], 500, rid))
        print(name, len(regs), 'samples')

    def g_range(oper, lo, hi):
        return struct.pack('<HBB', oper, lo, hi)

    def g_val(oper, v):
        return struct.pack('<Hh', oper, v)

    ibag = igen = imod = b''
    n_gen = n_mod = 0
    inst_hdr = b''
    order = ['Hammer', 'ResL', 'ResS', 'ResV3', 'PedalDn', 'PedalUp']
    n_ibag = 0
    for name in order:
        inst_hdr += pad(name, 20) + struct.pack('<H', n_ibag)
        # global zone: modulators
        ibag += struct.pack('<HH', n_gen, n_mod); n_ibag += 1
        for src, dest, amt in ((0x0502, 48, 0), (0x0102, 8, 0), (0x0102, 48, 600)):
            imod += struct.pack('<HHhHH', src, dest, amt, 0, 0); n_mod += 1
        for lo, hi, tune, pan, sid in zones[name]:
            ibag += struct.pack('<HH', n_gen, n_mod); n_ibag += 1
            igen += g_range(43, lo, hi)
            igen += g_range(44, 0, 127)
            n_gen += 2
            igen += g_val(17, pan); n_gen += 1
            if tune:
                igen += g_val(52, tune); n_gen += 1
            igen += struct.pack('<HH', 53, sid); n_gen += 1
    inst_hdr += pad('EOI', 20) + struct.pack('<H', n_ibag)
    ibag += struct.pack('<HH', n_gen, n_mod)
    igen += struct.pack('<HH', 0, 0)
    imod += b'\0' * 10

    phdr = b''
    pbag = b''
    pgen = b''
    for i, name in enumerate(order):
        phdr += pad(name, 20) + struct.pack('<HHHIII', i + 1, 0, i, 0, 0, 0)
        pbag += struct.pack('<HH', i, 0)
        pgen += struct.pack('<HH', 41, i)
    phdr += pad('EOP', 20) + struct.pack('<HHHIII', 255, 255, len(order), 0, 0, 0)
    pbag += struct.pack('<HH', len(order), 0)
    pgen += struct.pack('<HH', 0, 0)
    pmod = b'\0' * 10

    sh = b''
    for nm, st, en, ls, le, rate, root, corr, link, ctype in shdr:
        sh += pad(nm, 20) + struct.pack('<IIIIIBbHH', st, en, ls, le, rate, root, corr, link, ctype)
    sh += pad('EOS', 20) + struct.pack('<IIIIIBbHH', 0, 0, 0, 0, 0, 0, 0, 0, 0)

    info = chunk(b'ifil', struct.pack('<HH', 2, 1)) + chunk(b'isng', b'EMU8000\0') + \
        chunk(b'INAM', b'Salamander Mechanics\0') + \
        chunk(b'ICMT', b'Release/resonance/pedal samples from Salamander Grand Piano V3 by Alexander Holm (CC-BY)\0')
    sdta = lst(b'sdta', chunk(b'smpl', bytes(pcm)))
    pdta = lst(b'pdta', chunk(b'phdr', phdr) + chunk(b'pbag', pbag) + chunk(b'pmod', pmod) + chunk(b'pgen', pgen) +
               chunk(b'inst', inst_hdr) + chunk(b'ibag', ibag) + chunk(b'imod', imod) + chunk(b'igen', igen) +
               chunk(b'shdr', sh))
    body = b'sfbk' + lst(b'INFO', info) + sdta + pdta
    with open(OUT, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', len(body)) + body)
    print('samples %d, sf2 %.1f MB' % (len(shdr), os.path.getsize(OUT) / 1e6))


main()
