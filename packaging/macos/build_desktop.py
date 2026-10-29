"""Build the production desktop development app using the installed Xcode toolchain."""
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from package_internal import external_path, native_files, runtime_paths, validate_app

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'build/macos'
APP = ROOT / 'dist/macos/ScribeLoop.app'
ENV = dict(os.environ, DEVELOPER_DIR='/Applications/Xcode.app/Contents/Developer')
ENV.pop('SCRIBELOOP_BUILD_DEEPGRAM_API_KEY', None)
MINIMUM_MACOS = '13.0'
PYTHON = BUILD/'venv13/bin/python'


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, env=ENV, check=True)


def main(*, app=APP, fixture=False):
    app = Path(app)
    if fixture and app.resolve() == APP.resolve():
        raise ValueError('Preview must not replace the production app')
    frozen_name = 'scribeloop-preview-core' if fixture else 'scribeloop-core'
    python = PYTHON
    if not python.exists():
        raise SystemExit('Create build/macos/venv13 using requirements-desktop.txt first.')
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
    run('xcrun', 'swiftc', '-parse-as-library', '-swift-version', '5', '-O',
        '-target', f'arm64-apple-macos{MINIMUM_MACOS}',
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
            '--minimum-macos', MINIMUM_MACOS,
            '--output', contents / f'Helpers/scribeloop-{kind}')
    # Some wheels retain absolute build-machine RPATHs even after PyInstaller
    # relocates their actual dependencies. Remove those unused search paths;
    # validation below still rejects unresolved absolute library dependencies.
    for binary in native_files(app):
        commands = subprocess.check_output(['otool','-l',str(binary)],text=True)
        remove = [path for path in runtime_paths(commands) if external_path(path)]
        for path in remove:
            run('install_name_tool','-delete_rpath',path,binary)
        if remove:
            run('codesign','--force','--sign','-',binary)
    # Deployment target is explicit; never inherit the build host's OS version.
    version = subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip()
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleExecutable': 'ScribeLoop', 'CFBundleName': 'ScribeLoop Preview' if fixture else 'ScribeLoop',
            'CFBundleIdentifier': 'ai.scribeloop.desktop.preview' if fixture else 'ai.scribeloop.desktop', 'CFBundlePackageType': 'APPL',
            'CFBundleIconFile': 'ScribeLoop.icns',
            'CFBundleShortVersionString': '0.1.9', 'CFBundleVersion': '10',
            'LSMinimumSystemVersion': MINIMUM_MACOS, 'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription': 'Record your microphone only when you choose Record for a conversation.',
            'NSAudioCaptureUsageDescription': 'Record selected meeting audio only when you choose Record.',
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, f)
    run('codesign', '--force', '--sign', '-', app)
    run('codesign', '--verify', '--deep', '--strict', app)
    if not fixture:
        validate_app(app)  # Includes every frozen library's real deployment floor.
    freeze = subprocess.check_output(['uv', 'pip', 'freeze', '--python', str(python)], text=True)
    (BUILD / ('preview-requirements-resolved.txt' if fixture else 'requirements-resolved.txt')).write_text(freeze)
    (BUILD / ('preview-manifest.json' if fixture else 'desktop-manifest.json')).write_text(json.dumps({'fixture': fixture, 'host_macos': version,
        'architecture': os.uname().machine, 'app': str(app), 'signing': 'ad-hoc',
        'minimum_macos': MINIMUM_MACOS, 'release_signed': False}, indent=2) + '\n')
    print(app)


if __name__ == '__main__':
    main()
