"""Tests for pi/piano-web.py: the HTTP API with the system actions replaced by fakes (no Pi, no root needed)."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.join(HERE, '..', 'pi')
sys.path.insert(0, PI)
spec = importlib.util.spec_from_file_location('piano_web', os.path.join(PI, 'piano-web.py'))
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)

CONF = """PIANO_SF2=/p.sf2
MECH_SF2=/m.sf2
ALSA_DEVICE=auto
GAIN=1.0
POLYPHONY=128
USB_PERIOD_SIZE=64
USB_PERIODS=3
REVERB=0
FX_ARGS="--hammer-boost 0 --hammer-exp 0.8 --random 1"
"""


class FakeActions:
    def __init__(self):
        self.restarts = 0
        self.gains = []
        self.gain_ok = True

    def service_state(self, unit):
        return 'active'

    def restart_synth(self):
        self.restarts += 1

    def set_gain(self, value):
        self.gains.append(value)
        return self.gain_ok

    def cards(self):
        return [dict(id='U24', name='U-24', usb=True), dict(id='Headphones', name='bcm2835 Headphones', usb=False)]

    def audio_output(self):
        return dict(device='plughw:CARD=U24', kind='usb', period=64, periods=3)


class WebTests(unittest.TestCase):
    def start(self, conf=CONF):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, 'piano-synth.conf')
        with open(self.conf, 'w') as f:
            f.write(conf)
        self.actions = FakeActions()
        self.app = web.App(self.conf, self.actions)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def call(self, path, body=None, headers=None, ctype='application/json', raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, headers=dict(headers or {}))
        if data is not None:
            req.add_header('Content-Type', ctype)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def jcall(self, *a, **kw):
        code, body = self.call(*a, **kw)
        return code, json.loads(body.decode())

    def conf_text(self):
        with open(self.conf) as f:
            return f.read()

    def test_index_page_is_served(self):
        self.start()
        code, body = self.call('/')
        self.assertEqual(code, 200)
        self.assertIn(b'<title>', body)

    def test_state_has_settings_spec_and_status(self):
        self.start()
        code, s = self.jcall('/api/state')
        self.assertEqual(code, 200)
        self.assertEqual(s['settings']['hammer_exp'], 0.8)
        self.assertEqual(s['settings']['velocity_curve'], 1.0)
        self.assertEqual(s['spec']['gain']['scope'], 'gain')
        self.assertEqual((s['spec']['velocity_curve']['lo'], s['spec']['velocity_curve']['hi']), (0.4, 2.5))
        self.assertEqual(s['status']['synth'], 'active')
        self.assertEqual(s['status']['output']['device'], 'plughw:CARD=U24')
        self.assertEqual([c['id'] for c in s['status']['cards']], ['U24', 'Headphones'])
        self.assertFalse(s['auth_required'])

    def test_fx_setting_is_stored_without_restart(self):
        self.start()
        code, r = self.jcall('/api/settings', {'velocity_curve': 0.8, 'hammer_boost': 3})
        self.assertEqual(code, 200)
        self.assertTrue(r['applied']['fx'])
        self.assertFalse(r['restart_required'])
        self.assertIn('--velocity-curve 0.8', self.conf_text())
        self.assertEqual(self.actions.restarts, 0)

    def test_gain_is_applied_live_once(self):
        self.start()
        code, r = self.jcall('/api/settings', {'gain': 1.5})
        self.assertEqual((code, r['applied']['gain']), (200, True))
        self.assertEqual(self.actions.gains, [1.5])
        code, r = self.jcall('/api/settings', {'gain': 1.5})                      # unchanged -> nothing to do
        self.assertIsNone(r['applied']['gain'])
        self.assertEqual(self.actions.gains, [1.5])

    def test_gain_failure_is_reported(self):
        self.start()
        self.actions.gain_ok = False
        code, r = self.jcall('/api/settings', {'gain': 2.0})
        self.assertEqual((code, r['applied']['gain']), (200, False))
        self.assertIn('GAIN=2', self.conf_text())                                  # still stored for the next start

    def test_audio_setting_asks_for_a_restart_but_does_not_do_it(self):
        self.start()
        code, r = self.jcall('/api/settings', {'alsa_device': 'plughw:U24', 'reverb': 1})
        self.assertEqual(code, 200)
        self.assertTrue(r['restart_required'])
        self.assertEqual(self.actions.restarts, 0)
        self.assertIn('ALSA_DEVICE=plughw:U24', self.conf_text())
        code, r = self.jcall('/api/restart', {})
        self.assertEqual(code, 200)
        self.assertEqual(self.actions.restarts, 1)

    def test_bad_requests_are_rejected_and_change_nothing(self):
        self.start()
        before = self.conf_text()
        cases = [
            ('/api/settings', {'nope': 1}),
            ('/api/settings', {'gain': 99}),
            ('/api/settings', {'alsa_device': 'plughw:U24; reboot'}),
            ('/api/settings', {'hammer_exp': 'x'}),
            ('/api/settings', {}),
        ]
        for path, body in cases:
            with self.subTest(body):
                code, _ = self.jcall(path, body)
                self.assertEqual(code, 400)
        self.assertEqual(self.call('/api/settings', raw=b'{not json', ctype='application/json')[0], 400)
        self.assertEqual(self.call('/api/settings', raw=b'{"gain": 1.2}', ctype='text/plain')[0], 400)   # no simple cross-site form
        self.assertEqual(self.call('/api/settings', raw=b'x' * 5000)[0], 400)                             # too large
        self.assertEqual(self.conf_text(), before)
        self.assertEqual(self.actions.gains, [])

    def test_unknown_path(self):
        self.start()
        self.assertEqual(self.call('/nothing')[0], 404)
        self.assertEqual(self.call('/api/nothing', {'a': 1})[0], 404)

    def test_token_protects_the_api_but_not_the_page(self):
        self.start(CONF + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/')[0], 200)
        self.assertEqual(self.call('/api/state')[0], 401)
        self.assertEqual(self.call('/api/state', headers={'X-Token': 'wrong'})[0], 401)
        self.assertEqual(self.call('/api/settings', {'gain': 1.1})[0], 401)
        self.assertEqual(self.call('/api/restart', {})[0], 401)
        code, s = self.jcall('/api/state', headers={'X-Token': 's3cret'})
        self.assertEqual(code, 200)
        self.assertTrue(s['auth_required'])
        self.assertEqual(self.call('/api/settings', {'gain': 1.1}, headers={'X-Token': 's3cret'})[0], 200)
        self.assertEqual(self.actions.restarts, 0)


class CardsTests(unittest.TestCase):
    def test_cards_skips_hdmi_and_capture_only_cards(self):
        with tempfile.TemporaryDirectory() as a:
            rows = [(0, 'Headphones', 'pcm0p', False), (1, 'vc4hdmi', 'pcm0p', False),
                    (2, 'mk3', 'pcm0c', True), (3, 'U24', 'pcm0p', True)]
            text = ''
            for n, cid, pcm, usb in rows:
                text += ' %d [%-15s]: x - Name %s\n' % (n, cid, cid)
                os.makedirs(os.path.join(a, 'card%d' % n, pcm))
                if usb:
                    open(os.path.join(a, 'card%d' % n, 'usbid'), 'w').close()
            with open(os.path.join(a, 'cards'), 'w') as f:
                f.write(text)
            cards = web.Actions(asound=a).cards()
            self.assertEqual([(c['id'], c['usb']) for c in cards], [('Headphones', False), ('U24', True)])


if __name__ == '__main__':
    unittest.main()
