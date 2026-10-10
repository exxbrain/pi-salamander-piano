"""Settings shared by piano-fx (live reload) and piano-web (the web interface): /etc/piano-synth.conf.

The file stays a plain KEY=VALUE file that systemd reads as an EnvironmentFile. Only the whitelisted settings below
can be changed through the web interface, each with a type and a range, so a request can never inject anything else.

Scopes tell what has to happen after a change:
  fx    - picked up by the running piano-fx within a couple of seconds
  gain  - set live in FluidSynth (its TCP shell) and stored for the next start
  synth - needs a restart of piano-synth (the banks reload, ~45 s)
"""
import os
import re
import shlex
import tempfile

CONF_PATH = os.environ.get('PIANO_CONF', '/etc/piano-synth.conf')

DEVICE_RE = re.compile(r'^(auto|plughw:[A-Za-z0-9_.,:=-]{1,40})$')

# name -> dict(kind, lo, hi, default, scope, key)   key = variable in the conf file ('FX_ARGS' = part of the options line)
SETTINGS = {
    'velocity_curve': dict(kind='float', lo=0.4, hi=2.5, default=1.0, scope='fx', key='FX_ARGS', flag='--velocity-curve'),
    'hammer_boost': dict(kind='float', lo=-30.0, hi=30.0, default=0.0, scope='fx', key='FX_ARGS', flag='--hammer-boost'),
    'hammer_exp': dict(kind='float', lo=0.0, hi=4.0, default=0.8, scope='fx', key='FX_ARGS', flag='--hammer-exp'),
    'random': dict(kind='float', lo=0.0, hi=3.0, default=1.0, scope='fx', key='FX_ARGS', flag='--random'),
    'res_boost': dict(kind='float', lo=-30.0, hi=30.0, default=0.0, scope='fx', key='FX_ARGS', flag='--res-boost'),
    'pedal_boost': dict(kind='float', lo=-30.0, hi=30.0, default=0.0, scope='fx', key='FX_ARGS', flag='--pedal-boost'),
    'gain': dict(kind='float', lo=0.1, hi=5.0, default=1.0, scope='gain', key='GAIN'),
    'reverb': dict(kind='int', lo=0, hi=1, default=0, scope='synth', key='REVERB'),
    'polyphony': dict(kind='int', lo=16, hi=256, default=128, scope='synth', key='POLYPHONY'),
    'alsa_device': dict(kind='device', default='auto', scope='synth', key='ALSA_DEVICE'),
    'usb_period_size': dict(kind='int', lo=64, hi=8192, default=64, scope='synth', key='USB_PERIOD_SIZE'),
    'usb_periods': dict(kind='int', lo=2, hi=8, default=3, scope='synth', key='USB_PERIODS'),
    'period_size': dict(kind='int', lo=64, hi=8192, default=256, scope='synth', key='PERIOD_SIZE'),
    'periods': dict(kind='int', lo=2, hi=8, default=3, scope='synth', key='PERIODS'),
}
FX_NAMES = [n for n, s in SETTINGS.items() if s['key'] == 'FX_ARGS']
LINE_RE = re.compile(r'^([A-Z][A-Z0-9_]*)=(.*)$')


def parse_conf(path=None):
    """KEY -> value (quotes removed). Missing file -> empty dict."""
    values = {}
    try:
        with open(path or CONF_PATH, encoding='utf-8') as f:
            for line in f:
                m = LINE_RE.match(line.rstrip('\n'))
                if m:
                    try:
                        parts = shlex.split(m.group(2))
                    except ValueError:
                        parts = [m.group(2)]
                    values[m.group(1)] = ' '.join(parts)
    except OSError:
        pass
    return values


def _coerce(name, raw):
    spec = SETTINGS[name]
    if spec['kind'] == 'device':
        value = str(raw).strip()
        if not DEVICE_RE.match(value):
            raise ValueError('%s: expected "auto" or a plughw:<card> device' % name)
        return value
    try:
        value = float(raw) if spec['kind'] == 'float' else int(float(raw))
    except (TypeError, ValueError):
        raise ValueError('%s: not a number: %r' % (name, raw))
    if isinstance(raw, bool) or value != value:     # NaN or a JSON boolean
        raise ValueError('%s: not a number: %r' % (name, raw))
    if not spec['lo'] <= value <= spec['hi']:
        raise ValueError('%s: %s is outside %s..%s' % (name, value, spec['lo'], spec['hi']))
    return value


def validate(updates):
    """Return a cleaned copy of `updates`; raise ValueError for unknown names, bad types or out-of-range values."""
    if not isinstance(updates, dict) or not updates:
        raise ValueError('expected a non-empty object of settings')
    clean = {}
    for name, raw in updates.items():
        if name not in SETTINGS:
            raise ValueError('unknown setting: %s' % name)
        clean[name] = _coerce(name, raw)
    return clean


def _fmt(value):
    if isinstance(value, float):
        text = ('%.4f' % value).rstrip('0').rstrip('.')
        return text or '0'
    return str(value)


def _parse_fx_args(text):
    """'--hammer-boost 2 --random 1' -> {'hammer_boost': 2.0, 'random': 1.0} (unknown / bad parts are ignored)."""
    found = {}
    try:
        tokens = shlex.split(text or '')
    except ValueError:
        return found
    flags = {s['flag']: n for n, s in SETTINGS.items() if s['key'] == 'FX_ARGS'}
    for i, tok in enumerate(tokens[:-1]):
        if tok in flags:
            try:
                found[flags[tok]] = float(tokens[i + 1])
            except ValueError:
                pass
    return found


def get_settings(path=None):
    """Every setting as a typed value; defaults fill whatever the file does not define or defines badly."""
    raw = parse_conf(path)
    fx = _parse_fx_args(raw.get('FX_ARGS', ''))
    out = {}
    for name, spec in SETTINGS.items():
        value = fx.get(name) if spec['key'] == 'FX_ARGS' else raw.get(spec['key'])
        try:
            out[name] = _coerce(name, value) if value is not None else spec['default']
        except ValueError:
            out[name] = spec['default']
    return out


def _fx_line(settings):
    parts = []
    for name in FX_NAMES:
        parts.append('%s %s' % (SETTINGS[name]['flag'], _fmt(settings[name])))
    return 'FX_ARGS="%s"' % ' '.join(parts)


def apply_updates(updates, path=None):
    """Validate and store `updates` in the conf file (atomic replace, comments and other lines are kept).

    Returns (new_settings, scopes) where scopes is the set of scopes that changed.
    """
    path = path or CONF_PATH
    clean = validate(updates)
    current = get_settings(path)
    merged = dict(current, **clean)
    changed = {n for n in clean if clean[n] != current[n]}

    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    wanted = {}                                   # conf KEY -> full new line
    for name in clean:
        spec = SETTINGS[name]
        if spec['key'] == 'FX_ARGS':
            wanted['FX_ARGS'] = _fx_line(merged)
        else:
            wanted[spec['key']] = '%s=%s' % (spec['key'], _fmt(merged[name]))
    done = set()
    for i, line in enumerate(lines):
        m = LINE_RE.match(line)
        if m and m.group(1) in wanted:
            lines[i] = wanted[m.group(1)]
            done.add(m.group(1))
    while lines and lines[-1] == '':
        lines.pop()
    for key, line in wanted.items():
        if key not in done:
            lines.append(line)
    text = '\n'.join(lines) + '\n'

    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix='.piano-conf.', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return merged, {SETTINGS[n]['scope'] for n in changed}
