"""Structure tests for the .deb files made by scripts/build-deb.py (no dpkg needed).

Run from the repository root:   python3 -m unittest discover -s tests -v
dpkg itself is the final judge: on the Pi run `dpkg-deb --info FILE.deb` and `dpkg-deb --contents FILE.deb`.
"""
import gzip
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def parse_ar(path):
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:8] == b'!<arch>\n'
    pos, members = 8, []
    while pos < len(data):
        hdr = data[pos:pos + 60]
        assert len(hdr) == 60 and hdr[58:60] == b'`\n', 'bad ar header at %d' % pos
        name = hdr[0:16].decode().strip().rstrip('/')
        size = int(hdr[48:58].decode())
        members.append((name, data[pos + 60:pos + 60 + size], hdr))
        pos += 60 + size + (size % 2)
    return members


def parse_control(text):
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith(' '):
            fields[key] += '\n' + line
        else:
            key, _, val = line.partition(': ')
            fields[key] = val
    return fields


class DebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = cls.tmp.name
        cls.banks = {}
        for name, payload in (('piano.sf2', b'P' * 5000), ('mech.sf2', b'M' * 3001)):
            p = os.path.join(cls.out, name)
            with open(p, 'wb') as f:
                f.write(payload)
            cls.banks[name] = (p, payload)
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'build-deb.py'), '--version', '9.9.9',
                        '--out', os.path.join(cls.out, 'dist'),
                        '--banks', cls.banks['piano.sf2'][0], cls.banks['mech.sf2'][0]],
                       check=True, capture_output=True)
        cls.main = os.path.join(cls.out, 'dist', 'piano-synth_9.9.9_all.deb')
        cls.fonts = os.path.join(cls.out, 'dist', 'piano-synth-soundfonts_9.9.9_all.deb')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def members(self, deb):
        m = parse_ar(deb)
        self.assertEqual([x[0] for x in m], ['debian-binary', 'control.tar.gz', 'data.tar.gz'])
        self.assertEqual(m[0][1], b'2.0\n')
        return m

    def tar(self, blob):
        return tarfile.open(fileobj=io.BytesIO(gzip.decompress(blob)), mode='r:')

    def test_ar_layout(self):
        for deb in (self.main, self.fonts):
            self.members(deb)

    def test_control_fields(self):
        ctl = self.tar(self.members(self.main)[1][1])
        control = parse_control(ctl.extractfile('./control').read().decode())
        self.assertEqual(control['Package'], 'piano-synth')
        self.assertEqual(control['Version'], '9.9.9')
        self.assertEqual(control['Architecture'], 'all')
        for dep in ('fluidsynth', 'python3-mido', 'python3-rtmidi', 'alsa-utils'):
            self.assertIn(dep, control['Depends'])
        self.assertTrue(control['Description'].split('\n')[0])
        self.assertTrue(control['Installed-Size'].isdigit())

    def test_control_members_and_modes(self):
        ctl = self.tar(self.members(self.main)[1][1])
        names = {m.name: m for m in ctl.getmembers()}
        for script in ('./postinst', './prerm', './postrm'):
            self.assertEqual(names[script].mode & 0o755, 0o755, script)
        self.assertEqual(ctl.extractfile('./conffiles').read().decode().split(), ['/etc/piano-synth.conf'])

    def test_data_files_owner_and_modes(self):
        data = self.tar(self.members(self.main)[2][1])
        names = {m.name: m for m in data.getmembers()}
        for path in ('./etc/piano-synth.conf', './opt/piano-synth/piano-fx.py',
                     './lib/systemd/system/piano-synth.service', './lib/systemd/system/piano-fx.service',
                     './usr/bin/piano-synth-install-banks', './usr/share/doc/piano-synth/copyright',
                     './opt/piano-synth/soundfonts'):
            self.assertIn(path, names)
        for m in names.values():
            self.assertEqual((m.uid, m.gid, m.uname, m.gname), (0, 0, 'root', 'root'), m.name)
        self.assertEqual(names['./opt/piano-synth/piano-fx.py'].mode, 0o755)
        self.assertEqual(names['./usr/bin/piano-synth-install-banks'].mode, 0o755)
        self.assertTrue(names['./opt/piano-synth/soundfonts'].isdir())

    def test_md5sums_match_data(self):
        m = self.members(self.main)
        ctl, data = self.tar(m[1][1]), self.tar(m[2][1])
        for line in ctl.extractfile('./md5sums').read().decode().splitlines():
            digest, path = line.split('  ', 1)
            self.assertEqual(hashlib.md5(data.extractfile('./' + path).read()).hexdigest(), digest, path)

    def test_services_use_conf_and_server_flag(self):
        data = self.tar(self.members(self.main)[2][1])
        synth = data.extractfile('./lib/systemd/system/piano-synth.service').read().decode()
        self.assertIn('EnvironmentFile=/etc/piano-synth.conf', synth)
        self.assertIn('ExecStart=/opt/piano-synth/piano-synth-run', synth)
        runner = data.extractfile('./opt/piano-synth/piano-synth-run').read().decode()
        self.assertIn('mkfifo', runner)                     # FluidSynth keeps running on a root-only pipe, no network port
        self.assertNotIn('shell.port', runner)
        self.assertEqual(data.getmember('./opt/piano-synth/piano-synth-run').mode, 0o755)
        names = [m.name for m in data.getmembers()]
        self.assertIn('./lib/udev/rules.d/90-piano-synth.rules', names)
        for web_file in ('./opt/piano-synth/pianoconf.py', './opt/piano-synth/piano-web.py',
                         './opt/piano-synth/web/index.html', './lib/systemd/system/piano-web.service'):
            self.assertIn(web_file, names)

    def test_soundfonts_package(self):
        m = self.members(self.fonts)
        control = parse_control(self.tar(m[1][1]).extractfile('./control').read().decode())
        self.assertEqual(control['Package'], 'piano-synth-soundfonts')
        self.assertEqual(control['Depends'], 'piano-synth (>= 9.9.9)')
        data = self.tar(m[2][1])
        for name, (_, payload) in self.banks.items():
            self.assertEqual(data.extractfile('./opt/piano-synth/soundfonts/' + name).read(), payload)


if __name__ == '__main__':
    unittest.main()
