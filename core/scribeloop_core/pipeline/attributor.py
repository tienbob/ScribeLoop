"""Attribution: merge ASR segments with speaker turns (Flow 1 step 6).

community-1's *exclusive speaker diarization* output exists to ease exactly
this alignment. Online→offline reconciliation is handled in the finalization
pass (Flow 2 step 3): maximum temporal overlap, many-to-one allowed,
embeddings as tie-break — persisted in ``speaker_remaps``.
"""
from __future__ import annotations


def attribute(
    transcript_chunks: list[dict],
    speaker_turns: list[dict],
) -> list[dict]:
    """Merge [{'start_ms','end_ms','text',...}] with [{'start_ms','end_ms','local_speaker'}].

    Returns [{'local_speaker','text','start_ms','end_ms','revision': 1}].
    Overlap-based assignment (an ASR segment takes the speaker with the most
    temporal overlap); Phase 1 implements, with unit tests on synthetic turns.
    """
    raise NotImplementedError("Phase 1 — see ARCHITECTURE.md Flow 1 step 6")
