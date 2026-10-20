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
# Registry (bake-off harness picks by name)
# --------------------------------------------------------------------------

def available_backends() -> list[str]:
    names = [ChunkedFasterWhisper.name]
    try:
        import sherpa_onnx  # noqa: F401

        names.append("sherpa-onnx")
    except Exception:
        pass
    return names
