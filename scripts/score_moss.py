"""Score a local MOSS report: diagnostic DER and optional matched Whisper WER.

Uses imported AMI transcription-segment labels, not an official DER recipe.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.evaluation.diarization import evaluate
from scribeloop_core.evaluation.metrics import score_words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, help="alternate path for rescoring")
    parser.add_argument("--whisper", action="store_true")
    args = parser.parse_args()
    output = args.output or args.report.with_suffix(".scores.json")
    if output.exists():
        parser.error("scores already exist")
    from pyannote.core import Annotation, Segment

    report = json.loads(args.report.read_text())
    turns = json.loads(Path("data/corpus-v1/ES2004a.speakers.json").read_text())
    ref, hyp = Annotation(), Annotation()
    start, duration = report["audio_start_s"], report["audio_s"]
    for i, turn in enumerate(turns):
        a, b = max(0, turn["start_ms"] / 1000 - start), min(duration, turn["end_ms"] / 1000 - start)
        if b > a:
            ref[Segment(a, b), i] = turn["speaker"]
    invalid = 0
    for i, turn in enumerate(report["segments"]):
        if not 0 <= turn["start"] < turn["end"] <= duration:
            invalid += 1
        a, b = max(0, turn["start"]), min(duration, turn["end"])
        if b > a:
            hyp[Segment(a, b), i] = turn["speaker"]
    result, _, _ = evaluate(ref, hyp, duration)
    result.update({"moss_wer": report.get("wer"),
                   "invalid_or_out_of_bounds_segments": invalid,
                   "limitations": ["Diagnostic transcription-segment labels, not official AMI scoring.",
                                    "Invalid timestamps clipped to recording bounds for DER."]})
    if args.whisper:
        from faster_whisper import WhisperModel

        model = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=2,
                             local_files_only=True)
        chunks, _ = model.transcribe(str(args.report.with_suffix(".wav")), language="en",
                                     beam_size=5, vad_filter=True)
        hypothesis = " ".join(chunk.text for chunk in chunks)
        reference = args.report.with_suffix(".reference.txt").read_text()
        score = score_words(reference, hypothesis)
        result["whisper_tiny_matched"] = {"wer": score.wer, "word_errors": score.errors,
                                          "reference_words": score.reference_words,
                                          "beam_size": 5, "vad_filter": True}
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
