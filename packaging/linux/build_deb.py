"""Build an Ubuntu-specific .deb with vendored Python dependencies.

Run inside the target Ubuntu release/architecture. System GTK/GStreamer bindings
remain managed by apt; binary Python wheels match that release's interpreter.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/linux')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Build on the target Ubuntu release, or use the Docker command in README.md')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    ubuntu = release.get('VERSION_ID', '').strip('"')
    if release.get('ID', '').strip('"') != 'ubuntu' or int(ubuntu.split('.')[0]) < 22:
        parser.error('Ubuntu 22.04 or newer is required')
    arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    if arch not in {'amd64', 'arm64'}:
        parser.error('Only amd64 and arm64 packages are supported')
    version = f'0.1.0~preview1+ubuntu{ubuntu}'
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve()/f'scribeloop_{version}_{arch}.deb'
    with tempfile.TemporaryDirectory(prefix='scribeloop-deb-') as temporary:
        stage = Path(temporary)/'stage'
        library = stage/'usr/lib/scribeloop'
        library.mkdir(parents=True)
        # Build a wheel from a clean source copy, never include .env, data, or models.
        source = Path(temporary)/'source'
        source.mkdir()
        for name in ('pyproject.toml', 'README.md'):
            shutil.copy2(ROOT/name, source/name)
        shutil.copytree(ROOT/'core', source/'core',
                        ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
        run(sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
            '--no-build-isolation', '--no-compile', '--target', library, f'{source}[security]', 'sqlcipher3==0.6.4')
        if not (library/'scribeloop_core/api/linux.py').is_file():
            raise RuntimeError('Application wheel is missing Linux desktop code')
        # System GI must win; do not vendor a second GTK stack.
        launcher = stage/'usr/bin/scribeloop'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 -I '
                            '/usr/lib/scribeloop/launch.py "$@"\n')
        launcher.chmod(0o755)
        (library/'launch.py').write_text(
            'import sys\nsys.path.insert(0, "/usr/lib/scribeloop")\n'
            'from scribeloop_core.api.linux import main\nmain()\n')
        # The owned child uses -m: isolate its environment and point it to our modules.
        application = stage/'usr/share/applications/scribeloop.desktop'
        application.parent.mkdir(parents=True)
        application.write_text('[Desktop Entry]\nType=Application\nName=ScribeLoop\n'
            'Comment=Record conversations and keep notes\nExec=scribeloop\n'
            'Icon=audio-input-microphone\nTerminal=false\nCategories=AudioVideo;\n'
            'StartupWMClass=ScribeLoop\n')
        documentation = stage/'usr/share/doc/scribeloop'
        documentation.mkdir(parents=True)
        shutil.copy2(ROOT/'packaging/linux/README.md', documentation/'README.md')
        minor = sys.version_info.minor
        control = stage/'DEBIAN/control'
        control.parent.mkdir()
        control.write_text(f'Package: scribeloop\nVersion: {version}\nArchitecture: {arch}\n'
            'Maintainer: ScribeLoop <support@scribeloop.invalid>\nSection: sound\nPriority: optional\n'
            f'Depends: python3 (>= 3.{minor}), python3 (<< 3.{minor + 1}), python3-gi, '
            'gir1.2-gtk-3.0, gir1.2-webkit2-4.1, gir1.2-gstreamer-1.0, '
            'gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-base, '
            'gstreamer1.0-plugins-good, pulseaudio-utils, libsndfile1, '
            'libsecret-1-0, gnome-keyring, dbus-user-session\n'
            'Description: Encrypted meeting notebook (Ubuntu preview)\n'
            ' Manual microphone and system recording with cloud transcription and notes.\n')
        # Store exact resolved versions for reproducibility and support.
        resolved = subprocess.check_output([sys.executable, '-m', 'pip', 'list',
            '--path', str(library), '--format=freeze', '--disable-pip-version-check'], text=True)
        (documentation/'requirements-resolved.txt').write_text(resolved)
        (documentation/'build.txt').write_text(
            f'Ubuntu {ubuntu}\nArchitecture {arch}\nPython {platform.python_version()}\n')
        run('dpkg-deb', '--root-owner-group', '--build', stage, output)
    print(output)


if __name__ == '__main__':
    main()
