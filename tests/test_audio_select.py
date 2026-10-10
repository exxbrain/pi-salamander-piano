"""Tests for the automatic audio output choice in pi/piano-synth-run (a fake /proc/asound, no hardware needed).

Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RUN = os.path.join(ROOT, 'pi', 'piano-synth-run')

HEADPHONES = (0, 'Headphones', 'bcm2835_headphon - bcm2835 Headphones', False)
HDMI = (1, 'vc4hdmi', 'vc4-hdmi - vc4-hdmi', False)


def usb(num, card_id, name='USB-Audio - Some card', playback=True):
    return (num, card_id, name, True, playback)


def keyboard(num, card_id='mk3'):
    """A MIDI keyboard that shows up as a capture-only USB audio card (seen on a real Pi)."""
    return usb(num, card_id, 'USB-Audio - KL Essential 49 mk3', playback=False)


class AudioSelectTests(unittest.TestCase):
    def run_script(self, cards, **env):
        with tempfile.TemporaryDirectory() as asound:
            lines = []
            for card in cards:
                num, card_id, desc, is_usb = card[:4]
                playback = card[4] if len(card) > 4 else True
                lines.append('%2d [%-15s]: %s' % (num, card_id, desc))
                lines.append('                      %s' % desc)
                os.makedirs(os.path.join(asound, 'card%d' % num, 'pcm0p' if playback else 'pcm0c'))
                if is_usb:
                    with open(os.path.join(asound, 'card%d' % num, 'usbid'), 'w') as f:
                        f.write('1686:0090\n')
            with open(os.path.join(asound, 'cards'), 'w') as f:
                f.write('\n'.join(lines) + '\n')
            e = dict(os.environ, ASOUND_DIR=asound, DRY_RUN='1', USB_WAIT='0',
                     PIANO_SF2='/p.sf2', MECH_SF2='/m.sf2', FLUIDSYNTH='/usr/bin/fluidsynth')
            e.update({k: str(v) for k, v in env.items()})
            r = subprocess.run(['bash', RUN], env=e, capture_output=True, text=True)
            return r

    def test_only_onboard_uses_big_buffer(self):
        r = self.run_script([HEADPHONES])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('audio.alsa.device=plughw:CARD=Headphones', r.stdout)
        self.assertIn('-z 256 -c 3', r.stdout)

    def test_usb_card_is_preferred_and_uses_small_buffer(self):
        r = self.run_script([HEADPHONES, usb(1, 'U24')])
        self.assertIn('audio.alsa.device=plughw:CARD=U24', r.stdout)
        self.assertIn('-z 64 -c 3', r.stdout)

    def test_hdmi_is_never_chosen_over_onboard(self):
        r = self.run_script([HDMI, HEADPHONES])
        self.assertIn('plughw:CARD=Headphones', r.stdout)

    def test_hdmi_only_is_not_used_either(self):
        r = self.run_script([HDMI])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no sound card', r.stderr)

    def test_explicit_device_wins(self):
        r = self.run_script([HEADPHONES, usb(1, 'U24')], ALSA_DEVICE='plughw:Headphones', PERIOD_SIZE=512)
        self.assertIn('audio.alsa.device=plughw:Headphones', r.stdout)
        self.assertIn('-z 512 -c 3', r.stdout)

    def test_usb_buffer_is_configurable(self):
        r = self.run_script([usb(2, 'Interface')], USB_PERIOD_SIZE=128, USB_PERIODS=4)
        self.assertIn('plughw:CARD=Interface', r.stdout)
        self.assertIn('-z 128 -c 4', r.stdout)

    def test_banks_and_options_reach_fluidsynth(self):
        r = self.run_script([usb(1, 'U24')], GAIN='2.0', POLYPHONY=64, REVERB=1)
        self.assertNotIn(' -s ', r.stdout)               # no server mode: its control port listens on every interface
        self.assertNotIn(' -is ', r.stdout)
        self.assertNotIn('shell.', r.stdout)
        self.assertIn('-g 2.0', r.stdout)
        self.assertIn('synth.polyphony=64', r.stdout)
        self.assertIn('-R 1', r.stdout)
        self.assertTrue(r.stdout.strip().endswith('/p.sf2 /m.sf2'))

    def test_usb_card_without_playback_is_ignored(self):
        # regression: the keyboard's own USB audio card (capture only) was chosen instead of the jack
        r = self.run_script([HEADPHONES, keyboard(1)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('plughw:CARD=Headphones', r.stdout)
        self.assertIn('-z 256 -c 3', r.stdout)

    def test_interface_wins_over_keyboard_audio(self):
        r = self.run_script([HEADPHONES, keyboard(1), usb(2, 'U24')])
        self.assertIn('plughw:CARD=U24', r.stdout)

    def test_period_below_fluidsynth_minimum_is_clamped(self):
        # regression: USB_PERIOD_SIZE=32 made FluidSynth exit ("audio.period-size out of range"), so no sound at all
        r = self.run_script([usb(1, 'U24')], USB_PERIOD_SIZE=32)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-z 64 -c 3', r.stdout)
        self.assertIn('using 64', r.stderr)

    def test_period_above_maximum_is_clamped(self):
        r = self.run_script([HEADPHONES], PERIOD_SIZE=100000)
        self.assertIn('-z 8192 ', r.stdout)

    def test_first_usb_card_wins(self):
        r = self.run_script([usb(1, 'First'), usb(2, 'Second')])
        self.assertIn('plughw:CARD=First', r.stdout)


class PipePlumbingTests(unittest.TestCase):
    """FluidSynth must keep running on a root-only pipe, and commands written into the pipe must reach it."""

    def test_fluidsynth_reads_its_stdin_from_the_pipe(self):
        import stat
        import time
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, 'fake-fluidsynth')
            got = os.path.join(tmp, 'got.txt')
            with open(fake, 'w') as f:
                f.write('#!/bin/bash\nread -r line\necho "$line" > "%s"\n' % got)
            os.chmod(fake, 0o755)
            asound = os.path.join(tmp, 'asound')
            os.makedirs(os.path.join(asound, 'card0', 'pcm0p'))
            with open(os.path.join(asound, 'cards'), 'w') as f:
                f.write(' 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n')
            fifo = os.path.join(tmp, 'cmd')
            env = dict(os.environ, ASOUND_DIR=asound, USB_WAIT='0', PIANO_SF2='/p', MECH_SF2='/m',
                       FLUIDSYNTH=fake, CMD_FIFO=fifo)
            proc = subprocess.Popen(['bash', RUN], env=env, stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):                              # wait for the pipe to appear
                    if os.path.exists(fifo):
                        break
                    time.sleep(0.1)
                self.assertTrue(stat.S_ISFIFO(os.stat(fifo).st_mode))
                self.assertEqual(oct(os.stat(fifo).st_mode & 0o777), oct(0o600))       # root only
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, b'set synth.gain 1.2\n')
                os.close(fd)
                proc.wait(timeout=5)
                with open(got) as f:
                    self.assertEqual(f.read().strip(), 'set synth.gain 1.2')
            finally:
                if proc.poll() is None:
                    proc.kill()


if __name__ == '__main__':
    unittest.main()
