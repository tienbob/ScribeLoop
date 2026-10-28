"""Build the production desktop development app using the installed Xcode toolchain."""
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'build/macos'
APP = ROOT / 'dist/macos/ScribeLoop.app'
ENV = dict(os.environ, DEVELOPER_DIR='/Applications/Xcode.app/Contents/Developer')
ENV.pop('SCRIBELOOP_BUILD_DEEPGRAM_API_KEY', None)


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, env=ENV, check=True)


def transcription_key(environ=None, env_file=None):
    environ = os.environ if environ is None else environ
    key = environ.get('SCRIBELOOP_BUILD_DEEPGRAM_API_KEY', '').strip()
    if not key:
        path = ROOT/'.env' if env_file is None else Path(env_file)
        if path.is_file():
            for line in path.read_text(encoding='utf-8').splitlines():
                name, separator, value = line.strip().removeprefix('export ').partition('=')
                if separator and name.strip() == 'DEEPGRAM_API_KEY':
                    key = value.strip()
                    if len(key)>=2 and key[0]==key[-1] and key[0] in {'"', "'"}:
                        key=key[1:-1]
    if not 16<=len(key)<=512 or not key.isascii() or any(c.isspace() for c in key):
        raise SystemExit('Set SCRIBELOOP_BUILD_DEEPGRAM_API_KEY or DEEPGRAM_API_KEY in the ignored build .env before packaging.')
    return key


def main(*, app=APP, fixture=False):
    app = Path(app)
    if fixture and app.resolve() == APP.resolve():
        raise ValueError('Preview must not replace the production app')
    key = None if fixture else transcription_key()
    frozen_name = 'scribeloop-preview-core' if fixture else 'scribeloop-core'
    python = BUILD / 'venv/bin/python'
    if not python.exists():
        raise SystemExit('Create the separate packaging venv using requirements-m0.txt first.')
    run(python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', frozen_name, '--distpath', BUILD / 'frozen',
        '--workpath', BUILD / ('preview-freeze' if fixture else 'pyinstaller'), '--specpath', BUILD,
        '--collect-all', 'sqlite_vec', '--collect-all', 'sqlcipher3',
        '--hidden-import', 'keyring.backends.macOS',
        '--hidden-import', 'scribeloop_core.storage.encrypted',
        '--hidden-import', 'scribeloop_core.pipeline.deepgram_meeting',
        '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.auto',
        '--hidden-import', 'uvicorn.protocols.http.auto',
        '--hidden-import', 'uvicorn.protocols.websockets.websockets_impl',
        '--hidden-import', 'uvicorn.lifespan.on',
        ROOT / ('packaging/macos/desktop_test_entry.py' if fixture else 'packaging/macos/desktop_entry.py'))
    if app.exists():
        shutil.rmtree(app)  # Only this generated artifact; never user data.
    contents = app / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Helpers').mkdir()
    (contents / 'Resources').mkdir()
    config = contents / 'Resources/transcription.json'
    if not fixture:
        with config.open('x', encoding='utf-8') as stream:
            os.chmod(config, 0o600)
            json.dump({'provider': 'deepgram', 'api_key': key}, stream)
    key = None
    run('xcrun', 'swiftc', '-parse-as-library', '-swift-version', '5', '-O',
        '-module-cache-path', BUILD / 'swift-cache',
        ROOT / 'shell/macos/Desktop/DesktopClient.swift',
        ROOT / 'shell/macos/Desktop/MeetingPlayback.swift',
        ROOT / 'shell/macos/Desktop/PlaybackSmoke.swift',
        ROOT / 'shell/macos/Desktop/Appearance.swift',
        ROOT / 'shell/macos/Desktop/DesktopApp.swift',
        ROOT / 'shell/macos/Desktop/Preferences.swift',
        '-o', contents / 'MacOS/ScribeLoop')
    shutil.copytree(BUILD / 'frozen' / frozen_name, contents / 'Resources/Core')
    if fixture:
        (contents/'Resources/Core'/frozen_name).rename(contents/'Resources/Core/scribeloop-core')
    shutil.copy2(ROOT/'shell/macos/Assets/ScribeLoop.icns',contents/'Resources/ScribeLoop.icns')
    for kind in ('meeting', 'observer'):
        run(python, ROOT / 'scripts/build_capture.py', '--kind', kind,
            '--output', contents / f'Helpers/scribeloop-{kind}')
    # Host-only spike: no claim of compatibility with an untested older OS.
    version = subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip()
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable': 'ScribeLoop', 'CFBundleName': 'ScribeLoop Preview' if fixture else 'ScribeLoop',
            'CFBundleIdentifier': 'ai.scribeloop.desktop.preview' if fixture else 'ai.scribeloop.desktop', 'CFBundlePackageType': 'APPL',
            'CFBundleIconFile': 'ScribeLoop.icns',
            'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1',
            'LSMinimumSystemVersion': version, 'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription': 'Record your microphone only when you choose Record for a conversation.',
            'NSAudioCaptureUsageDescription': 'Record selected meeting audio only when you choose Record.',
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, f)
    run('codesign', '--force', '--sign', '-', app)
    run('codesign', '--verify', '--deep', '--strict', app)
    freeze = subprocess.check_output(['uv', 'pip', 'freeze', '--python', str(python)], text=True)
    (BUILD / ('preview-requirements-resolved.txt' if fixture else 'requirements-resolved.txt')).write_text(freeze)
    (BUILD / ('preview-manifest.json' if fixture else 'desktop-manifest.json')).write_text(json.dumps({'fixture': fixture, 'host_macos': version,
        'architecture': os.uname().machine, 'app': str(app), 'signing': 'ad-hoc',
        'release_signed': False}, indent=2) + '\n')
    print(app)


if __name__ == '__main__':
    main()
