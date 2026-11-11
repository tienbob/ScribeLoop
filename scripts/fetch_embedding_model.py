"""Fetch the pinned, official local multilingual search model with checksums."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'models/granite-embedding-97m-multilingual-r2'
REVISION='835ad14087e140460703cf0fae09f97d469d65c2'
FILES={
    'model_quint8_avx2.onnx':('onnx/model_quint8_avx2.onnx','a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e'),
    'tokenizer.json':('tokenizer.json','4f2842d568e2724370aec203652a42ac783c7937f8347a1a2cc7506d71f1582f'),
    'config.json':('config.json','de948b0bdc6f356afad7a84b276d8dd7e7fe10fb9add1bb5e610621c28e41ebc'),
    'tokenizer_config.json':('tokenizer_config.json','6ed69389e30a8ecabfce2f9ebcdf0c908b34056f24d994340f2f216521c057d5'),
    'special_tokens_map.json':('special_tokens_map.json','013787ee251ff611722479197c00853b62113ad303cb0a36524231783c676c69'),
}


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    DEST.mkdir(parents=True,exist_ok=True)
    for name,(remote,expected) in FILES.items():
        target=DEST/name
        if target.is_file() and digest(target)==expected:
            continue
        if args.verify_only:
            raise ValueError(f'Missing or invalid pinned embedding asset: {name}; run scripts/fetch_embedding_model.py')
        temporary=target.with_suffix(target.suffix+'.download')
        url=f'https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/resolve/{REVISION}/{remote}'
        with urllib.request.urlopen(url,timeout=120) as response,temporary.open('wb') as output:
            shutil.copyfileobj(response,output)
        if digest(temporary)!=expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(f'Checksum mismatch for {name}')
        temporary.replace(target)
    print(DEST)


if __name__=='__main__':
    main()
