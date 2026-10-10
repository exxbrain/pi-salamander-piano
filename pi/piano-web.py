#!/usr/bin/env python3
"""piano-web: a small web interface for the piano module (standard library only).

  http://<pi>:8080/        the control page
  GET  /api/state          settings, their ranges, service status, sound cards
  POST /api/settings       {"velocity_curve": 0.8, ...}  - validated, stored in /etc/piano-synth.conf, applied
  POST /api/restart        restart piano-synth (needed after changing audio output / buffer / reverb / polyphony)

Settings that can be changed are whitelisted with ranges in pianoconf.py. Optional WEB_TOKEN, WEB_PORT and WEB_BIND
come from /etc/piano-synth.conf. Without WEB_TOKEN anybody on your network can change the settings (nothing else).
"""
import hmac
import json
import os
import re
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pianoconf

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
MAX_BODY = 4096
OUTPUT_RE = re.compile(r'audio output (\S+) \((\w+)\), period (\d+) x (\d+)')


class Actions:
    """Everything that touches the system; tests replace this."""

    def __init__(self, asound=None):
        self.asound = asound or os.environ.get('ASOUND_DIR', '/proc/asound')

    def service_state(self, unit):
        try:
            r = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=3)
            return r.stdout.strip() or 'unknown'
        except (OSError, subprocess.SubprocessError):
            return 'unknown'

    def restart_synth(self):
        subprocess.Popen(['systemctl', '--no-block', 'restart', 'piano-synth.service'])

    def set_gain(self, value):
        """Live master gain through FluidSynth's TCP shell (server mode, localhost only). True on success."""
        try:
            with socket.create_connection(('127.0.0.1', 9800), timeout=2) as s:
                s.sendall(('gain %s\nquit\n' % pianoconf._fmt(value)).encode('ascii'))
            return True
        except OSError:
            return False

    def cards(self):
        """Sound cards that can play audio: [{id, name, usb}]."""
        result = []
        try:
            with open(os.path.join(self.asound, 'cards'), encoding='utf-8') as f:
                text = f.read()
        except OSError:
            return result
        for m in re.finditer(r'^\s*(\d+) \[([^\]]*)\]:\s*(.*)$', text, re.M):
            num, cid, name = m.group(1), m.group(2).strip(), m.group(3).strip()
            card = os.path.join(self.asound, 'card' + num)
            try:
                if not any(n.startswith('pcm') and n.endswith('p') for n in os.listdir(card)):
                    continue                                 # no playback (a keyboard's own USB audio, ...)
            except OSError:
                continue
            if 'hdmi' in cid.lower():
                continue
            result.append(dict(id=cid, name=name, usb=os.path.exists(os.path.join(card, 'usbid'))))
        return result

    def audio_output(self):
        try:
            r = subprocess.run(['journalctl', '-u', 'piano-synth', '-n', '300', '--no-pager', '-o', 'cat'],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        last = None
        for m in OUTPUT_RE.finditer(r.stdout):
            last = m
        if not last:
            return None
        return dict(device=last.group(1), kind=last.group(2), period=int(last.group(3)), periods=int(last.group(4)))


class App:
    def __init__(self, conf_path=None, actions=None):
        self.conf_path = conf_path or pianoconf.CONF_PATH
        self.actions = actions or Actions()
        conf = pianoconf.parse_conf(self.conf_path)
        self.token = conf.get('WEB_TOKEN', '')

    def spec(self):
        return {n: {k: v for k, v in s.items() if k in ('kind', 'lo', 'hi', 'default', 'scope')}
                for n, s in pianoconf.SETTINGS.items()}

    def state(self):
        a = self.actions
        return dict(
            settings=pianoconf.get_settings(self.conf_path),
            spec=self.spec(),
            status=dict(synth=a.service_state('piano-synth'), bridge=a.service_state('piano-fx'),
                        output=a.audio_output(), cards=a.cards()),
        )

    def update(self, body):
        settings, scopes = pianoconf.apply_updates(body, self.conf_path)     # ValueError -> 400
        done = dict(fx='fx' in scopes, gain=None)         # gain: True live, False failed, None unchanged
        if 'gain' in scopes:
            done['gain'] = bool(self.actions.set_gain(settings['gain']))
        return dict(settings=settings, applied=done, restart_required='synth' in scopes)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'piano-web'

        def log_message(self, fmt, *args):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

        def _send(self, code, payload, ctype='application/json'):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            if not app.token:
                return True
            return hmac.compare_digest(self.headers.get('X-Token', ''), app.token)

        def _body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                raise ValueError('bad body size')
            if 'application/json' not in self.headers.get('Content-Type', ''):
                raise ValueError('Content-Type must be application/json')
            return json.loads(self.rfile.read(length).decode('utf-8'))

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                try:
                    with open(os.path.join(WEB_DIR, 'index.html'), 'rb') as f:
                        return self._send(200, f.read(), 'text/html')
                except OSError:
                    return self._send(500, dict(error='index.html is missing'))
            if self.path == '/api/state':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                return self._send(200, dict(app.state(), auth_required=bool(app.token)))
            if self.path == '/api/ping':
                return self._send(200, dict(ok=True, auth_required=bool(app.token)))
            self._send(404, dict(error='not found'))

        def do_POST(self):
            if not self._authorised():
                return self._send(401, dict(error='token required'))
            try:
                if self.path == '/api/settings':
                    return self._send(200, app.update(self._body()))
                if self.path == '/api/restart':
                    app.actions.restart_synth()
                    return self._send(200, dict(ok=True))
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, dict(error=str(exc)))
            except OSError as exc:
                return self._send(500, dict(error='cannot write the settings: %s' % exc))
            self._send(404, dict(error='not found'))

    return Handler


def main():
    conf = pianoconf.parse_conf()
    port = int(conf.get('WEB_PORT', '8080'))
    bind = conf.get('WEB_BIND', '0.0.0.0')
    app = App()
    server = ThreadingHTTPServer((bind, port), make_handler(app))
    print('piano-web on http://%s:%d/ (token %s)' % (bind, port, 'required' if app.token else 'not set'), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
