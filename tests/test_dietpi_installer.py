"""Tests for the DietPi installer: the bundle's structure and a real run of install-piano.sh with fake system commands.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import gzip
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
with open(os.path.join(ROOT, 'VERSION')) as _f:
    VERSION = _f.read().strip()


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'build-dietpi-installer.py'), '--out', cls.tmp.name],
                       check=True, capture_output=True)
        cls.top = 'piano-synth-dietpi-%s' % VERSION

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_both_archives_have_the_same_files(self):
        z = zipfile.ZipFile(os.path.join(self.tmp.name, self.top + '.zip'))
        t = tarfile.open(os.path.join(self.tmp.name, self.top + '.tar.gz'))
        zip_names = sorted(n for n in z.namelist() if not n.endswith('/'))
        tar_names = sorted(m.name for m in t.getmembers() if m.isfile())
        self.assertEqual(zip_names, tar_names)
        expected = {'README.txt', 'dietpi.txt.example', 'boot-partition/Automation_Custom_Script.sh',
                    'boot-partition/piano-synth/install-piano.sh', 'boot-partition/piano-synth/banks/README.txt',
                    'boot-partition/piano-synth/piano-synth_%s_all.deb' % VERSION}
        self.assertEqual({n[len(self.top) + 1:] for n in tar_names}, expected)

    def test_scripts_are_executable_in_both_archives(self):
        z = zipfile.ZipFile(os.path.join(self.tmp.name, self.top + '.zip'))
        t = tarfile.open(os.path.join(self.tmp.name, self.top + '.tar.gz'))
        for name in ('boot-partition/Automation_Custom_Script.sh', 'boot-partition/piano-synth/install-piano.sh'):
            full = '%s/%s' % (self.top, name)
            self.assertTrue(t.getmember(full).mode & 0o111, name)
            self.assertTrue((z.getinfo(full).external_attr >> 16) & stat.S_IXUSR, name)

    def test_bundled_package_has_the_repository_version(self):
        t = tarfile.open(os.path.join(self.tmp.name, self.top + '.tar.gz'))
        deb = t.extractfile('%s/boot-partition/piano-synth/piano-synth_%s_all.deb' % (self.top, VERSION)).read()
        self.assertTrue(deb.startswith(b'!<arch>\n'))
        i = deb.index(b'control.tar.gz')                      # the ar header: name 16, ... size at offset 48..58
        size = int(deb[i + 48:i + 58])
        control_tar = gzip.decompress(deb[i + 60:i + 60 + size])
        control = tarfile.open(fileobj=io.BytesIO(control_tar)).extractfile('./control').read().decode()
        self.assertIn('Version: %s\n' % VERSION, control)

    def test_scripts_are_valid_bash(self):
        t = tarfile.open(os.path.join(self.tmp.name, self.top + '.tar.gz'))
        for name in ('boot-partition/Automation_Custom_Script.sh', 'boot-partition/piano-synth/install-piano.sh'):
            data = t.extractfile('%s/%s' % (self.top, name)).read()
            r = subprocess.run(['bash', '-n'], input=data, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_automation_script_hands_over_to_the_installer_on_both_boot_layouts(self):
        with open(os.path.join(ROOT, 'dietpi', 'Automation_Custom_Script.sh')) as f:
            text = f.read()
        self.assertIn('/boot/piano-synth', text)
        self.assertIn('/boot/firmware/piano-synth', text)
        self.assertIn('install-piano.sh', text)


class InstallerRunTests(unittest.TestCase):
    """Run install-piano.sh for real, with apt-get, id, hostname and piano-synth-install-banks replaced by fakes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, 'piano-synth')
        os.makedirs(os.path.join(self.dir, 'banks'))
        shutil.copy(os.path.join(ROOT, 'dietpi', 'install-piano.sh'), self.dir)
        self.calls = os.path.join(self.tmp.name, 'calls.txt')
        self.bin = os.path.join(self.tmp.name, 'bin')
        os.makedirs(self.bin)
        self.fake('id', 'echo ${FAKE_UID:-0}')
        self.fake('hostname', 'echo 192.168.1.50')
        self.fake('apt-get', 'echo "apt-get $*" >> "%s"' % self.calls)
        self.fake('piano-synth-install-banks', 'echo "banks $*" >> "%s"' % self.calls)
        self.config = os.path.join(self.tmp.name, 'config.txt')

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, 'w') as f:
            f.write('#!/bin/bash\n%s\n' % body)
        os.chmod(path, 0o755)

    def run_installer(self, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'],
                 PIANO_INSTALL_LOG=os.path.join(self.tmp.name, 'install.log'), PIANO_BOOT_CONFIG=self.config)
        e.update(env)
        return subprocess.run(['bash', os.path.join(self.dir, 'install-piano.sh')], env=e, capture_output=True, text=True)

    def add_deb(self):
        with open(os.path.join(self.dir, 'piano-synth_1.1.0_all.deb'), 'wb') as f:
            f.write(b'x')

    def config_text(self):
        with open(self.config) as f:
            return f.read()

    def calls_text(self):
        try:
            with open(self.calls) as f:
                return f.read()
        except OSError:
            return ''

    def test_installs_the_package_and_switches_the_onboard_audio_on(self):
        self.add_deb()
        with open(self.config, 'w') as f:
            f.write('arm_64bit=1\ndtparam=audio=off\n')
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('apt-get install -y %s' % os.path.join(self.dir, 'piano-synth_1.1.0_all.deb'), self.calls_text())
        self.assertEqual(self.config_text(), 'arm_64bit=1\ndtparam=audio=on\n')
        self.assertFalse(os.path.exists(self.config + '.bak'))
        self.assertIn('No banks', r.stdout)
        self.assertIn('http://192.168.1.50:8080/', r.stdout)
        self.assertIn('Reboot once', r.stdout)

    def test_adds_the_audio_line_when_it_is_missing_and_leaves_it_when_already_on(self):
        self.add_deb()
        with open(self.config, 'w') as f:
            f.write('arm_64bit=1\n')
        self.run_installer()
        self.assertIn('dtparam=audio=on', self.config_text())
        r = self.run_installer()
        self.assertEqual(self.config_text().count('dtparam=audio=on'), 1)
        self.assertNotIn('Reboot once', r.stdout)

    def test_banks_next_to_the_installer_are_installed(self):
        self.add_deb()
        for n in ('piano.sf2', 'mech.sf2'):
            open(os.path.join(self.dir, 'banks', n), 'wb').close()
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('banks --piano %s --mech %s' % (os.path.join(self.dir, 'banks', 'piano.sf2'),
                                                       os.path.join(self.dir, 'banks', 'mech.sf2')), self.calls_text())
        self.assertNotIn('No banks', r.stdout)

    def test_fails_clearly_without_the_package(self):
        r = self.run_installer()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('No piano-synth_*_all.deb', r.stdout)
        self.assertEqual(self.calls_text(), '')

    def test_refuses_to_run_as_a_normal_user(self):
        self.add_deb()
        r = self.run_installer(FAKE_UID='1000')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Run as root', r.stdout + r.stderr)
        self.assertEqual(self.calls_text(), '')


if __name__ == '__main__':
    unittest.main()
