"""Word scoring shared by the bake-off and corpus runner.

Normalization is lowercase plus Unicode punctuation removal. This is a small,
versioned protocol, not the full language-specific Whisper normalizer.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

NORMALIZATION = "unicode-lower-punctuation-v1"


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(
        " " if unicodedata.category(char).startswith("P") else char for char in text
    ).split()


@dataclass(frozen=True)
class WordScore:
    errors: int
    reference_words: int

    @property
    def wer(self) -> float | None:
        # Silence has no denominator; retain insertion counts for aggregation.
        return self.errors / self.reference_words if self.reference_words else None


def score_words(reference: str, hypothesis: str) -> WordScore:
    ref, hyp = normalize(reference), normalize(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[-1] + 1,
                               previous[j - 1] + (word != other)))
        previous = current
    return WordScore(previous[-1], len(ref))
