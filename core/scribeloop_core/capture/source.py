"""AudioSource protocol + dev/test sources (ARCHITECTURE.md §2 Capture, §2.3).

Every platform source produces the same ``AudioFrame`` stream:
16 kHz mono float32 PCM, in ~100 ms frames, with recorder-timeline timestamps.
The session recorder is fed from this stream (never dropped — §2.3 priority
order); ASR/diarization consume it through bounded queues with the lag-cap
policy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

import numpy as np
import soundfile as sf

#: Pipeline-wide working format (ARCHITECTURE.md §2).
SAMPLE_RATE = 16_000
#: Nominal capture frame length.
FRAME_MS = 100
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

#: Stream identities (schema: session_streams.stream, §3.1).
STREAM_LOOPBACK = "loopback"
STREAM_MIC = "mic"


@dataclass(frozen=True)
class AudioFrame:
    """One capture frame, already resampled to the pipeline format."""

    pcm: np.ndarray  # float32 mono [-1, 1], SAMPLE_RATE Hz
    stream: str  # 'loopback' | 'mic'
    t_ms: int  # ms since session start (recorder timeline)
    wall_ts: float  # time.monotonic() at capture (device-change handling)


@runtime_checkable
class AudioSource(Protocol):
    """Common interface for every platform capture source."""

    stream: str

    def frames(self) -> Iterator[AudioFrame]: ...

    def close(self) -> None: ...


class WavFileSource:
    """Replays a WAV file as AudioFrames — the dev/test/bake-off source.

    ``realtime=False`` (default) replays as fast as the consumer can pull,
    which is what offline bake-offs want. ``realtime=True`` paces frames to
    wall-clock, approximating a live device (for latency measurements).
    """

    def __init__(
        self,
        path: str | Path,
        stream: str = STREAM_LOOPBACK,
        realtime: bool = False,
        frame_ms: int = FRAME_MS,
    ) -> None:
        self.path = Path(path)
        self.stream = stream
        self.realtime = realtime
        self.frame_ms = frame_ms
        self._frame_samples = frame_ms * SAMPLE_RATE // 1000
        if not self.path.exists():
            raise FileNotFoundError(self.path)

    def frames(self) -> Iterator[AudioFrame]:
        data, sr = sf.read(self.path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1).astype(np.float32)
        if sr != SAMPLE_RATE:
            # Cheap linear resample for dev fixtures only; real capture
            # helpers do proper resampling (soxr/ffmpeg).
            n = int(len(mono) * SAMPLE_RATE / sr)
            x = np.linspace(0.0, len(mono) - 1, n)
            mono = np.interp(x, np.arange(len(mono)), mono).astype(np.float32)

        t_ms = 0
        start = time.monotonic()
        for i in range(0, len(mono), self._frame_samples):
            if self.realtime:
                target = t_ms / 1000.0
                remaining = target - (time.monotonic() - start)
                if remaining > 0:
                    time.sleep(remaining)
            yield AudioFrame(
                pcm=mono[i : i + self._frame_samples],
                stream=self.stream,
                t_ms=t_ms,
                wall_ts=time.monotonic(),
            )
            t_ms += self.frame_ms

    def close(self) -> None:  # nothing to release
        pass


class SilenceSource:
    """Emits silent frames up to ``max_ms`` — smoke tests."""

    def __init__(self, max_ms: int = 10_000, stream: str = STREAM_LOOPBACK) -> None:
        self.max_ms = max_ms
        self.stream = stream

    def frames(self) -> Iterator[AudioFrame]:
        t_ms = 0
        while t_ms < self.max_ms:
            yield AudioFrame(
                pcm=np.zeros(FRAME_SAMPLES, dtype=np.float32),
                stream=self.stream,
                t_ms=t_ms,
                wall_ts=time.monotonic(),
            )
            t_ms += FRAME_MS

    def close(self) -> None:  # nothing to release
        pass
