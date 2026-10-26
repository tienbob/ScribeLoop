"""Inspect the full app without opening user storage, Keychain or providers."""
import json
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT/'dist/macos/ScribeLoop.app'


def main():
    info=plistlib.loads((APP/'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier']=='ai.scribeloop.desktop'
    assert info['NSMicrophoneUsageDescription']
    assert not info['NSAppTransportSecurity'].get('NSAllowsArbitraryLoads',False)
    native=0
    total=0
    for path in APP.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        total+=path.stat().st_size
        assert path.name!='.env' and path.suffix not in {'.sqlcipher','.slpa','.wav','.flac','.gguf','.safetensors'}
        assert path.name not in {'torch','faster_whisper','pyannote','ctranslate2','llama_cpp'}
        with path.open('rb') as stream:
            magic=stream.read(4)
        if magic not in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe'):
            continue
        native+=1
        linked=subprocess.check_output(['otool','-L',str(path)],text=True)
        for line in linked.splitlines()[1:]:
            dependency=line.strip().split(' (')[0]
            if dependency.startswith('/') and not dependency.startswith(('/usr/lib/','/System/Library/')):
                raise RuntimeError(f'External native dependency: {path.name}: {dependency}')
        rpaths=subprocess.check_output(['otool','-l',str(path)],text=True)
        lines=rpaths.splitlines()
        for index,line in enumerate(lines):
            if 'cmd LC_RPATH' in line:
                value=lines[index+2].strip().split(' ')[1]
                if value.startswith('/') and not value.startswith(('/usr/lib/','/System/Library/')):
                    raise RuntimeError(f'External runtime path: {path.name}')
    subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True)
    report={'ok':True,'native_binaries':native,'bundle_bytes':total,'external_dependencies':0,
            'credentials_or_recordings_bundled':False,'release_signed':False,
            'minimum_os':info['LSMinimumSystemVersion']}
    (ROOT/'build/macos/desktop-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
