"""Linux capture — GStreamer ``pipewiresrc``/``pulsesrc`` → ``appsink``.

Default (ARCHITECTURE.md §2.3 / §10): an ``appsink`` **pulled** from a Python
thread — no realtime-callback contract. ``pw-cat`` subprocess /
sounddevice Pulse-monitor are experiments, not defaults.

Placeholder only — Linux implementation and host validation remain pending.

Target pipeline (system-wide monitor):

    pipewiresrc ! audioconvert ! audio/x-raw,format=F32LE,rate=16000,channels=1
        ! appsink name=sink max-buffers=8 drop=false

Per-application capture uses PipeWire stream targeting (only the meeting app).
"""
from __future__ import annotations

from collections.abc import Iterator

from .source import AudioFrame


class PipeWireSource:
    """GStreamer appsink source (PipeWire default, PulseAudio fallback)."""

    stream = "loopback"

    def __init__(self, target_app: str | None = None) -> None:
        raise NotImplementedError(
            "PipeWireSource is not implemented; Linux host validation is pending — "
            "see ARCHITECTURE.md §2.3/§9 and the docstring for the pipeline."
        )

    def frames(self) -> Iterator[AudioFrame]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
