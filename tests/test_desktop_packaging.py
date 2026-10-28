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


def test_preview_build_is_isolated_from_production_and_operator_key(tmp_path,monkeypatch):
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
    monkeypatch.setattr(build,'APP',production)
    def no_key(): raise AssertionError('Preview must never load an operator key')
    monkeypatch.setattr(build,'transcription_key',no_key)
    def run(*args):
        args = list(map(str,args))
        if 'PyInstaller' in args:
            assert args[-1].endswith('desktop_test_entry.py')
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
    preview = work/'ScribeLoop Preview.app'
    build.main(app=preview,fixture=True)
    assert sentinel.read_bytes() == b'existing production app'
    assert list(production.iterdir()) == [sentinel]
    assert not (preview/'Contents/Resources/transcription.json').exists()
    assert (preview/'Contents/Resources/Core/scribeloop-core').is_file()
    info = plistlib.loads((preview/'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier'] == 'ai.scribeloop.desktop.preview'
    assert (work/'preview-manifest.json').is_file()
    assert not (work/'desktop-manifest.json').exists()
    with pytest.raises(ValueError,match='production'):
        build.main(app=production,fixture=True)
