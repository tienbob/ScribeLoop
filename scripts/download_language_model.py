"""Explicitly install the pinned local language-only detector (no audio inputs)."""
from pathlib import Path

from huggingface_hub import snapshot_download

REPO = 'Systran/faster-whisper-small'
REVISION = '536b0662742c02347bc0e980a01041f333bce120'
FILES = ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt']


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'models/faster-whisper-small'
    snapshot_download(REPO, revision=REVISION, local_dir=folder, allow_patterns=FILES)
    if any(not (folder / name).is_file() for name in FILES):
        raise RuntimeError('Language model is incomplete; no automatic fallback is permitted')
    print('Pinned language-only detector ready locally')


if __name__ == '__main__':
    main()
