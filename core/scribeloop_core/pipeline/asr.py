"""Streaming ASR backends (ARCHITECTURE.md §2 Transcription; Phase 0a bake-off).

Contract: a backend consumes ``AudioFrame`` objects and emits
``TranscriptChunk`` bus events — partial (``is_final=False``, ephemeral,
§3.1) and confirmed text.

Phase 0a bake-off participants (§8 0a):
- ``ChunkedFasterWhisper`` — faster-whisper (CTranslate2; CPU/CUDA; CPU-only
  on macOS — no Metal backend), chunked decode. LocalAgreement confirmation
  policy is the Phase 0a TODO below.
- sherpa-onnx streaming Zipformer — natively streaming (optional dep).
- whisper.cpp / Moonshine — measured from a macOS/Linux host or separate
  harness (whisper.cpp is a C++ build; Moonshine is a separate extra).

Lag-cap policy (§2.3) applies at the *pipeline* level, not here: the runner
downshifts model size (Whisper family) or pauses and defers to the offline
pass (native streaming), with upshift hysteresis.
"""
from __future__ import annotations

import numpy as np

from ..capture.source import SAMPLE_RATE, AudioFrame


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------

class StreamingASR:
    """Interface every backend implements."""

    name = "abstract"

    def feed(self, frame: AudioFrame) -> list[dict]:
        """Ingest one frame; return list of {'text','start_ms','end_ms','is_final', ...}."""
        raise NotImplementedError

    def flush(self) -> list[dict]:
        """Drain any buffered audio at end of session/chunk."""
        raise NotImplementedError

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------
# faster-whisper — chunked decode baseline
# --------------------------------------------------------------------------

class ChunkedFasterWhisper(StreamingASR):
    """Chunked-decode baseline for the bake-off.

    Buffers frames and transcribes when the buffer reaches ``chunk_s`` (or on
    ``flush``). Emits only ``is_final=True`` text at this stage — the
    LocalAgreement partial/confirm policy (consecutive decodes must agree
    before text is confirmed) is the Phase 0a TODO that makes this live-usable.
    """

    name = "faster-whisper"

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        chunk_s: float = 12.0,
    ) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._language = language
        self._chunk_samples = int(chunk_s * SAMPLE_RATE)
        self._buffer = np.empty(0, dtype=np.float32)
        self._offset_ms = 0

    def feed(self, frame: AudioFrame) -> list[dict]:
        self._buffer = np.concatenate([self._buffer, frame.pcm])
        if len(self._buffer) >= self._chunk_samples:
            return self._drain()
        return []

    def flush(self) -> list[dict]:
        if len(self._buffer) < SAMPLE_RATE // 2:  # <0.5 s — not worth a decode
            return []
        return self._drain()

    def _drain(self) -> list[dict]:
        segments, info = self._model.transcribe(
            self._buffer,
            language=self._language,
            beam_size=1,        # latency over accuracy for the live path
            vad_filter=False,  # VAD already gates upstream
        )
        out: list[dict] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append(
                    {
                        "text": text,
                        "start_ms": self._offset_ms + int(seg.start * 1000),
                        "end_ms": self._offset_ms + int(seg.end * 1000),
                        "is_final": True,
                        "language": info.language,
                        "confidence": seg.avg_logprob,
                    }
                )
        self._offset_ms += int(len(self._buffer) / SAMPLE_RATE * 1000)
        self._buffer = np.empty(0, dtype=np.float32)
        return out


# --------------------------------------------------------------------------
# sherpa-onnx — natively streaming Zipformer transducer
# --------------------------------------------------------------------------

class SherpaOnnxStreaming(StreamingASR):
    """Natively streaming backend — real partials, no LocalAgreement needed.

    Emits unstable partials (``is_final=False``) as the hypothesis grows, and
    a final chunk when sherpa's endpoint detector fires (or on ``flush``,
    with the 0.66 s tail-padding + ``input_finished`` protocol).

    Timestamps are utterance-level approximations — the zipformer transducer
    does not emit word timestamps (Whisper backends do; §3.1 ``words`` rows
    come from the offline pass anyway).

    Model: sherpa-onnx streaming zipformer release, e.g.
    ``sherpa-onnx-streaming-zipformer-en-2023-06-26`` — see
    ``scripts/download_models.py``.
    """

    name = "sherpa-onnx"

    def __init__(self, model_dir, num_threads: int = 2, int8: bool = False) -> None:
        from pathlib import Path

        import sherpa_onnx

        d = Path(model_dir)
        if not d.exists():
            raise FileNotFoundError(
                f"model dir not found: {d} — run scripts/download_models.py first"
            )

        def pick(pattern: str) -> str:
            suffix = ".int8.onnx" if int8 else ".onnx"
            matches = [p for p in d.glob(pattern) if p.name.endswith(suffix)]
            if not matches:
                raise FileNotFoundError(f"no {pattern} in {d}")
            return str(matches[0])

        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"),
            encoder=pick("encoder-*.onnx"),
            decoder=pick("decoder-*.onnx"),
            joiner=pick("joiner-*.onnx"),
            num_threads=num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
        )
        self._stream = None
        self._utterance_start_ms = 0
        self._last_end_ms = 0
        self._last_text = ""

    # -- internals ---------------------------------------------------------

    def _ensure_stream(self, now_ms: int) -> None:
        if self._stream is None:
            self._stream = self._recognizer.create_stream()
            self._utterance_start_ms = now_ms
            self._last_text = ""

    def _chunk(self, text: str, end_ms: int, is_final: bool) -> dict:
        return {
            "text": text,
            "start_ms": self._utterance_start_ms,
            "end_ms": end_ms,
            "is_final": is_final,
            "language": "en",
            "confidence": None,
        }

    # -- StreamingASR ------------------------------------------------------

    def feed(self, frame: AudioFrame) -> list[dict]:
        self._ensure_stream(frame.t_ms)
        self._stream.accept_waveform(SAMPLE_RATE, frame.pcm.astype(np.float32))
        end_ms = frame.t_ms + int(len(frame.pcm) / SAMPLE_RATE * 1000)
        self._last_end_ms = end_ms

        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

        text = (self._recognizer.get_result(self._stream) or "").strip()

        out: list[dict] = []
        if self._recognizer.is_endpoint(self._stream):
            if text:
                out.append(self._chunk(text, end_ms, is_final=True))
            self._stream = None  # next utterance gets a fresh stream
        elif text and text != self._last_text:
            out.append(self._chunk(text, end_ms, is_final=False))
            self._last_text = text
        return out

    def flush(self) -> list[dict]:
        if self._stream is None:
            return []
        # sherpa protocol: 0.66 s of silence tail + input_finished before the
        # final decode of whatever is left in the stream.
        tail = np.zeros(int(0.66 * SAMPLE_RATE), dtype=np.float32)
        self._stream.accept_waveform(SAMPLE_RATE, tail)
        self._stream.input_finished()
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = (self._recognizer.get_result(self._stream) or "").strip()
        out = [self._chunk(text, self._last_end_ms, is_final=True)] if text else []
        self._stream = None
        return out

    def close(self) -> None:
        self._stream = None


# --------------------------------------------------------------------------
# Registry (bake-off harness picks by name)
# --------------------------------------------------------------------------

def available_backends() -> list[str]:
    names = [ChunkedFasterWhisper.name]
    try:
        import sherpa_onnx  # noqa: F401

        names.append(SherpaOnnxStreaming.name)
    except Exception:
        pass
    return names
