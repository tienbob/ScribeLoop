"""Validate a local corpus or run an ASR throughput/WER baseline.

Fast replay measures throughput, not live partial/confirmed latency or DER.
Reports contain aggregate metrics and file hashes, never transcript text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.evaluation.corpus import load_corpus, readiness
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-ready", action="store_true",
                        help="fail unless the Phase 0b corpus composition is complete")
    parser.add_argument("--backend", choices=["faster-whisper", "sherpa-onnx"],
                        default="faster-whisper")
    parser.add_argument("--model", default="small")
    parser.add_argument("--model-dir")
    parser.add_argument("--language")
    parser.add_argument("--machine", help="machine identifier and relevant hardware specs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    corpus = load_corpus(args.manifest)
    status = readiness(corpus)
    if args.require_ready and not status["corpus_ready"]:
        parser.error(f"corpus is incomplete: {json.dumps(status)}")
    if args.validate_only:
        print(json.dumps(status, indent=2))
        return
    if not args.output or not args.machine:
        parser.error("baseline runs require --output and --machine")
    if args.output.exists():
        parser.error("output already exists; choose a new report path")

    from bakeoff import run_backend

    rows = []
    for recording in corpus.recordings:
        # Hash inputs before inference to describe the data used for this run.
        hashes = {field: digest(getattr(recording, field))
                  for field in ("audio", "reference", "speaker_labels")}
        chunks, elapsed, first, duration = run_backend(
            args.backend, recording.audio, args.model, args.model_dir, args.language
        )
        hypothesis = " ".join(chunk["text"] for chunk in chunks if chunk["is_final"])
        score = score_words(recording.reference.read_text(encoding="utf-8"), hypothesis)
        rows.append({"id": recording.id, "source": recording.source,
                     "variant_of": recording.variant_of, "codec": recording.codec,
                     "sha256": hashes, "word_errors": score.errors,
                     "reference_words": score.reference_words, "wer": score.wer,
                     "audio_s": duration, "processing_s": elapsed,
                     "speed_realtime": duration / elapsed if elapsed else None,
                     "first_chunk_processing_s": first if chunks else None})
    groups = {}
    for row in rows:
        key = f"{row['source']}/{'original' if row['variant_of'] is None else row['codec']}"
        group = groups.setdefault(key, {"recordings": 0, "word_errors": 0,
                                        "reference_words": 0, "audio_s": 0, "processing_s": 0})
        group["recordings"] += 1
        for field in ("word_errors", "reference_words", "audio_s", "processing_s"):
            group[field] += row[field]
    for group in groups.values():
        group["wer"] = (group["word_errors"] / group["reference_words"]
                        if group["reference_words"] else None)
        group["speed_realtime"] = group["audio_s"] / group["processing_s"]
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "normalization": NORMALIZATION, "manifest_sha256": digest(args.manifest),
              "machine": args.machine, "platform": platform.platform(),
              "python": platform.python_version(), "backend": args.backend,
              "backend_version": version(args.backend), "model": (args.model if args.backend == "faster-whisper" else
                        Path(args.model_dir).name if args.model_dir else
                        "sherpa-onnx-streaming-zipformer-en-2023-06-26"),
              "model_dir": args.model_dir, "language": args.language,
              "readiness": status, "recordings": rows, "groups": groups,
              "der": None, "live_latency": None, "targets_signed_off": False,
              "limitations": ["Fast replay excludes model load time and does not measure live latency.",
                              "DER and target sign-off remain pending.",
                              "Model names are recorded; weight revisions are not fingerprinted."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Saved baseline: {args.output}")


if __name__ == "__main__":
    main()
