#!/usr/bin/env python3
"""piano-fx: MIDI bridge between USB keyboards and FluidSynth that adds piano mechanics.

* passes every message from hardware MIDI inputs to FluidSynth on channel 0 (the piano);
* on key release plays hammer/damper noise (channel 1) and string resonance (channels 2-4);
  with the sustain pedal down the resonance is deferred until the pedal is released;
* on sustain pedal down/up plays the pedal noise (channels 5 / 6).

Levels follow the SFZ definition of Salamander Grand Piano V3: base volume, amp_veltrack
(note-on velocity) and rt_decay (dB per second the key was held).  The mechanics presets in
Salamander-mech.sf2 map velocity linearly to attenuation (60 dB over 0..127), so this program
requests an attenuation in dB through the note velocity.

Needs: python3-mido python3-rtmidi (apt).  Run:  piano-fx.py [--hammer-boost DB] [--res-boost DB]
[--pedal-boost DB]
"""
import argparse
import math
import random
import re
import sys
import threading
import time

CH_HAMMER, CH_RES_L, CH_RES_S, CH_RES_V3, CH_PEDAL_DN, CH_PEDAL_UP = 1, 2, 3, 4, 5, 6
PROGRAMS = {CH_HAMMER: 1, CH_RES_L: 2, CH_RES_S: 3, CH_RES_V3: 4, CH_PEDAL_DN: 5, CH_PEDAL_UP: 6}
ATT_RANGE_DB = 60.0   # attenuation covered by velocity 1..127 in Salamander-mech.sf2


def res_decay(key):
    """rt_decay (dB/s) of the long (L) resonance groups, by key."""
    if key <= 28:
        return 6
    if key <= 37:
        return 7
    if key <= 49:
        return 8
    return 9


class FXCore:
    """Pure logic: feed it note/pedal events, it returns FX note-ons as (channel, note, velocity)."""

    def __init__(self, hammer_boost=10.0, res_boost=0.0, pedal_boost=0.0, rng=None, hammer_exp=2.0,
                 randomness=1.0):
        self.boost = {'hammer': hammer_boost, 'res': res_boost, 'pedal': pedal_boost}
        self.hammer_exp = hammer_exp
        self.randomness = randomness
        self.rng = rng or random.Random()
        self.held = {}        # note -> (t_on, vel)
        self.sustained = {}   # note -> (t_on, vel) released while the pedal was down
        self.pedal = False

    @staticmethod
    def _vel(att_db):
        if att_db < 0:
            att_db = 0.0
        v = int(round(127 * (1.0 - att_db / ATT_RANGE_DB)))
        return v if v >= 1 else 0

    @staticmethod
    def _track(vel, pct):
        amp = 1.0 - pct / 100.0 * (1.0 - vel / 127.0)
        return -20.0 * math.log10(max(amp, 1e-3))

    def _emit(self, out, ch, note, att_db):
        v = self._vel(att_db)
        if v:
            out.append((ch, note, v))

    def _jitter_db(self, sigma):
        """Gaussian level variation in dB, clamped to +-2 sigma; 0 when randomness is off."""
        s = sigma * self.randomness
        if s <= 0:
            return 0.0
        return max(-2 * s, min(2 * s, self.rng.gauss(0.0, s)))

    def hammer(self, out, note, vel, hold):
        if 21 <= note <= 108:
            # amplitude ~ (vel/127)**hammer_exp: a soft touch is much quieter than a hard one
            att = 37.0 - 20.0 * self.hammer_exp * math.log10(max(vel, 1) / 127.0) \
                + 2.0 * hold - self.boost['hammer'] + self._jitter_db(3.0)
            key = note
            # every key has its own recorded sample, so a neighbour gives a different knock
            if self.randomness > 0 and self.rng.random() < 0.7 * min(self.randomness, 1.0):
                cand = note + self.rng.choice((-3, -2, -1, 1, 2, 3))
                if 21 <= cand <= 108:
                    key = cand
            self._emit(out, CH_HAMMER, key, att)

    def resonance(self, out, note, vel, hold):
        if not 20 <= note <= 88:
            return
        b = self.boost['res']
        track_l = 94 if note <= 37 else 90
        if vel >= 45:   # long layer, only for velocities from 45
            self._emit(out, CH_RES_L, note, 4.0 + self._track(vel, track_l) + res_decay(note) * hold - b)
        else:           # short layer for velocities up to 44
            track_s = 95 if note <= 46 else 90
            self._emit(out, CH_RES_S, note, self._track(vel, track_s) + 7.0 * hold - b)
        self._emit(out, CH_RES_V3, note, self._track(vel, 96) + 2.0 * hold - b)

    def pedal_sound(self, out, down):
        key = self.rng.choice((60, 61))
        base = 20.0 if down else 19.0
        self._emit(out, CH_PEDAL_DN if down else CH_PEDAL_UP, key,
                   base - self.boost['pedal'] + self._jitter_db(2.0))

    # ---- events ----
    def note_on(self, note, vel, now):
        self.held[note] = (now, vel)
        self.sustained.pop(note, None)
        return []

    def note_off(self, note, now):
        out = []
        item = self.held.pop(note, None)
        if item is None:
            return out
        t_on, vel = item
        hold = now - t_on
        self.hammer(out, note, vel, hold)
        if self.pedal:
            self.sustained[note] = item
        else:
            self.resonance(out, note, vel, hold)
        return out

    def sustain(self, value, now):
        out = []
        down = value >= 64
        if down == self.pedal:
            return out
        self.pedal = down
        self.pedal_sound(out, down)
        if not down:
            for note, (t_on, vel) in sorted(self.sustained.items()):
                self.resonance(out, note, vel, now - t_on)
            self.sustained.clear()
        return out

    def all_off(self):
        self.held.clear()
        self.sustained.clear()


