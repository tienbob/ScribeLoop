"""Preview builds cannot replace production or read operator credentials."""
import importlib.util
import plistlib
from pathlib import Path

import pytest


def builder():
    path = Path(__file__).resolve().parents[1]/'packaging/macos/build_desktop.py'
    spec = importlib.util.spec_from_file_location('desktop_packaging_audit',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fixture',[True,False])
def test_build_has_no_credentials_and_custom_destination_preserves_existing_app(tmp_path,monkeypatch,fixture):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'packaging/macos'))
    build = builder()
    root = tmp_path/'repo'
    work = root/'build'
    production = root/'ScribeLoop.app'
    production.mkdir(parents=True)
    sentinel = production/'unchanged'
    sentinel.write_bytes(b'existing production app')
    (work/'venv/bin').mkdir(parents=True)
    (work/'venv/bin/python').touch()
    (root/'shell/macos/Assets').mkdir(parents=True)
    (root/'shell/macos/Assets/ScribeLoop.icns').write_bytes(b'icon')
    monkeypatch.setattr(build,'ROOT',root)
    monkeypatch.setattr(build,'BUILD',work)
    monkeypatch.setattr(build,'PYTHON',work/'venv/bin/python')
    monkeypatch.setattr(build,'APP',production)
    monkeypatch.setattr(build,'validate_app',lambda app:None)
    monkeypatch.setenv('SCRIBELOOP_BUILD_DEEPGRAM_API_KEY','must-never-be-bundled')
    monkeypatch.setenv('DEEPGRAM_API_KEY','must-never-be-bundled')
    (root/'.env').write_text('DEEPGRAM_API_KEY=must-never-be-bundled')
    def run(*args):
        args = list(map(str,args))
        if 'PyInstaller' in args:
            assert args[-1].endswith('desktop_test_entry.py' if fixture else 'desktop_entry.py')
            name = args[args.index('--name')+1]
            target = work/'frozen'/name
            target.mkdir(parents=True)
            (target/name).write_bytes(b'fixture core')
        elif args[0] == 'xcrun':
            Path(args[args.index('-o')+1]).write_bytes(b'native UI')
        elif '--output' in args:
            Path(args[args.index('--output')+1]).write_bytes(b'helper')
    monkeypatch.setattr(build,'run',run)
    monkeypatch.setattr(build.subprocess,'check_output',lambda args,**kwargs:'27.2' if args[0]=='sw_vers' else 'dependencies')
    preview = work/'ScribeLoop Test.app'
    build.main(app=preview,fixture=fixture)
    assert sentinel.read_bytes() == b'existing production app'
    assert list(production.iterdir()) == [sentinel]
    assert not (preview/'Contents/Resources/transcription.json').exists()
    assert (preview/'Contents/Resources/Core/scribeloop-core').is_file()
    info = plistlib.loads((preview/'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier'] == ('ai.scribeloop.desktop.preview' if fixture else 'ai.scribeloop.desktop')
    assert info['LSMinimumSystemVersion'] == '13.0'
    assert (work/('preview-manifest.json' if fixture else 'desktop-manifest.json')).is_file()
    for path in preview.rglob('*'):
        if path.is_file(): assert b'must-never-be-bundled' not in path.read_bytes()
    with pytest.raises(ValueError,match='production'):
        build.main(app=production,fixture=True)


def test_package_requires_signing_or_explicit_unsigned_and_rejects_bundle_credentials(tmp_path):
    path = Path(__file__).resolve().parents[1]/'packaging/macos/package_internal.py'
    spec = importlib.util.spec_from_file_location('internal_packaging',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = tmp_path/'ScribeLoop.app'
    with pytest.raises(ValueError,match='identity'):
        module.package(app,tmp_path/'out')
    with pytest.raises(ValueError,match='combined'):
        module.package(app,tmp_path/'out',unsigned=True,profile='profile')
    resources = app/'Contents/Resources'
    resources.mkdir(parents=True)
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'ai.scribeloop.desktop'}))
    (resources/'transcription.json').write_text('{"api_key":"synthetic-secret"}')
    with pytest.raises(ValueError,match='prohibited'):
        module.validate_app(app)


def test_package_rejects_dependencies_newer_than_declared_macos(tmp_path,monkeypatch):
    path = Path(__file__).resolve().parents[1]/'packaging/macos/package_internal.py'
    spec = importlib.util.spec_from_file_location('compatibility_packaging',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.native_minimum('cmd LC_BUILD_VERSION\n minos 11.0\n sdk 27.0') == '11.0'
    assert module.native_minimum('cmd LC_VERSION_MIN_MACOSX\n version 10.15\n sdk 14.0') == '10.15'
    with pytest.raises(ValueError): module.native_minimum('cmd LC_UUID')
    app = tmp_path/'ScribeLoop.app'
    contents = app/'Contents'
    contents.mkdir(parents=True)
    info = {'CFBundleIdentifier':'ai.scribeloop.desktop','LSMinimumSystemVersion':'11.0'}
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    binary = contents/'Python'
    binary.write_bytes(b'\xcf\xfa\xed\xfe')
    def run(*args):
        return 'arm64' if args[0]=='lipo' else 'cmd LC_BUILD_VERSION\n minos 27.0\n sdk 27.0'
    monkeypatch.setattr(module,'run',run)
    with pytest.raises(ValueError,match='requires macOS 27.0'):
        module.validate_app(app)
    info['LSMinimumSystemVersion'] = '27.2'
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    assert module.validate_app(app)['LSMinimumSystemVersion'] == '27.2'
    def external(*args):
        if args[:2] == ('otool','-l'):
            return 'cmd LC_BUILD_VERSION\n minos 11.0\ncmd LC_RPATH\n path /opt/build/lib (offset 12)'
        return run(*args)
    monkeypatch.setattr(module,'run',external)
    with pytest.raises(ValueError,match='External runtime'):
        module.validate_app(app)
    def dependency(*args):
        if args[:2] == ('otool','-L'):
            return 'Python:\n /opt/build/lib.dylib (compatibility version 1.0.0)'
        return run(*args)
    monkeypatch.setattr(module,'run',dependency)
    with pytest.raises(ValueError,match='External native'):
        module.validate_app(app)
