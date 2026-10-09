#!/usr/bin/env python3
"""Build Debian packages without dpkg-deb (works on macOS and Linux, only the standard library).

  scripts/build-deb.py [--version 1.0.0] [--out dist] [--banks PIANO.sf2 MECH.sf2]

Always builds  piano-synth_<version>_all.deb   (program, services, config, dependencies).
With --banks also builds piano-synth-soundfonts_<version>_all.deb (the two .sf2 files, hundreds of MB).
Install on the Pi:  sudo apt install ./piano-synth_1.0.0_all.deb
"""
import argparse
import gzip
import hashlib
import os
import sys
import tarfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MAINTAINER = 'Denis Zakharov <noreply@localhost>'
DEPENDS = 'fluidsynth, alsa-utils, python3, python3-mido, python3-rtmidi'


def src(*parts):
    return os.path.join(ROOT, *parts)


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def md5_of(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def parents(arcname):
    parts = arcname.split('/')[:-1]
    return ['/'.join(parts[:i + 1]) for i in range(len(parts))]


def write_tar_gz(path, files, mtime, extra_dirs=()):
    """files: list of (arcname, source) where source is a path (str) or bytes; mode comes from the tuple."""
    dirs = sorted({d for arc, _, _ in files for d in parents(arc)} | set(extra_dirs))
    with open(path, 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=6, mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode='w', format=tarfile.GNU_FORMAT) as tf:
        def owner(ti):
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            ti.mtime = mtime
            return ti
        root = owner(tarfile.TarInfo('./'))
        root.type, root.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(root)
        for d in dirs:
            ti = owner(tarfile.TarInfo('./' + d))
            ti.type, ti.mode = tarfile.DIRTYPE, 0o755
            tf.addfile(ti)
        for arc, source, mode in files:
            ti = owner(tarfile.TarInfo('./' + arc))
            ti.mode = mode
            if isinstance(source, bytes):
                import io
                ti.size = len(source)
                tf.addfile(ti, io.BytesIO(source))
            else:
                ti.size = os.path.getsize(source)
                with open(source, 'rb') as f:
                    tf.addfile(ti, f)


def ar_header(name, size, mtime):
    hdr = '%-16s%-12d%-6d%-6d%-8s%-10d`\n' % (name, mtime, 0, 0, '100644', size)
    assert len(hdr) == 60, hdr
    return hdr.encode('ascii')


def write_deb(deb_path, control_tar, data_tar, mtime):
    with open(deb_path, 'wb') as out:
        out.write(b'!<arch>\n')
        for name, path in (('debian-binary', None), ('control.tar.gz', control_tar), ('data.tar.gz', data_tar)):
            if path is None:
                data = b'2.0\n'
                out.write(ar_header(name, len(data), mtime))
                out.write(data)
                size = len(data)
            else:
                size = os.path.getsize(path)
                out.write(ar_header(name, size, mtime))
                with open(path, 'rb') as f:
                    for block in iter(lambda: f.read(1 << 20), b''):
                        out.write(block)
            if size % 2:
                out.write(b'\n')


def build_package(out_dir, name, version, depends, description, files, extra_dirs, scripts, conffiles, mtime, tmp):
    """files: [(arcname, source_path_or_bytes, mode)]; scripts: {'postinst': bytes, ...}."""
    md5lines, installed = [], 0
    for arc, source, _ in files:
        if isinstance(source, bytes):
            digest, size = hashlib.md5(source).hexdigest(), len(source)
        else:
            digest, size = md5_of(source), os.path.getsize(source)
        md5lines.append('%s  %s\n' % (digest, arc))
        installed += size
    control = (
        'Package: %s\nVersion: %s\nArchitecture: all\nMaintainer: %s\nInstalled-Size: %d\n'
        'Depends: %s\nSection: sound\nPriority: optional\nDescription: %s\n'
    ) % (name, version, MAINTAINER, (installed + 1023) // 1024, depends, description)
    ctl_files = [('control', control.encode('utf-8'), 0o644),
                 ('md5sums', ''.join(md5lines).encode('ascii'), 0o644)]
    if conffiles:
        ctl_files.append(('conffiles', ''.join(c + '\n' for c in conffiles).encode('ascii'), 0o644))
    for script, data in scripts.items():
        ctl_files.append((script, data, 0o755))

    data_tar = os.path.join(tmp, name + '-data.tar.gz')
    ctl_tar = os.path.join(tmp, name + '-control.tar.gz')
    write_tar_gz(data_tar, files, mtime, extra_dirs)
    # control archive has no directories except './'
    with open(ctl_tar, 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=9, mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode='w', format=tarfile.GNU_FORMAT) as tf:
        import io
        root = tarfile.TarInfo('./')
        root.type, root.mode, root.uid, root.gid, root.uname, root.gname, root.mtime = \
            tarfile.DIRTYPE, 0o755, 0, 0, 'root', 'root', mtime
        tf.addfile(root)
        for arc, data, mode in ctl_files:
            ti = tarfile.TarInfo('./' + arc)
            ti.size, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname, ti.mtime = len(data), mode, 0, 0, 'root', 'root', mtime
            tf.addfile(ti, io.BytesIO(data))
    deb_path = os.path.join(out_dir, '%s_%s_all.deb' % (name, version))
    write_deb(deb_path, ctl_tar, data_tar, mtime)
    return deb_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', default='1.0.0')
    ap.add_argument('--out', default=src('dist'))
    ap.add_argument('--banks', nargs=2, metavar=('PIANO_SF2', 'MECH_SF2'))
    args = ap.parse_args()

    mtime = int(os.environ.get('SOURCE_DATE_EPOCH', time.time()))
    os.makedirs(args.out, exist_ok=True)
    import tempfile
    copyright_text = (
        'piano-synth: scripts MIT (Denis Zakharov). Sounds: Salamander Grand Piano V3 by Alexander Holm, CC-BY.\n\n'
        + read(src('NOTICE.md')).decode('utf-8') + '\n' + read(src('LICENSE')).decode('utf-8')).encode('utf-8')

    main_files = [
        ('opt/piano-synth/piano-fx.py', src('pi', 'piano-fx.py'), 0o755),
        ('etc/piano-synth.conf', src('pi', 'piano-synth.conf'), 0o644),
        ('lib/systemd/system/piano-synth.service', src('pi', 'piano-synth.service'), 0o644),
        ('lib/systemd/system/piano-fx.service', src('pi', 'piano-fx.service'), 0o644),
        ('usr/bin/piano-synth-install-banks', src('packaging', 'piano-synth-install-banks'), 0o755),
        ('usr/share/doc/piano-synth/README.md', src('README.md'), 0o644),
        ('usr/share/doc/piano-synth/TROUBLESHOOTING.md', src('docs', 'TROUBLESHOOTING.md'), 0o644),
        ('usr/share/doc/piano-synth/copyright', copyright_text, 0o644),
    ]
    scripts = {name: read(src('packaging', 'debian', name)) for name in ('postinst', 'prerm', 'postrm')}
    description = (
        'Salamander piano on FluidSynth with piano mechanics (Raspberry Pi)\n'
        ' Systemd services piano-synth (FluidSynth) and piano-fx (MIDI bridge that adds hammer,\n'
        ' damper, string resonance and pedal noise). Settings: /etc/piano-synth.conf.\n'
        ' .\n'
        ' Sound banks are not included. Install piano-synth-soundfonts or run\n'
        ' piano-synth-install-banks.\n')

    with tempfile.TemporaryDirectory() as tmp:
        built = [build_package(args.out, 'piano-synth', args.version, DEPENDS, description, main_files,
                               ['opt/piano-synth/soundfonts'], scripts, ['/etc/piano-synth.conf'], mtime, tmp)]
        if args.banks:
            piano, mech = args.banks
            for p in (piano, mech):
                if not os.path.isfile(p):
                    sys.exit('No such file: ' + p)
            bank_files = [('opt/piano-synth/soundfonts/piano.sf2', os.path.abspath(piano), 0o644),
                          ('opt/piano-synth/soundfonts/mech.sf2', os.path.abspath(mech), 0o644)]
            bank_desc = (
                'Salamander piano sound banks for piano-synth\n'
                ' piano.sf2 (piano) and mech.sf2 (mechanics), built from Salamander Grand Piano V3\n'
                ' by Alexander Holm (CC-BY).\n')
            built.append(build_package(
                args.out, 'piano-synth-soundfonts', args.version, 'piano-synth (>= %s)' % args.version,
                bank_desc, bank_files, [],
                {'postinst': read(src('packaging', 'debian', 'soundfonts-postinst'))}, [], mtime, tmp))
    for path in built:
        print('%s  (%.1f MB)' % (path, os.path.getsize(path) / 1e6))


if __name__ == '__main__':
    main()
