"""Speaker registry (Flow 3).

- Profiles keep up to ~10 exemplars (no centroids — one wrong match cannot
  contaminate a profile). Exemplars only from offline-reconciled segments.
- Matching: max cosine over all exemplars; thresholds calibrated per model
  and audio condition (0.75 auto-assign / 0.60 suggest are starting
  defaults, re-measured in Phase 2b on real meetings).
- Manual rename relabels the session and flags a candidate exemplar — it
  does NOT auto-add one (the online cluster may be impure).
- Bootstrapping (rename → opt in → extract from retained audio) only works
  within the audio retention window (default 30 days) — §3.1.
"""
from __future__ import annotations


class SpeakerRegistry:
    """Global registry backed by the ``speakers``/``speaker_embeddings`` tables."""

    def __init__(self) -> None:
        raise NotImplementedError("Phase 2b — built on storage.db (schema §3.1)")
