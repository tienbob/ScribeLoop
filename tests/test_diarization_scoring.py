"""Optional scoring-library integration tests; no gated models are required."""
from __future__ import annotations

import pytest
from scribeloop_core.evaluation.diarization import evaluate

pytest.importorskip("pyannote.metrics")
from pyannote.core import Annotation, Segment


def annotation(turns):
    result = Annotation()
    for i, (start, end, speaker) in enumerate(turns):
        result[Segment(start, end), i] = speaker
    return result


def test_duplicate_same_speaker_spans_are_not_two_voices():
    ref = annotation([(0, 4, "A")])
    hyp = annotation([(0, 3, "S1"), (1, 4, "S1")])
    score, mapping, _ = evaluate(ref, hyp, 4, collar_s=0)
    assert score["der"]["diarization error rate"] == 0
    assert score["hypothesis_duplicate_label_s"] == 2
    assert mapping == {"S1": "A"}


def test_real_overlap_is_preserved_after_union_and_counted_as_missed():
    ref = annotation([(0, 4, "A"), (1, 3, "B")])
    hyp = annotation([(0, 4, "S1")])
    score, _, _ = evaluate(ref, hyp, 4, collar_s=0)
    assert score["der"]["missed detection"] == 2
    assert score["der"]["total"] == 6
    assert score["der"]["diarization error rate"] == pytest.approx(1 / 3)
    assert score["overlap_only_with_global_speaker_mapping"]["identification error rate"] == 0.5


def test_speaker_permutation_is_free_but_time_errors_are_not():
    ref = annotation([(0, 2, "A"), (2, 4, "B")])
    hyp = annotation([(0, 2, "S2"), (2, 3, "S1")])
    score, mapping, _ = evaluate(ref, hyp, 4, collar_s=0)
    assert mapping == {"S1": "B", "S2": "A"}
    assert score["der"]["diarization error rate"] == 0.25
