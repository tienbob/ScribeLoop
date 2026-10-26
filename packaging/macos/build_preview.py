"""Assemble a separate test-only app; never modify the production artifact."""
import plistlib
import shutil
from pathlib import Path

import build_desktop as build

ROOT = Path(__file__).resolve().parents[2]


def main():
    build.main()
    python=build.BUILD/'venv/bin/python'
    build.run(python,'-m','PyInstaller','--noconfirm','--onedir','--name','scribeloop-preview-core',
        '--distpath',build.BUILD/'frozen','--workpath',build.BUILD/'preview-freeze',
        '--specpath',build.BUILD,'--collect-all','sqlite_vec','--collect-all','sqlcipher3',
        '--hidden-import','uvicorn.protocols.websockets.websockets_impl',
        ROOT/'packaging/macos/desktop_test_entry.py')
    app=build.BUILD/'ScribeLoop Preview.app'
    if app.exists():
        shutil.rmtree(app)
    shutil.copytree(build.APP,app,symlinks=True)
    (app/'Contents/Resources/transcription.json').unlink(missing_ok=True)
    core=app/'Contents/Resources/Core'
    shutil.rmtree(core)
    shutil.copytree(build.BUILD/'frozen/scribeloop-preview-core',core,symlinks=True)
    (core/'scribeloop-preview-core').rename(core/'scribeloop-core')
    info=app/'Contents/Info.plist'
    value=plistlib.loads(info.read_bytes())
    value['CFBundleIdentifier']='ai.scribeloop.desktop.preview'
    value['CFBundleName']='ScribeLoop Preview'
    info.write_bytes(plistlib.dumps(value))
    build.run('codesign','--force','--sign','-',app)
    print(app)


if __name__=='__main__':
    main()