# ---------------------------------------------------------------- MIDI I/O ---
SYNTH_RE = re.compile(r'FLUID Synth', re.I)
SKIP_RE = re.compile(r'Through|DINTHRU|MCU|HUI|ALV|FLUID|Timer|Announce|System|RtMidi|Client', re.I)


class Bridge:
    def __init__(self, core):
        import mido
        self.mido = mido
        self.core = core
        self.lock = threading.Lock()
        self.out = None
        self.out_name = None
        self.inputs = {}

    def send(self, msg):
        out = self.out
        if out is None:
            return
        try:
            out.send(msg)
        except Exception as exc:  # port vanished
            print('send failed:', exc, file=sys.stderr)
            self.out = None
            self.out_name = None

    def setup_programs(self):
        for ch, prog in PROGRAMS.items():
            self.send(self.mido.Message('control_change', channel=ch, control=0, value=0))
            self.send(self.mido.Message('control_change', channel=ch, control=32, value=0))
            self.send(self.mido.Message('program_change', channel=ch, program=prog))

    def on_msg(self, msg):
        mido = self.mido
        if msg.type in ('clock', 'active_sensing', 'reset', 'start', 'stop', 'continue'):
            return
        if hasattr(msg, 'channel') and msg.channel != 0:
            msg = msg.copy(channel=0)
        now = time.monotonic()
        with self.lock:
            self.send(msg)    # the piano first: lowest latency
            fx = []
            if msg.type == 'note_on' and msg.velocity > 0:
                fx = self.core.note_on(msg.note, msg.velocity, now)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                fx = self.core.note_off(msg.note, now)
            elif msg.type == 'control_change':
                if msg.control == 64:
                    fx = self.core.sustain(msg.value, now)
                elif msg.control in (120, 123):
                    self.core.all_off()
            for ch, note, vel in fx:
                self.send(mido.Message('note_on', channel=ch, note=note, velocity=vel))

    def scan(self):
        mido = self.mido
        try:
            outs = mido.get_output_names()
            ins = mido.get_input_names()
        except Exception as exc:
            print('scan failed:', exc, file=sys.stderr)
            return
        synth = next((n for n in outs if SYNTH_RE.search(n)), None)
        if synth != self.out_name:
            with self.lock:
                if self.out is not None:
                    try:
                        self.out.close()
                    except Exception:
                        pass
                self.out, self.out_name = None, None
                if synth:
                    try:
                        self.out = mido.open_output(synth)
                        self.out_name = synth
                        self.setup_programs()
                        print('synth:', synth, flush=True)
                    except Exception as exc:
                        print('open synth failed:', exc, file=sys.stderr)
        for name in ins:
            if name in self.inputs or SKIP_RE.search(name):
                continue
            try:
                self.inputs[name] = mido.open_input(name, callback=self.on_msg)
                print('input:', name, flush=True)
            except Exception as exc:
                print('open input failed:', name, exc, file=sys.stderr)
        for name in list(self.inputs):
            if name not in ins:
                try:
                    self.inputs.pop(name).close()
                except Exception:
                    pass
                print('input gone:', name, flush=True)

    def run(self):
        while True:
            self.scan()
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hammer-boost', type=float, default=10.0, help='dB louder than the SFZ level (default 10)')
    ap.add_argument('--res-boost', type=float, default=0.0)
    ap.add_argument('--pedal-boost', type=float, default=0.0)
    ap.add_argument('--hammer-exp', type=float, default=2.0,
                    help='hammer noise amplitude ~ (velocity/127)**EXP; larger = stronger dependence (default 2)')
    ap.add_argument('--random', type=float, default=1.0,
                    help='variation of knock/pedal level and sample choice: 0 = none, 1 = default, 2 = strong')
    args = ap.parse_args()
    core = FXCore(args.hammer_boost, args.res_boost, args.pedal_boost, hammer_exp=args.hammer_exp,
                  randomness=args.random)
    Bridge(core).run()


if __name__ == '__main__':
    main()
