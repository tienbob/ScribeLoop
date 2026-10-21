"""Controlled AMI ASR ablations. Diagnostic transcripts stay under ignored data/."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from scribeloop_core.capture.source import WavFileSource
from scribeloop_core.evaluation.corpus import load_corpus
from scribeloop_core.evaluation.metrics import score_words
from scribeloop_core.pipeline.asr import SherpaOnnxStreaming

CASES = ("original", "fp32", "endpoint-fresh", "endpoint-reset", "whisper-tiny-offline",
         "whisper-tiny-chunked", "whisper-tiny-offline-beam1", "whisper-tiny-offline-no-vad")


class LegacySherpa(SherpaOnnxStreaming):
    """Preserve the original fresh-stream endpoint behavior for ablations."""

    def feed(self, frame):
        self._ensure_stream(frame.t_ms)
        self._stream.accept_waveform(16000, frame.pcm.astype("float32"))
        end_ms = frame.t_ms + int(len(frame.pcm) / 16000 * 1000)
        self._last_end_ms = end_ms
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = (self._recognizer.get_result(self._stream) or "").strip()
        if self._recognizer.is_endpoint(self._stream):
            result = [self._chunk(text, end_ms, True)] if text else []
            self._stream = None
            return result
        if text and text != self._last_text:
            self._last_text = text
            return [self._chunk(text, end_ms, False)]
        return []


class ResetSherpa(LegacySherpa):
    """Ablation: reset decoder on the same stream, retaining buffered features."""

    def feed(self, frame):
        stream_before = self._stream
        result = super().feed(frame)
        if stream_before is not None and self._stream is None:
            self._recognizer.reset(stream_before)
            self._stream = stream_before
            self._utterance_start_ms = self._last_end_ms
            self._last_text = ""
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("data/corpus-v1/ami-es2004a.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/corpus-v1/investigation"))
    args = parser.parse_args()
    output = args.output_dir / f"{args.case}.json"
    if output.exists():
        parser.error(f"report already exists: {output}")
    recording = load_corpus(args.manifest).recordings[0]
    reference = recording.reference.read_text(encoding="utf-8")
    config = {}
    if args.case.startswith("whisper-"):
        from faster_whisper import WhisperModel

        model = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=2,
                             local_files_only=True)
        beam = 1 if args.case.endswith("beam1") or args.case.endswith("chunked") else 5
        vad = args.case == "whisper-tiny-offline"
        config = {"model": "tiny", "beam_size": beam, "vad_filter": vad,
                  "language": "en", "cpu_threads": 2, "compute_type": "int8"}
        started = time.perf_counter()
        if args.case.endswith("chunked"):
            from scribeloop_core.pipeline.asr import ChunkedFasterWhisper

            backend = ChunkedFasterWhisper.__new__(ChunkedFasterWhisper)
            backend._model = model
            backend._language = "en"
            backend._chunk_samples = 12 * 16000
            backend._buffer = np.empty(0, dtype=np.float32)
            backend._offset_ms = 0
            chunks = []
            source = WavFileSource(recording.audio)
            for frame in source.frames():
                chunks.extend(backend.feed(frame))
            chunks.extend(backend.flush())
            backend.close()
            source.close()
            config["chunk_s"] = 12
        else:
            segments, _ = model.transcribe(str(recording.audio), language="en", beam_size=beam,
                                           vad_filter=vad, word_timestamps=True)
            chunks = [{"text": seg.text, "start_ms": round(seg.start * 1000),
                       "end_ms": round(seg.end * 1000),
                       "words": [{"text": w.word, "start": w.start, "end": w.end}
                                 for w in seg.words]} for seg in segments]
        elapsed = time.perf_counter() - started
    else:
        import sherpa_onnx

        factory = sherpa_onnx.OnlineRecognizer.from_transducer
        model_dir = Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26")

        def create(**kwargs):
            # Reproduce the actual historical mixed-precision selection exactly,
            # independently of directory iteration order and production fixes.
            for part in ("encoder", "decoder", "joiner"):
                quantized = args.case == "original" and part == "joiner"
                kwargs[part] = str(next(
                    p for p in sorted(model_dir.glob(f"{part}-*.onnx"))
                    if p.name.endswith(".int8.onnx") == quantized
                ))
            kwargs["enable_endpoint_detection"] = args.case.startswith("endpoint-")
            config.update(kwargs)
            return factory(**kwargs)

        cls = ResetSherpa if args.case == "endpoint-reset" else LegacySherpa
        with patch.object(sherpa_onnx.OnlineRecognizer, "from_transducer", side_effect=create):
            backend = cls(model_dir)
        source = WavFileSource(recording.audio)
        chunks = []
        started = time.perf_counter()
        try:
            for frame in source.frames():
                chunks.extend(c for c in backend.feed(frame) if c["is_final"])
            chunks.extend(backend.flush())
            elapsed = time.perf_counter() - started
        finally:
            backend.close()
            source.close()
    hypothesis = " ".join(chunk["text"] for chunk in chunks)
    score = score_words(reference, hypothesis)
    report = {"case": args.case, "config": config, "processing_s": elapsed,
              "audio_s": sf.info(recording.audio).duration, "wer": score.wer,
              "word_errors": score.errors, "reference_words": score.reference_words,
              "final_chunks": len(chunks), "chunks": chunks,
              "model_sha256": {Path(config[part]).name:
                  hashlib.sha256(Path(config[part]).read_bytes()).hexdigest()
                  for part in ("encoder", "decoder", "joiner", "tokens") if part in config},
              "reference_sha256": hashlib.sha256(recording.reference.read_bytes()).hexdigest(),
              "audio_sha256": hashlib.sha256(recording.audio.read_bytes()).hexdigest()}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({key: report[key] for key in
                      ("case", "wer", "word_errors", "reference_words", "final_chunks", "processing_s")}))


if __name__ == "__main__":
    main()
