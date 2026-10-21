"""Inspect speaker-count undercoverage and lexical evidence without rerunning ML."""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.evaluation.diarization import evaluate, review_regions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    from pyannote.core import Annotation, Segment

    report = json.loads(args.report.read_text())
    start, duration = report["audio_start_s"], report["audio_s"]
    if not 0 < duration <= 300:
        parser.error("development review is limited to five minutes")
    reference, hypothesis = [], []
    ref_annotation, hyp_annotation = Annotation(), Annotation()
    for i, turn in enumerate(json.loads(Path("data/corpus-v1/ES2004a.speakers.json").read_text())):
        a, b = max(0, turn["start_ms"] / 1000 - start), min(duration, turn["end_ms"] / 1000 - start)
        if b > a:
            reference.append({"start": a, "end": b, "speaker": turn["speaker"]})
            ref_annotation[Segment(a, b), i] = turn["speaker"]
    for i, turn in enumerate(report["segments"]):
        a, b = max(0, turn["start"]), min(duration, turn["end"])
        if b > a:
            hypothesis.append({"start": a, "end": b, "speaker": turn["speaker"]})
            hyp_annotation[Segment(a, b), i] = turn["speaker"]
    score, mapping, uem = evaluate(ref_annotation, hyp_annotation, duration)
    words = []
    with ZipFile("data/corpus-v1/upstream/ami_public_manual_1.6.2.zip") as archive:
        for name in sorted(archive.namelist()):
            if name.startswith("words/ES2004a."):
                for node in ET.fromstring(archive.read(name)):
                    if node.tag == "w" and node.get("punc") != "true" and (node.text or "").strip():
                        a, b = float(node.get("starttime")) - start, float(node.get("endtime")) - start
                        if a < duration and b > 0:
                            words.append({"start": max(0, a), "end": min(duration, b),
                                          "speaker": name.split(".")[1]})
    review = review_regions(reference, hypothesis, mapping, uem, words)
    totals = review["totals"]
    # Cross-check independent timeline accounting against the scoring library.
    for actual, expected in [("missed_speaker_s", "missed detection"), ("confusion_s", "confusion"),
                             ("false_alarm_s", "false alarm"), ("reference_speaker_s", "total")]:
        if abs(totals[actual] - score["der"][expected]) > 1e-6:
            raise RuntimeError(f"review accounting disagrees with scorer: {actual}")
    review.update({"source_report": str(args.report), "audio_start_s": start, "score": score,
                   "speaker_mapping": mapping,
                   "limitations": ["Automatic lexical-timing review, not a human listening review.",
                                    "Lexical absence is not proof of silence: nonlexical vocalizations may occur.",
                                    "Reference transcription segments are diagnostic, not official AMI DER labels."]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Short listening clips for the highest-priority distinct error intervals.
    import soundfile as sf

    audio, sr = sf.read(args.report.with_suffix(".wav"), dtype="float32")
    windows = []
    for row in review["review_priority"]:
        a, b = max(0, row["start"] - 2), min(duration, row["end"] + 2)
        if any(abs(a - item["start"]) < 3 for item in windows):
            continue
        clip = args.output.with_name(f"{args.output.stem}-review-{len(windows)+1}.wav")
        sf.write(clip, audio[round(a*sr):round(b*sr)], sr, subtype="PCM_16")
        windows.append({"start": a, "end": b, "ami_start_s": start + a, "path": str(clip)})
        if len(windows) == 5:
            break
    review["listening_clips"] = windows
    with args.output.open("x") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
    print(json.dumps({"totals": totals, "mapping": mapping, "clips": windows}, indent=2))


if __name__ == "__main__":
    main()
