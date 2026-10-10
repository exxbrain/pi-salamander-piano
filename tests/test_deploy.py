"""scripts/deploy-to-pi.sh: builds the package and streams it to the Pi over ssh (a fake ssh stands in for the Pi)."""
import io
import os
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
with open(os.path.join(ROOT, 'VERSION')) as _f:
    VERSION = _f.read().strip()


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = os.path.join(self.tmp.name, 'bin')
        os.makedirs(self.bin)
        self.args_file = os.path.join(self.tmp.name, 'ssh-args.txt')
        self.stdin_file = os.path.join(self.tmp.name, 'ssh-stdin.tgz')
        with open(os.path.join(self.bin, 'ssh'), 'w') as f:
            f.write('#!/bin/bash\nprintf "%%s\\n" "$@" > "%s"\ncat > "%s"\n' % (self.args_file, self.stdin_file))
        os.chmod(os.path.join(self.bin, 'ssh'), 0o755)

    def run_deploy(self, *args):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'])
        return subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'deploy-to-pi.sh')] + list(args),
                              env=env, capture_output=True, text=True)

    def ssh_args(self):
        with open(self.args_file) as f:
            return f.read().split('\n')

    def sent_files(self):
        with open(self.stdin_file, 'rb') as f:
            data = f.read()
        return sorted(m.name.lstrip('./') for m in tarfile.open(fileobj=io.BytesIO(data), mode='r:gz').getmembers() if m.isfile())

    def test_a_bare_host_means_root_and_the_right_files_are_sent(self):
        r = self.run_deploy('192.168.1.95')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        args = self.ssh_args()
        self.assertEqual(args[0], 'root@192.168.1.95')
        self.assertIn('upgrade-on-pi.sh', args[1])
        self.assertEqual(self.sent_files(), sorted(['piano-synth_%s_all.deb' % VERSION, 'rollback-on-pi.sh', 'upgrade-on-pi.sh']))

    def test_an_explicit_user_is_kept(self):
        r = self.run_deploy('denis@piano.local')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ssh_args()[0], 'denis@piano.local')

    def test_no_host_prints_usage(self):
        r = self.run_deploy()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('usage', r.stderr)

    def test_the_scripts_on_the_pi_do_not_hardcode_a_version(self):
        for name in ('upgrade-on-pi.sh', 'rollback-on-pi.sh'):
            with open(os.path.join(ROOT, 'deploy', name)) as f:
                text = f.read()
            self.assertNotIn('1.0.0', text)
            self.assertNotIn('1.1.0', text)
        with open(os.path.join(ROOT, 'deploy', 'upgrade-on-pi.sh')) as f:
            self.assertIn('piano-synth_*_all.deb', f.read())


if __name__ == '__main__':
    unittest.main()
