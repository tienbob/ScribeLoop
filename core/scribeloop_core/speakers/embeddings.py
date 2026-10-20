"""Speaker embeddings (Flow 3, §10 decision log).

Model: WeSpeaker embeddings (community-1's internal space) or SpeechBrain
ECAPA — ONE model online and offline. Resemblyzer is excluded (dormant, §11).
Exemplars are taken ONLY from offline-reconciled segments (≥ ~30 s,
purity-checked) and store their embedding model name/version (schema §3.1).

Transients: embeddings computed in memory for unnamed speakers are never
persisted (opt-in rule above).
"""
from __future__ import annotations

import numpy as np


def extract(pcm: np.ndarray, model_name: str = "wespeaker") -> np.ndarray:
    """Compute a speaker embedding for float32 mono PCM. Phase 2b build."""
    raise NotImplementedError("Phase 2b — WeSpeaker/ECAPA extractor (§10 decision log)")
