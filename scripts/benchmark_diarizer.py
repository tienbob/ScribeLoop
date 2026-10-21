"""Compare community-1 with a saved five-minute MOSS run using identical audio.

Requires accepted Hugging Face model conditions and local `hf auth login`.
Reference speaker counts/labels are never passed to the inference pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-report", type=Path,
                        default=Path("data/corpus-v1/moss/segment-300-600-fp16-sdpa.json"))
    parser.add_argument("--revision", help="pinned model revision (allows cached offline reruns)")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    import soundfile as sf
    import torch
    from huggingface_hub import HfApi, get_token
    from pyannote.audio import Pipeline

    token = get_token()
    if not token:
        parser.error("accept community-1 model conditions and run hf auth login locally first")
    parent = json.loads(args.reference_report.read_text())
    audio_path = args.reference_report.with_suffix(".wav")
    if not 0 < parent["audio_s"] <= 300:
        parser.error("development comparison is limited to five minutes")
    audio_hash = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    if audio_hash != parent["audio_sha256"]:
        parser.error("audio no longer matches the saved MOSS benchmark")
    info = sf.info(audio_path)
    if (info.channels != 1 or not 0 < info.duration <= 300
            or abs(info.duration - parent["audio_s"]) > 1 / info.samplerate):
        parser.error("audio format/duration does not match the bounded source report")
    waveform, sr = sf.read(audio_path, dtype="float32")
    model_id = "pyannote/speaker-diarization-community-1"
    revision = args.revision or HfApi().model_info(model_id, token=token).sha
    torch.set_num_threads(4)
    print(f"Loading {model_id}@{revision} on {args.device}", flush=True)
    started = time.perf_counter()
    pipeline = Pipeline.from_pretrained(model_id, revision=revision, token=token,
                                        cache_dir="models/pyannote-cache")
    if pipeline is None:
        raise RuntimeError("pipeline unavailable: check model access and local dependencies")
    pipeline.to(torch.device(args.device))
    load_s = time.perf_counter() - started
    started = time.perf_counter()
    output = pipeline({"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": sr})
    if args.device == "mps":
        torch.mps.synchronize()
    elif args.device == "cuda":
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    segments = [{"start": turn.start, "end": turn.end, "speaker": speaker}
                for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True)]
    exclusive = [{"start": turn.start, "end": turn.end, "speaker": speaker}
                 for turn, _, speaker in output.exclusive_speaker_diarization.itertracks(yield_label=True)]
    report = {"model": model_id, "revision": revision, "device": args.device,
              "precision": "upstream pipeline default (float32); MOSS defaults unchanged",
              "audio_start_s": parent["audio_start_s"], "audio_s": parent["audio_s"],
              "audio_sha256": audio_hash, "audio_condition": parent.get("audio_condition"),
              "load_s": load_s, "processing_s": elapsed, "segments": segments,
              "exclusive_segments": exclusive, "reference_speaker_count_provided": False,
              "limitations": ["Diarization only; no ASR quality or word-attribution claim.",
                               "Use regular overlapping output for DER; exclusive output is for later alignment."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    # Scoring tools expect the co-located audio used for the inference run.
    import shutil

    shutil.copyfile(audio_path, args.output.with_suffix(".wav"))
    print(json.dumps({"output": str(args.output), "segments": len(segments),
                      "speakers": len({s["speaker"] for s in segments}), "processing_s": elapsed}))


if __name__ == "__main__":
    main()
