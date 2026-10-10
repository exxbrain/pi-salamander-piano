"""Tests for pi/pianoconf.py (settings read/validate/store)."""
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pianoconf', os.path.join(HERE, '..', 'pi', 'pianoconf.py'))
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

SAMPLE = """# /etc/piano-synth.conf - comment kept
PIANO_SF2=/opt/piano-synth/soundfonts/piano.sf2
MECH_SF2=/opt/piano-synth/soundfonts/mech.sf2

# a comment above a setting
ALSA_DEVICE=auto
GAIN=1.0
POLYPHONY=128
USB_PERIOD_SIZE=64
USB_PERIODS=3
REVERB=0
FX_ARGS="--hammer-boost 0 --hammer-exp 0.8 --random 1"
"""


class ConfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'piano-synth.conf')
        with open(self.path, 'w') as f:
            f.write(SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        with open(self.path) as f:
            return f.read()

    def test_reads_values_and_fills_defaults(self):
        s = pc.get_settings(self.path)
        self.assertEqual(s['hammer_exp'], 0.8)
        self.assertEqual(s['gain'], 1.0)
        self.assertEqual(s['alsa_device'], 'auto')
        self.assertEqual(s['velocity_curve'], 1.0)          # not in the file -> default
        self.assertEqual(s['res_boost'], 0.0)

    def test_missing_file_gives_defaults(self):
        s = pc.get_settings(os.path.join(self.tmp.name, 'nope.conf'))
        self.assertEqual(s['gain'], 1.0)

    def test_rejects_unknown_and_out_of_range(self):
        for bad in ({'nope': 1}, {'gain': 99}, {'gain': -1}, {'polyphony': 5}, {'hammer_exp': 'abc'},
                    {'alsa_device': 'hw:1; rm -rf /'}, {'alsa_device': 'plughw:U24 -x'}, {'gain': True},
                    {'gain': float('nan')}, {}, [], 'x'):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    pc.validate(bad)
        self.assertEqual(self.read(), SAMPLE)               # nothing was written

    def test_accepts_valid_devices(self):
        for dev in ('auto', 'plughw:U24', 'plughw:CARD=U24', 'plughw:Headphones'):
            self.assertEqual(pc.validate({'alsa_device': dev})['alsa_device'], dev)

    def test_fx_change_rewrites_only_fx_args_and_keeps_the_rest(self):
        merged, scopes = pc.apply_updates({'velocity_curve': 0.8, 'hammer_boost': 3}, self.path)
        text = self.read()
        self.assertEqual(scopes, {'fx'})
        self.assertIn('# a comment above a setting', text)
        self.assertIn('PIANO_SF2=/opt/piano-synth/soundfonts/piano.sf2', text)
        self.assertIn('GAIN=1.0', text)
        line = [l for l in text.split('\n') if l.startswith('FX_ARGS=')]
        self.assertEqual(len(line), 1)
        self.assertIn('--velocity-curve 0.8', line[0])
        self.assertIn('--hammer-boost 3', line[0])
        self.assertIn('--hammer-exp 0.8', line[0])           # untouched values survive
        self.assertEqual(pc.get_settings(self.path)['velocity_curve'], 0.8)

    def test_scopes(self):
        _, scopes = pc.apply_updates({'gain': 1.5}, self.path)
        self.assertEqual(scopes, {'gain'})
        _, scopes = pc.apply_updates({'reverb': 1, 'gain': 1.5}, self.path)
        self.assertEqual(scopes, {'synth'})                  # gain unchanged this time
        _, scopes = pc.apply_updates({'reverb': 1}, self.path)
        self.assertEqual(scopes, set())                      # nothing changed -> nothing to do

    def test_new_key_is_appended(self):
        pc.apply_updates({'periods': 4}, self.path)
        self.assertTrue(self.read().rstrip('\n').endswith('PERIODS=4'))

    def test_roundtrip_is_stable_and_systemd_friendly(self):
        pc.apply_updates({'gain': 2, 'velocity_curve': 1.25, 'alsa_device': 'plughw:U24'}, self.path)
        s = pc.get_settings(self.path)
        self.assertEqual((s['gain'], s['velocity_curve'], s['alsa_device']), (2.0, 1.25, 'plughw:U24'))
        for line in self.read().split('\n'):
            if '=' in line and not line.startswith('#'):
                # systemd EnvironmentFile keeps trailing comments as part of the value: never write any
                self.assertNotIn(' #', line)

    def test_file_mode_and_no_temp_leftovers(self):
        pc.apply_updates({'gain': 2}, self.path)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), oct(0o644))
        self.assertEqual(os.listdir(self.tmp.name), ['piano-synth.conf'])


if __name__ == '__main__':
    unittest.main()
