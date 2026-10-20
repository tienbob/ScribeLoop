"""Diarization (ARCHITECTURE.md §2 Diarization).

Fallback chain for ONLINE diarization (§8 0a / §10):
1. ``diart`` — IF compatible with the pyannote 4 / community-1 stack
   (Phase 0 verification; known friction).
2. near-online chunked offline diarization — re-run the offline pipeline on
   30–60 s overlapping chunks; labels arrive ~1 min late. Simple; likely good
   enough for UX.
3. DIY sliding-window on community-1's segmentation + WeSpeaker embeddings —
   guarantees one embedding space online and offline.

OFFLINE diarization (Flow 2): pyannote/speaker-diarization-community-1
(CC-BY-4.0, HF-gated download — one-time in-app token flow, §9).

Note: "pyannote streaming" exists only as pyannoteAI's commercial cloud
(Live-1) — not an open component (§11).
"""
from __future__ import annotations

from ..capture.source import AudioFrame


class DiarizationBackend:
    """Interface: consume frames → local speaker labels per time window."""

    name = "abstract"

    def feed(self, frame: AudioFrame) -> list[dict]:
        """Return [{'start_ms','end_ms','local_speaker'}] — labels may arrive delayed."""
        raise NotImplementedError

    def flush(self) -> list[dict]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class OfflineCommunity1:
    """Batch pass (Flow 2): pyannote community-1 over a full session WAV."""

    name = "pyannote-community-1"

    def __init__(self) -> None:
        raise NotImplementedError(
            "Phase 2a — requires pyannote.audio + HF-gated community-1 "
            "(one-time in-app token flow, ARCHITECTURE.md §9)"
        )


class NearOnlineChunked(DiarizationBackend):
    """Fallback #2 — offline diarization on 30–60 s overlapping chunks."""

    name = "near-online-chunked"

    def __init__(self) -> None:
        raise NotImplementedError("Phase 0a decision / Phase 1 build — see module docstring")
