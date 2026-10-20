"""Download Phase 0a bake-off models into ``models/`` (gitignored).

Usage:
    python scripts/download_models.py sherpa-streaming-zipformer-en

Sources are pinned to official release artifacts (ARCHITECTURE.md §11 rule:
record where every model came from; re-check links before shipping).
"""
from __future__ import annotations

import sys
import tarfile
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"

MODELS = {
    # English streaming zipformer — natively streaming ASR bake-off candidate
    # (Apache-2.0; see ARCHITECTURE.md §9 license chain).
    "sherpa-streaming-zipformer-en": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2"
    ),
}


def download(name: str) -> Path:
    url = MODELS.get(name)
    if not url:
        raise SystemExit(f"unknown model {name!r}; known: {sorted(MODELS)}")
    MODELS_DIR.mkdir(exist_ok=True)
    archive = MODELS_DIR / url.rsplit("/", 1)[1]
    print(f"downloading {url}")
    urllib.request.urlretrieve(url, archive)
    print("extracting ...")
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(MODELS_DIR, filter="data")
    except TypeError:  # Python < 3.11.4 has no filter kwarg
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(MODELS_DIR)
    archive.unlink()
    print(f"done -> {MODELS_DIR}")
    return MODELS_DIR


if __name__ == "__main__":
    download(sys.argv[1] if len(sys.argv) > 1 else "sherpa-streaming-zipformer-en")
