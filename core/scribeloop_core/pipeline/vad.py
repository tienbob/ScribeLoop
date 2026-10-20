"""Silero VAD gate (ARCHITECTURE.md §2 Pre-processing).

Converts a frame stream into SpeechSpan events (speech start/end, sample
indices on the recorder timeline). Silence is discarded downstream; speech
spans are time-stamped (Flow 1 step 3).

The silero-vad pip package ships the ONNX model — no download at runtime.
"""
from __future__ import annotations

import numpy as np

from ..capture.source import SAMPLE_RATE, AudioFrame

try:
    from silero_vad import VADIterator, load_silero_vad

    _SILERO_AVAILABLE = True
except Exception:  # pragma: no cover - optional dep at import time
    _SILERO_AVAILABLE = False

#: Silero requires 512-sample windows at 16 kHz.
_WIN = 512


class SileroVAD:
    """Windowed VAD over AudioFrames → speech events.

    ``feed`` returns a list of dicts with ``start``/``end`` sample indices
    (or None when no boundary crossed in this frame).
    """

    def __init__(self, threshold: float = 0.5) -> None:
        if not _SILERO_AVAILABLE:
            raise RuntimeError("silero-vad is not installed: pip install silero-vad")
        self._model = load_silero_vad()
        self._iterator = VADIterator(
            self._model, threshold=threshold, sampling_rate=SAMPLE_RATE
        )
        self._buffer = np.empty(0, dtype=np.float32)

    def feed(self, frame: AudioFrame) -> list[dict]:
        self._buffer = np.concatenate([self._buffer, frame.pcm])
        events: list[dict] = []
        while len(self._buffer) >= _WIN:
            chunk, self._buffer = self._buffer[:_WIN], self._buffer[_WIN:]
            out = self._iterator(chunk, return_seconds=False)
            if out:
                events.append(out)
        return events

    def reset(self) -> None:
        self._iterator.reset()
        self._buffer = np.empty(0, dtype=np.float32)
