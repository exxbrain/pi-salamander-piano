"""Unit tests for the mechanics logic of pi/piano-fx.py (no MIDI hardware needed).

Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import importlib.util
import os
import random
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('piano_fx', os.path.join(HERE, '..', 'pi', 'piano-fx.py'))
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def core(**kw):
    kw.setdefault('randomness', 0)
    kw.setdefault('rng', random.Random(1))
    return fx.FXCore(**kw)


def att_db(vel):
    """Inverse of FXCore._vel: attenuation in dB requested through the note velocity."""
    return fx.ATT_RANGE_DB * (1 - vel / 127.0)


def ch(events, channel):
    return [(n, v) for c, n, v in events if c == channel]


class HammerTests(unittest.TestCase):
    def test_note_off_plays_hammer_on_channel_1(self):
        c = core()
        c.note_on(60, 100, 0.0)
        ev = c.note_off(60, 0.5)
        self.assertEqual([n for n, _ in ch(ev, fx.CH_HAMMER)], [60])

    def test_harder_touch_is_louder(self):
        levels = []
        for vel in (30, 60, 90, 127):
            c = core(hammer_boost=0, hammer_exp=0.8)
            c.note_on(60, vel, 0.0)
            (_, v), = ch(c.note_off(60, 0.5), fx.CH_HAMMER)
            levels.append(att_db(v))
        self.assertEqual(levels, sorted(levels, reverse=True))   # attenuation falls as touch grows

    def test_very_soft_touch_is_inaudible_with_steep_curve(self):
        c = core(hammer_boost=0, hammer_exp=2.0)
        c.note_on(60, 15, 0.0)
        self.assertEqual(ch(c.note_off(60, 0.5), fx.CH_HAMMER), [])

    def test_note_off_without_note_on_is_silent(self):
        self.assertEqual(core().note_off(60, 1.0), [])

    def test_longer_hold_is_quieter(self):
        def level(hold):
            c = core(hammer_boost=10)
            c.note_on(60, 100, 0.0)
            (_, v), = ch(c.note_off(60, hold), fx.CH_HAMMER)
            return v
        self.assertGreater(level(0.1), level(10.0))


class ResonanceAndPedalTests(unittest.TestCase):
    def test_release_without_pedal_gives_resonance(self):
        c = core()
        c.note_on(60, 100, 0.0)
        ev = c.note_off(60, 1.0)
        self.assertTrue(ch(ev, fx.CH_RES_L))
        self.assertTrue(ch(ev, fx.CH_RES_V3))
        self.assertFalse(ch(ev, fx.CH_RES_S))        # soft layer only for velocity < 45

    def test_soft_touch_uses_short_resonance_layer(self):
        c = core()
        c.note_on(60, 30, 0.0)
        ev = c.note_off(60, 1.0)
        self.assertTrue(ch(ev, fx.CH_RES_S))
        self.assertFalse(ch(ev, fx.CH_RES_L))

    def test_resonance_is_deferred_while_pedal_is_down(self):
        c = core()
        self.assertEqual(len(ch(c.sustain(127, 0.0), fx.CH_PEDAL_DN)), 1)
        c.note_on(60, 100, 0.5)
        ev = c.note_off(60, 1.0)
        self.assertTrue(ch(ev, fx.CH_HAMMER))        # key noise is immediate
        self.assertFalse(ch(ev, fx.CH_RES_L))        # damper has not fallen yet
        up = c.sustain(0, 2.0)
        self.assertEqual(len(ch(up, fx.CH_PEDAL_UP)), 1)
        self.assertTrue(ch(up, fx.CH_RES_L))         # resonance when the pedal rises

    def test_repeated_pedal_value_does_nothing(self):
        c = core()
        c.sustain(127, 0.0)
        self.assertEqual(c.sustain(120, 0.1), [])

    def test_keys_outside_resonance_range_have_none(self):
        c = core()
        c.note_on(100, 100, 0.0)
        ev = c.note_off(100, 1.0)
        self.assertFalse(ch(ev, fx.CH_RES_L) or ch(ev, fx.CH_RES_V3))


class RandomnessTests(unittest.TestCase):
    def test_randomness_zero_is_deterministic(self):
        seen = set()
        for _ in range(50):
            c = core(randomness=0)
            c.note_on(60, 80, 0.0)
            seen.add(tuple(ch(c.note_off(60, 0.5), fx.CH_HAMMER)))
        self.assertEqual(len(seen), 1)

    def test_randomness_varies_level_and_key(self):
        keys, levels = set(), set()
        c = fx.FXCore(hammer_boost=0, hammer_exp=0.8, randomness=1.0, rng=random.Random(5))
        for _ in range(300):
            c.note_on(60, 80, 0.0)
            for n, v in ch(c.note_off(60, 0.5), fx.CH_HAMMER):
                keys.add(n)
                levels.add(v)
        self.assertGreater(len(keys), 3)
        self.assertGreater(len(levels), 5)
        self.assertTrue(all(21 <= k <= 108 for k in keys))


if __name__ == '__main__':
    unittest.main()
