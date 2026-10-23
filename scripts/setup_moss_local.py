"""Install pinned transcribe.cpp and verify Q6_K weights; never processes audio."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REVISION = '4d7c1ccefd39e5a9ea36cca5f8fefc6dcc14b99b'
MODEL_REVISION = '8c603aa6da33a068e39bbd109786f85786a21834'
MODEL_SHA256 = '7fd37409dbec9c732f912717f3cd8f4cd2c5083841edfab2f3b670f816512b44'


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            checksum.update(block)
    return checksum.hexdigest()


def main():
    runtime = ROOT/'models/transcribe.cpp'
    cmake = shutil.which('cmake') or str(Path(sys.executable).parent/'cmake')
    if not Path(cmake).is_file():
        raise SystemExit('Install CMake first: uv pip install --python .venv/bin/python cmake')
    if not runtime.exists():
        subprocess.run(['git','clone','--no-checkout','https://github.com/handy-computer/transcribe.cpp.git',str(runtime)],check=True)
        subprocess.run(['git','-C',str(runtime),'checkout',RUNTIME_REVISION],check=True)
        subprocess.run(['git','-C',str(runtime),'submodule','update','--init','--recursive'],check=True)
    revision = subprocess.check_output(['git','-C',str(runtime),'rev-parse','HEAD'],text=True).strip()
    if revision != RUNTIME_REVISION:
        raise SystemExit('Existing runtime revision differs; preserve it and install the pinned revision separately.')
    subprocess.run([cmake,'-S',str(runtime),'-B',str(runtime/'build'),
        '-DCMAKE_BUILD_TYPE=Release','-DTRANSCRIBE_BUILD_SHARED=ON','-DTRANSCRIBE_BUILD_TESTS=OFF'],check=True)
    subprocess.run([cmake,'--build',str(runtime/'build'),'--target','transcribe','-j','4'],check=True)
    model = ROOT/'models/MOSS-Transcribe-Diarize-Q6_K.gguf'
    if not model.exists():
        part = model.with_suffix('.gguf.part')
        url = f'https://huggingface.co/handy-computer/MOSS-Transcribe-Diarize-gguf/resolve/{MODEL_REVISION}/{model.name}'
        print('Downloading 768 MB Q6_K weights…',flush=True)
        with urlopen(url,timeout=60) as source, part.open('wb') as target:
            shutil.copyfileobj(source,target)
        if digest(part) != MODEL_SHA256:
            raise SystemExit('Checksum failed; downloaded file was not activated.')
        part.replace(model)
    if digest(model) != MODEL_SHA256:
        raise SystemExit('Model checksum failed. Local model was not verified.')
    print('Local MOSS Q6_K ready. Restart ScribeLoop, then select Local · MOSS Q6_K.')


if __name__ == '__main__':
    main()
