"""The SF2 builders must accept any package of the Salamander set (44.1 kHz/16 bit or 48 kHz/24 bit, WAV or FLAC,
any folder layout): sample files are found through the paths written in the SFZ, and the result is always 44.1 kHz.

The tests make tiny synthetic sets with ffmpeg and are skipped when ffmpeg is not installed.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
HAVE_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
SECONDS = 1.5


def sf2_samples(path):
    """[(name, length_in_points, rate, root_key)] from the shdr chunk."""
    with open(path, 'rb') as f:
        data = f.read()
    i = data.index(b'shdr')
    n = struct.unpack('<I', data[i + 4:i + 8])[0] // 46
    out = []
    for k in range(n - 1):
        name, st, en, _, _, rate, root, _, _, _ = struct.unpack('<20sIIIIIBbHH', data[i + 8 + 46 * k:i + 8 + 46 * k + 46])
        out.append((name.split(b'\0')[0].decode(), en - st, rate, root))
    return out


def make_audio(path, rate, codec_args):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=%s:sample_rate=%d' % (SECONDS, rate), '-ac', '2'] + codec_args + [path],
                   check=True)


@unittest.skipUnless(HAVE_FFMPEG, 'ffmpeg/ffprobe not installed')
class BuilderPackageTests(unittest.TestCase):
    # name, folder, separator used in the SFZ, extension, ffmpeg codec args, rate
    CASES = [
        ('44k16 wav', '44.1khz16bit', '\\', 'wav', ['-c:a', 'pcm_s16le'], 44100),
        ('48k24 wav', '48khz24bit', '\\', 'wav', ['-c:a', 'pcm_s24le'], 48000),
        ('48k24 flac nested folder', 'FLAC/samples', '/', 'flac',
         ['-c:a', 'flac', '-sample_fmt', 's32', '-bits_per_raw_sample', '24'], 48000),
    ]

    def make_set(self, root, folder, sep, ext, codec, rate):
        names = ['A0v2', 'A0v5', 'C1v2', 'C1v5',
                 'rel1', 'harmLA0', 'harmSA0', 'harmV3A0', 'pedalD1', 'pedalD2', 'pedalU1', 'pedalU2']
        for n in names:
            make_audio(os.path.join(root, *folder.split('/'), n + '.' + ext), rate, codec)
        rel = folder.replace('/', sep) + sep
        lines = ['<group> amp_veltrack=73 ampeg_release=1']
        for n, lo, hi, key in (('A0', 21, 22, 21), ('C1', 23, 25, 24)):
            for layer in (2, 5):
                lines.append('<region> sample=%s%sv%d.%s lokey=%d hikey=%d lovel=1 hivel=127 pitch_keycenter=%d'
                             % (rel, n, layer, ext, lo, hi, key))
        lines += ['<group> trigger=release volume=-37',
                  '<region> sample=%srel1.%s lokey=21 hikey=21' % (rel, ext),
                  '<group> trigger=release volume=-4 rt_decay=6',
                  '<region> sample=%sharmLA0.%s lokey=20 hikey=22 lovel=45 pitch_keycenter=21' % (rel, ext),
                  '<group> trigger=release rt_decay=7',
                  '<region> sample=%sharmSA0.%s lokey=20 hikey=22 hivel=44 pitch_keycenter=21' % (rel, ext),
                  '<group> trigger=release rt_decay=2',
                  '<region> sample=%sharmV3A0.%s lokey=20 hikey=22 pitch_keycenter=21' % (rel, ext),
                  '<group> group=1 hikey=-1 lokey=-1 on_locc64=126 on_hicc64=127',
                  '<region> sample=%spedalD1.%s lorand=0 hirand=0.5' % (rel, ext),
                  '<region> sample=%spedalD2.%s lorand=0.5 hirand=1' % (rel, ext),
                  '<group> group=2 hikey=-1 lokey=-1 on_locc64=0 on_hicc64=1',
                  '<region> sample=%spedalU1.%s lorand=0 hirand=0.5' % (rel, ext),
                  '<region> sample=%spedalU2.%s lorand=0.5 hirand=1' % (rel, ext)]
        with open(os.path.join(root, 'SalamanderGrandPianoV3Retuned.sfz'), 'w') as f:
            f.write('\n'.join(lines) + '\n')

    def test_every_package_layout_builds_44k_banks(self):
        expected = int(SECONDS * 44100)
        for name, folder, sep, ext, codec, rate in self.CASES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                self.make_set(tmp, folder, sep, ext, codec, rate)
                piano, mech = os.path.join(tmp, 'p.sf2'), os.path.join(tmp, 'm.sf2')
                subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'tools', 'build_piano_sf2.py'), tmp, piano,
                                '--layers', '2,5', '--tail', '8'], check=True, capture_output=True)
                subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'tools', 'build_mech_sf2.py'), tmp, mech],
                               check=True, capture_output=True)
                p, m = sf2_samples(piano), sf2_samples(mech)
                self.assertEqual(len(p), 2 * 2 * 2)          # 2 notes x 2 layers x (L, R)
                self.assertEqual(len(m), 8 * 2)              # 1 rel + 3 harm + 4 pedal, each L and R
                for bank in (p, m):
                    for sample_name, length, sample_rate, _ in bank:
                        self.assertEqual(sample_rate, 44100, sample_name)
                        self.assertLessEqual(abs(length - expected), 4, (name, sample_name, length))
                self.assertEqual({x[3] for x in p}, {21, 24})   # root keys survive the conversion


if __name__ == '__main__':
    unittest.main()
