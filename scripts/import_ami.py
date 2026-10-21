"""Import the pinned ES2004a mixed-headset meeting and AMI manual annotations.

Download the two URLs in docs/PHASE_0B.md first. All corpus data stays gitignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.evaluation.ami import convert_annotations
from scribeloop_core.evaluation.corpus import load_corpus

REVISION = "722d8891643e1e4dc62cfd0d198fa05a1646c3cc"
BASE = f"https://huggingface.co/datasets/FluidInference/ami-corpus-mirror/resolve/{REVISION}"
FILES = {
    "ami_public_manual_1.6.2.zip": (
        "annotations/ami_public_manual_1.6.2.zip",
        "56e29112cd89cfba76fbf8f10215eb245cc89b0d3d27355ba14b406a7ea7776c"),
    "ES2004a.Mix-Headset.wav": (
        "sdm/ES2004a.Mix-Headset.wav",
        "3e2560b19bee6952c7c7ce041b0f1ea8a7ea9468044c4eea79d2a2c67e24ab0f"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/corpus-v1"))
    args = parser.parse_args()
    root = args.root
    manifest = root / "ami-es2004a.json"
    if manifest.exists():
        parser.error("manifest exists; choose a new root to avoid overwriting a corpus")
    for name, (_, expected) in FILES.items():
        path = root / "upstream" / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Checksum mismatch: {path}")
    with ZipFile(root / "upstream" / "ami_public_manual_1.6.2.zip") as archive:
        reference, turns = convert_annotations(archive, "ES2004a")
        (root / "AMI-LICENCE.txt").write_bytes(archive.read("LICENCE.txt"))
    (root / "ES2004a.txt").write_text(reference + "\n", encoding="utf-8")
    (root / "ES2004a.speakers.json").write_text(json.dumps(turns, indent=2) + "\n")
    provenance = {
        "dataset": "AMI Meeting Corpus", "meeting": "ES2004a",
        "audio_condition": "mixed headset (not single distant microphone)",
        "annotations": "AMI public manual 1.6.2", "mirror_revision": REVISION,
        "upstream_homepage": "https://groups.inf.ed.ac.uk/ami/corpus/",
        "attribution": "AMI Consortium; Carletta et al., The AMI Meeting Corpus: A Pre-announcement (2005)",
        "files": {name: {"url": f"{BASE}/{path}", "sha256": checksum}
                  for name, (path, checksum) in FILES.items()},
        "reference_protocol": "Lexical words ordered by onset; punctuation and nonlexical vocal sounds excluded",
        "speaker_protocol": "Manual transcription segments containing lexical words; overlaps preserved",
        "limitations": ["Exploratory baseline; not an official AMI scoring recipe.",
                        "Overlap serialized by word onset for single-stream WER.",
                        "One meeting cannot establish corpus-wide target performance."]}
    (root / "ES2004a.provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    manifest.write_text(json.dumps({"version": 1, "recordings": [{
        "id": "ami-ES2004a-mix-headset", "source": "ami",
        "audio": "upstream/ES2004a.Mix-Headset.wav", "reference": "ES2004a.txt",
        "speaker_labels": "ES2004a.speakers.json",
        "provenance": "AMI manual 1.6.2 / mixed headset; see ES2004a.provenance.json and AMI-LICENCE.txt"
    }]}, indent=2) + "\n")
    corpus = load_corpus(manifest)
    print(f"Imported {manifest}: {len(reference.split())} reference tokens, "
          f"{len(turns)} speaker turns, {len({t['speaker'] for t in turns})} speakers")
    assert len(corpus.recordings) == 1


if __name__ == "__main__":
    main()
