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


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, env=ENV, check=True)


def main():
    python = BUILD / 'venv/bin/python'
    if not python.exists():
        raise SystemExit('Create the separate packaging venv using requirements-m0.txt first.')
    run(python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', 'scribeloop-core', '--distpath', BUILD / 'frozen',
        '--workpath', BUILD / 'pyinstaller', '--specpath', BUILD,
        '--collect-all', 'sqlite_vec', '--collect-all', 'sqlcipher3',
        '--hidden-import', 'keyring.backends.macOS',
        '--hidden-import', 'scribeloop_core.storage.encrypted',
        '--hidden-import', 'scribeloop_core.pipeline.deepgram_meeting',
        '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.auto',
        '--hidden-import', 'uvicorn.protocols.http.auto',
        '--hidden-import', 'uvicorn.protocols.websockets.websockets_impl',
        '--hidden-import', 'uvicorn.lifespan.on',
        ROOT / 'packaging/macos/desktop_entry.py')
    if APP.exists():
        shutil.rmtree(APP)  # Only this generated artifact; never user data.
    contents = APP / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Helpers').mkdir()
    (contents / 'Resources').mkdir()
    run('xcrun', 'swiftc', '-parse-as-library', '-swift-version', '5', '-O',
        '-module-cache-path', BUILD / 'swift-cache',
        ROOT / 'shell/macos/Desktop/DesktopClient.swift',
        ROOT / 'shell/macos/Desktop/DesktopApp.swift',
        ROOT / 'shell/macos/Desktop/Preferences.swift',
        '-o', contents / 'MacOS/ScribeLoop')
    shutil.copytree(BUILD / 'frozen/scribeloop-core', contents / 'Resources/Core')
    for kind in ('meeting', 'observer'):
        run(python, ROOT / 'scripts/build_capture.py', '--kind', kind,
            '--output', contents / f'Helpers/scribeloop-{kind}')
    # Host-only spike: no claim of compatibility with an untested older OS.
    version = subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip()
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable': 'ScribeLoop', 'CFBundleName': 'ScribeLoop',
            'CFBundleIdentifier': 'ai.scribeloop.desktop', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1',
            'LSMinimumSystemVersion': version, 'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription': 'Record your microphone only when you choose Record for a conversation.',
            'NSAudioCaptureUsageDescription': 'Record selected meeting audio only when you choose Record.',
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, f)
    run('codesign', '--force', '--sign', '-', APP)
    run('codesign', '--verify', '--deep', '--strict', APP)
    freeze = subprocess.check_output(['uv', 'pip', 'freeze', '--python', str(python)], text=True)
    (BUILD / 'requirements-resolved.txt').write_text(freeze)
    (BUILD / 'desktop-manifest.json').write_text(json.dumps({'fixture': False, 'host_macos': version,
        'architecture': os.uname().machine, 'app': str(APP), 'signing': 'ad-hoc',
        'release_signed': False}, indent=2) + '\n')
    print(APP)


if __name__ == '__main__':
    main()
