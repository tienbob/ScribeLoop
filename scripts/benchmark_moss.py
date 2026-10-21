"""Local MOSS benchmark against AMI ES2004a; requires isolated MOSS dependencies.

Example: PYTHONPATH=data/moss-runtime:data/moss-source HF_HOME=data/moss-hf \
 .venv/bin/python scripts/benchmark_moss.py --start-s 300 --duration-s 300 \
 --device mps --output data/corpus-v1/moss/excerpt.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile

import soundfile as sf
import torch
from transformers import AutoModelForCausalLM, AutoProcessor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from moss_transcribe_diarize import parse_transcript
from moss_transcribe_diarize.inference_utils import (
    build_transcription_messages,
    generate_transcription,
)
from scribeloop_core.evaluation.codec import load_aligned_variant
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-s", type=float, default=None,
                        help="start offset (default: 300 s for development; 0 with --full)")
    interval = parser.add_mutually_exclusive_group()
    interval.add_argument("--duration-s", type=float, default=300,
                          help="audio duration in seconds (default: 300)")
    interval.add_argument("--full", action="store_true",
                          help="explicitly process the full recording; reserved for later validation")
    parser.add_argument("--audio-override", type=Path,
                        help="aligned codec copy of the selected AMI interval")
    parser.add_argument("--audio-provenance", type=Path,
                        help="matching prepare_codec.py timing/hash report")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="mps")
    parser.add_argument("--dtype", choices=["float32", "float16", "bfloat16"], default="float16")
    parser.add_argument("--attention", choices=["sdpa", "metal-flash"], default="sdpa")
    parser.add_argument("--quant-bits", type=int, choices=[0, 4, 8], default=0)
    parser.add_argument("--quant-scope", choices=["decoder", "all"], default="decoder")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--max-new-tokens", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.start_s is None:
        args.start_s = 0 if args.full else 300
    if bool(args.audio_override) != bool(args.audio_provenance):
        parser.error("--audio-override and --audio-provenance must be used together")
    if args.audio_override and args.full:
        parser.error("codec development tests must use a bounded interval")
    if args.output.exists():
        parser.error("output already exists")
    if args.attention == "metal-flash" and args.device != "mps":
        parser.error("Metal flash attention requires --device mps")
    if args.quant_bits and args.device != "mps":
        parser.error("Metal quantization requires --device mps")
    if args.start_s < 0 or (args.duration_s is not None and args.duration_s <= 0):
        parser.error("invalid audio interval")
    root = Path("data/corpus-v1")
    # Read only the requested interval, keeping development memory bounded.
    with sf.SoundFile(root / "upstream/ES2004a.Mix-Headset.wav") as source:
        sr = source.samplerate
        start = round(args.start_s * sr)
        end = (len(source) if args.full else
               min(len(source), start + round(args.duration_s * sr)))
        if start >= end:
            parser.error("audio interval is empty")
        source.seek(start)
        audio = source.read(frames=end - start, dtype="float32")
    end_s = end / sr
    args.output.parent.mkdir(parents=True, exist_ok=True)
    wav = args.output.with_suffix(".wav")
    sf.write(wav, audio, sr, subtype="PCM_16")
    codec_provenance = None
    if args.audio_override:
        try:
            audio, codec_provenance = load_aligned_variant(wav, args.audio_override, args.audio_provenance)
        except (TypeError, ValueError, OSError) as exc:
            parser.error(str(exc))
        sf.write(wav, audio, sr, subtype="PCM_16")
    words = []
    with ZipFile(root / "upstream/ami_public_manual_1.6.2.zip") as archive:
        for name in sorted(archive.namelist()):
            if name.startswith("words/ES2004a."):
                speaker = name.split(".")[1]
                for node in ET.fromstring(archive.read(name)):
                    if (node.tag == "w" and node.get("punc") != "true"
                            and (node.text or "").strip()
                            and args.start_s <= float(node.get("starttime"))
                            and float(node.get("endtime")) <= end_s):
                        words.append((float(node.get("starttime")), speaker,
                                      node.get("{http://nite.sourceforge.net/}id"), node.text.strip()))
    reference = " ".join(word[3] for word in sorted(words))
    args.output.with_suffix(".reference.txt").write_text(reference + "\n", encoding="utf-8")
    torch.set_num_threads(4)
    device = torch.device(args.device)
    dtype = getattr(torch, args.dtype)
    model_path = "models/MOSS-Transcribe-Diarize"
    print(f"Loading MOSS: {args.device}/{args.dtype}, {end_s - args.start_s:.2f}s audio", flush=True)
    loaded = time.perf_counter()
    loader_kwargs = {}
    if args.quant_bits:
        from transformers import MetalConfig

        exclusions = ["lm_head"]
        if args.quant_scope == "decoder":
            exclusions.extend(["model.whisper_encoder", "model.vq_adaptor"])
        loader_kwargs = {"device_map": "mps", "quantization_config": MetalConfig(
            bits=args.quant_bits, group_size=64, modules_to_not_convert=exclusions
        )}
    model = AutoModelForCausalLM.from_pretrained(
        model_path, trust_remote_code=True, local_files_only=True, dtype=dtype,
        attn_implementation=("sdpa" if args.attention == "sdpa" else
                             "kernels-community/metal-flash-sdpa"), **loader_kwargs,
    )
    if not args.quant_bits:
        model = model.to(device)
    model.eval()
    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True, local_files_only=True)
    load_s = time.perf_counter() - loaded
    print(f"Model ready in {load_s:.1f}s", flush=True)
    warmup_s = 0.0
    if args.warmup:
        warm_wav = args.output.with_suffix(".warmup.wav")
        sf.write(warm_wav, audio[:sr], sr, subtype="PCM_16")
        warm_started = time.perf_counter()
        generate_transcription(
            model, processor, build_transcription_messages(warm_wav), device=device, dtype=dtype,
            do_sample=False, max_new_tokens=1,
        )
        if device.type == "mps":
            torch.mps.synchronize()
        warmup_s = time.perf_counter() - warm_started
        print(f"Kernel warmup: {warmup_s:.1f}s", flush=True)
    started = time.perf_counter()

    def progress(tokens):
        if tokens % 128 == 0:
            print(f"Generated {tokens} tokens in {time.perf_counter() - started:.1f}s", flush=True)

    result = generate_transcription(
        model, processor, build_transcription_messages(wav), device=device, dtype=dtype,
        do_sample=False, max_new_tokens=args.max_new_tokens, token_callback=progress,
        input_callback=lambda count: print(f"Input tokens: {count}", flush=True),
    )
    if device.type == "mps":
        torch.mps.synchronize()
    elapsed = time.perf_counter() - started
    segments = [asdict(segment) for segment in parse_transcript(result["text"])]
    hypothesis = " ".join(segment["text"] for segment in segments)
    score = score_words(reference, hypothesis)
    report = {"model": "OpenMOSS-Team/MOSS-Transcribe-Diarize",
              "audio_condition": codec_provenance or {"codec": "original"},
              "reference_sha256": hashlib.sha256((reference + "\n").encode("utf-8")).hexdigest(),
              "model_provenance": json.loads(Path("data/moss-model-provenance.json").read_text()),
              "device": args.device, "dtype": args.dtype, "attention": args.attention,
              "effective_attention": {"text": model.model.language_model.config._attn_implementation,
                                      "audio": model.model.whisper_encoder.config._attn_implementation},
              "quant_bits": args.quant_bits, "quant_scope": args.quant_scope if args.quant_bits else None,
              "kernels_version": version("kernels") if args.quant_bits else None,
              "model_storage_bytes": model.get_memory_footprint(),
              "load_strategy": "direct-device" if args.quant_bits else "cpu-then-device",
              "warmup_s": warmup_s, "platform": platform.platform(),
              "torch": torch.__version__, "transformers": version("transformers"),
              "audio_start_s": args.start_s,
              "audio_s": (end - start) / sr, "load_s": load_s, "processing_s": elapsed,
              "speed_realtime": ((end - start) / sr) / elapsed,
              "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                    (1 if sys.platform == "darwin" else 1024),
              "mps_driver_allocated_bytes_at_end": torch.mps.driver_allocated_memory()
                    if device.type == "mps" else None,
              "normalization": NORMALIZATION, "word_errors": score.errors,
              "reference_words": score.reference_words, "wer": score.wer,
              "segments": segments, "raw_output": result["text"],
              "generated_tokens": result["generated_tokens"], "input_tokens": result["prompt_len"],
              "token_limit_reached": result["generated_tokens"] >= args.max_new_tokens,
              "generation_complete": bool(segments) and result["generated_tokens"] < args.max_new_tokens,
              "last_hypothesis_end_s": max((s["end"] for s in segments), default=None),
              "last_reference_word_start_s": max((w[0] for w in words), default=args.start_s) - args.start_s,
              "audio_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
              "limitations": ["Exploratory onset-serialized WER; not official AMI scoring.",
                               "Words crossing excerpt boundaries are excluded from the reference.",
                               "RSS excludes some GPU allocations; driver memory is an end snapshot, not a peak."]}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({k: report[k] for k in ("wer", "word_errors", "reference_words",
        "processing_s", "audio_s", "peak_process_rss_bytes", "token_limit_reached")}))

    if not report["generation_complete"]:
        raise SystemExit("Incomplete generation: token cap reached or no parseable transcript; reject this run.")


if __name__ == "__main__":
    main()
