"""Phase 0a ASR bake-off harness (ARCHITECTURE.md §8 "0a — Spike").

Usage:
    python scripts/bakeoff.py --wav meeting.wav --backend faster-whisper \
        --model small --reference transcript.txt

Measures, per backend (§7 provisional targets):
- throughput: audio duration vs wall time (offline pass speed)
- WER vs an optional ground-truth transcript (Whisper-style normalization,
  §7 scoring protocol)
- chunk cadence: how often confirmed text is emitted

The WAV replay uses WavFileSource (fast mode) — platform-independent, so the
harness runs on any dev machine.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.capture.source import WavFileSource  # noqa: E402
from scribeloop_core.pipeline.asr import available_backends  # noqa: E402


# --- §7 scoring protocol: Whisper-style text normalization ----------------

def normalize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate via word-level Levenshtein (substitutions count 1)."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1,          # deletion
                         cur[j - 1] + 1,       # insertion
                         prev[j - 1] + (r != h))  # substitution
        prev = cur
    return prev[-1] / len(ref)


# --- runner ----------------------------------------------------------------

def run_backend(
    name: str, wav: Path, model: str, model_dir: str | None, language: str | None
) -> tuple[list[dict], float, float, float]:
    """Returns (chunks, elapsed_s, first_chunk_s, audio_duration_s)."""
    from scribeloop_core.pipeline.asr import ChunkedFasterWhisper, SherpaOnnxStreaming

    if name == "faster-whisper":
        backend = ChunkedFasterWhisper(model_size=model, language=language or None)
    elif name == "sherpa-onnx":
        backend = SherpaOnnxStreaming(model_dir=model_dir or _default_sherpa_dir())
    else:
        raise SystemExit(f"backend {name!r} not wired yet (available: {available_backends()})")

    source = WavFileSource(wav)
    chunks: list[dict] = []
    started = time.perf_counter()
    first_ts: float | None = None
    audio_s = 0.0
    for frame in source.frames():
        audio_s = (frame.t_ms + 100) / 1000.0  # last frame's end = file duration
        out = backend.feed(frame)
        if out and first_ts is None:
            first_ts = time.perf_counter() - started
        chunks.extend(out)
    tail = backend.flush()
    if tail and first_ts is None:
        first_ts = time.perf_counter() - started
    chunks.extend(tail)
    elapsed = time.perf_counter() - started
    backend.close()
    return chunks, elapsed, (first_ts or 0.0), audio_s


def _default_sherpa_dir() -> str:
    root = Path(__file__).resolve().parents[1]
    return str(root / "models" / "sherpa-onnx-streaming-zipformer-en-2023-06-26")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--backend", default="faster-whisper", choices=available_backends())
    parser.add_argument("--model", default="small", help="whisper model size (faster-whisper)")
    parser.add_argument(
        "--model-dir", default=None,
        help="sherpa-onnx model dir (default: models/sherpa-onnx-streaming-zipformer-en-2023-06-26)",
    )
    parser.add_argument("--language", default=None)
    parser.add_argument("--reference", type=Path, default=None,
                        help="ground-truth transcript for WER (§7 scoring protocol)")
    args = parser.parse_args()

    if not args.wav.exists():
        raise SystemExit(f"no such file: {args.wav}")

    chunks, elapsed, first_ts, audio_s = run_backend(
        args.backend, args.wav, args.model, args.model_dir, args.language
    )
    finals = [c for c in chunks if c["is_final"]]
    partials = [c for c in chunks if not c["is_final"]]
    text = " ".join(c["text"] for c in finals)

    print(f"backend:        {args.backend} ({args.model if args.backend == 'faster-whisper' else args.model_dir or 'default model dir'})")
    print(f"audio duration: {audio_s:.1f} s")
    print(f"wall time:      {elapsed:.1f} s  ->  {audio_s / max(elapsed, 1e-9):.2f}x realtime")
    print(f"first chunk:    {first_ts:.2f} s after start")
    print(f"finals:         {len(finals)}   partials: {len(partials)}")
    if args.reference and args.reference.exists():
        print(f"WER (finals):   {wer(args.reference.read_text(encoding='utf-8'), text):.1%}")
    print("--- transcript (finals) ---")
    print(text[:600])


if __name__ == "__main__":
    main()
