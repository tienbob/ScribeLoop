"""Capture silence diagnostics (§8 Phase 0a), independent of permissions/VAD.

Run on a consumer thread, never a native realtime callback. Silence is evidence
of missing signal, not proof of denied permission. No PCM is retained.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .source import SAMPLE_RATE, AudioFrame


@dataclass(frozen=True)
class CaptureHealthEvent:
    kind: str  # prolonged_silence | signal_recovered
    stream: str
    t_ms: float
    silent_audio_ms: float


class CaptureSilenceMonitor:
    """One monitor per stream; emit once per continuous silence episode.

    Durations count received samples rather than processing wall time, allowing
    identical results for live capture and accelerated WAV replay. Timestamp gaps
    reset accumulation: missing audio is not classified as recorded silence.
    """

    def __init__(self, stream: str = 'loopback', *, silence_ms: float = 5000,
                 threshold_dbfs: float = -70):
        if not math.isfinite(silence_ms) or silence_ms <= 0:
            raise ValueError('silence_ms must be positive and finite')
        if not math.isfinite(threshold_dbfs) or threshold_dbfs > 0:
            raise ValueError('threshold_dbfs must be finite and <= 0')
        self.stream = stream
        self.silence_ms = silence_ms
        self.threshold = 10 ** (threshold_dbfs / 20)
        self._silent_samples = 0
        self._warned = False
        self._end_ms: float | None = None

    def feed(self, frame: AudioFrame) -> list[CaptureHealthEvent]:
        pcm = np.asarray(frame.pcm)
        if (frame.stream != self.stream or pcm.ndim != 1 or not pcm.size
                or not np.issubdtype(pcm.dtype, np.floating)
                or not np.isfinite(pcm).all() or not math.isfinite(frame.t_ms)
                or frame.t_ms < 0):
            raise ValueError('expected nonempty finite mono float PCM on the configured stream')
        if self._end_ms is not None:
            if frame.t_ms < self._end_ms - .5:
                raise ValueError('overlapping or backwards capture timestamps')
            if frame.t_ms > self._end_ms + .5:
                self._silent_samples = 0
                # Keep a pending warning until a real signal confirms recovery.
        self._end_ms = frame.t_ms + pcm.size * 1000 / SAMPLE_RATE
        rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float64)))))
        if rms <= self.threshold:
            self._silent_samples += pcm.size
            duration = self._silent_samples * 1000 / SAMPLE_RATE
            if duration >= self.silence_ms and not self._warned:
                self._warned = True
                return [CaptureHealthEvent('prolonged_silence', self.stream, self._end_ms, duration)]
        else:
            duration = self._silent_samples * 1000 / SAMPLE_RATE
            self._silent_samples = 0
            if self._warned:
                self._warned = False
                return [CaptureHealthEvent('signal_recovered', self.stream, self._end_ms, duration)]
        return []
