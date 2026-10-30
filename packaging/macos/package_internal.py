"""Package an existing credential-free app as an internal DMG.

Default mode requires Developer ID signing and notarization. --unsigned-internal
creates an explicitly labelled ad-hoc test package without uploading anything.
The source app and user workspace are never changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAGIC = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe',
         b'\xbe\xba\xfe\xca'}


def run(*args):
    return subprocess.run(list(map(str,args)), check=True, capture_output=True, text=True).stdout


def native_files(app):
    result = []
    for path in app.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                if stream.read(4) in MAGIC:
                    result.append(path)
    return sorted(result, key=lambda path:len(path.parts), reverse=True)


def version_tuple(value):
    if not re.fullmatch(r'\d+(?:\.\d+){0,2}',value):
        raise ValueError('Invalid macOS version')
    return tuple(map(int,value.split('.'))) + (0,)*(3-len(value.split('.')))


def native_minimum(load_commands):
    modern = re.search(r'\bcmd LC_BUILD_VERSION\b(?:(?!\bcmd ).)*?\bminos (\d+(?:\.\d+)*)',
                       load_commands,re.DOTALL)
    legacy = re.search(r'\bcmd LC_VERSION_MIN_MACOSX\b(?:(?!\bcmd ).)*?\bversion (\d+(?:\.\d+)*)',
                       load_commands,re.DOTALL)
    match = modern or legacy
    if not match:
        raise ValueError('Native binary has no recognized minimum macOS version')
    return match.group(1)


def runtime_paths(commands):
    return re.findall(r'\bcmd LC_RPATH\b(?:(?!\bcmd ).)*?\bpath (.*?) \(offset',commands,re.DOTALL)


def external_path(value):
    return value.startswith('/') and not value.startswith(('/usr/lib/','/System/Library/'))


def validate_app(app):
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'ai.scribeloop.desktop':
        raise ValueError('Only the production ScribeLoop app can be packaged')
    for path in app.rglob('*'):
        if path.name in {'.env','transcription.json'} or path.suffix in {
                '.sqlcipher','.slpa','.wav','.flac','.gguf','.safetensors'}:
            raise ValueError('Bundle contains prohibited credentials or runtime data')
    for path in native_files(app):
        if run('lipo','-archs',path).strip() != 'arm64':
            raise ValueError(f'Expected Apple Silicon-only binary: {path.name}')
        commands = run('otool','-l',path)
        minimum = native_minimum(commands)
        if version_tuple(minimum) > version_tuple(info['LSMinimumSystemVersion']):
            raise ValueError(f'{path.relative_to(app)} requires macOS {minimum}, newer than '
                             f"the declared {info['LSMinimumSystemVersion']}")
        if any(external_path(value) for value in runtime_paths(commands)):
            raise ValueError(f'External runtime search path: {path.name}')
        for line in run('otool','-L',path).splitlines()[1:]:
            if external_path(line.strip().split(' (')[0]):
                raise ValueError(f'External native dependency: {path.name}')
    run('codesign','--verify','--deep','--strict',app)
    return info


def sign_app(app, identity):
    if not identity.startswith('Developer ID Application:'):
        raise ValueError('A Developer ID Application identity is required')
    # Sign from the inside out, including every frozen extension and framework.
    flags = ['codesign','--force','--timestamp','--options','runtime','--sign',identity]
    entitlements = ROOT/'packaging/macos/audio.entitlements'
    for path in native_files(app):
        extra = ['--entitlements',entitlements] if path.parent.name=='Helpers' else []
        run(*flags,*extra,path)
    bundles = [p for p in app.rglob('*') if p.is_dir() and not p.is_symlink()
               and p.suffix in {'.framework','.app','.xpc','.bundle'}]
    for path in sorted(bundles,key=lambda path:len(path.parts),reverse=True):
        run(*flags,path)
    run(*flags,'--entitlements',entitlements,app)
    run('codesign','--verify','--deep','--strict',app)


def package(app, output, *, unsigned=False, identity=None, profile=None):
    app, output = Path(app).resolve(), Path(output).resolve()
    if not unsigned and (not identity or not profile):
        raise ValueError('Provide --identity and --notary-profile, or explicitly use --unsigned-internal')
    if unsigned and (identity or profile):
        raise ValueError('Unsigned mode cannot be combined with signing credentials')
    info = validate_app(app)
    output.mkdir(parents=True,exist_ok=True)
    label = 'internal-unsigned' if unsigned else 'internal'
    stem = f"ScribeLoop-{info['CFBundleShortVersionString']}-{info['CFBundleVersion']}-arm64-{label}"
    dmg = output/f'{stem}.dmg'
    report_path = output/f'{stem}.json'
    if dmg.exists() or report_path.exists():
        raise FileExistsError('Output already exists; use a new output directory or build number')
    with tempfile.TemporaryDirectory(prefix='scribeloop-package-') as tmp:
        work = Path(tmp)
        stage = work/'image'
        stage.mkdir()
        copy = stage/'ScribeLoop.app'
        run('ditto',app,copy)
        if not unsigned:
            sign_app(copy,identity)
            # Staple the app too, so it retains its ticket after being copied off the DMG.
            archive = work/'ScribeLoop.zip'
            run('ditto','-c','-k','--keepParent',copy,archive)
            status = json.loads(run('xcrun','notarytool','submit',archive,
                '--keychain-profile',profile,'--wait','--output-format','json'))
            if status.get('status') != 'Accepted':
                raise RuntimeError('App notarization was not accepted; inspect the notarytool submission log')
            run('xcrun','stapler','staple',copy)
            run('xcrun','stapler','validate',copy)
            run('spctl','--assess','--type','execute','--verbose=2',copy)
        (stage/'Applications').symlink_to('/Applications',target_is_directory=True)
        guide = (ROOT/'packaging/macos/INTERNAL_INSTALL.txt').read_text()
        (stage/'READ ME.txt').write_text(guide +
            f"\nThis package: Apple Silicon, macOS {info['LSMinimumSystemVersion']} or later.\n"+
            ('AD-HOC TEST BUILD: not Developer ID signed or notarized.\n' if unsigned
             else 'Developer ID signed and notarized.\n'))
        image = work/f'{stem}.dmg'
        run('hdiutil','create','-volname','ScribeLoop','-srcfolder',stage,
            '-format','UDZO','-ov',image)
        if not unsigned:
            run('codesign','--timestamp','--sign',identity,image)
            status = json.loads(run('xcrun','notarytool','submit',image,
                '--keychain-profile',profile,'--wait','--output-format','json'))
            if status.get('status') != 'Accepted':
                raise RuntimeError('DMG notarization was not accepted')
            run('xcrun','stapler','staple',image)
            run('xcrun','stapler','validate',image)
            run('spctl','--assess','--type','open','--context','context:primary-signature',image)
        run('hdiutil','verify',image)
        shutil.copy2(image,dmg)
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    report = {'file':dmg.name,'sha256':digest,'architecture':'arm64',
        'minimum_macos':info['LSMinimumSystemVersion'],
        'version':info['CFBundleShortVersionString'],'build':info['CFBundleVersion'],
        'developer_id_signed':not unsigned,'notarized':not unsigned,
        'bundled_api_credentials':False}
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    dmg.with_suffix('.sha256').write_text(f'{digest}  {dmg.name}\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,default=ROOT/'dist/macos/ScribeLoop.app')
    parser.add_argument('--output',type=Path,default=ROOT/'dist/macos/internal')
    parser.add_argument('--unsigned-internal',action='store_true')
    parser.add_argument('--identity')
    parser.add_argument('--notary-profile')
    args = parser.parse_args()
    package(args.app,args.output,unsigned=args.unsigned_internal,
            identity=args.identity,profile=args.notary_profile)
