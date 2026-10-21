"""Timeline accounting controls independent of optional ML/scoring packages."""
from __future__ import annotations

import pytest
from scribeloop_core.evaluation.diarization import review_regions


def turn(start, end, speaker):
    return {"start": start, "end": end, "speaker": speaker}


def test_overlap_omission_has_word_evidence_and_gap_evidence_without_double_counting():
    ref = [turn(0, 4, "A"), turn(1, 3, "B")]
    hyp = [turn(0, 4, "S1")]
    words = [turn(1, 2, "B"), turn(1.5, 2.5, "B")]
    result = review_regions(ref, hyp, {"S1": "A"}, [(0, 4)], words)
    totals = result["totals"]
    assert totals["reference_speaker_s"] == 6
    assert totals["overlap_missed_speaker_s"] == 2
    assert totals["nonoverlap_missed_speaker_s"] == 0
    assert totals["unambiguous_missed_lexical_speaker_s"] == 1.5
    assert totals["unambiguous_missed_gap_speaker_s"] == 0.5


def test_scored_regions_exclude_collars_and_distinguish_confusion_from_miss():
    ref = [turn(0, 4, "A")]
    hyp = [turn(0, 2, "S1")]
    result = review_regions(ref, hyp, {"S1": "B"}, [(0.25, 3.75)], [])
    assert result["totals"]["confusion_s"] == pytest.approx(1.75)
    assert result["totals"]["nonoverlap_missed_speaker_s"] == pytest.approx(1.75)


def test_unmapped_hypothesis_cannot_accidentally_match_a_reference_name():
    result = review_regions([turn(0, 2, "A")], [turn(0, 2, "A")], {}, [(0, 2)], [])
    assert result["totals"]["confusion_s"] == 2
    assert result["totals"]["missed_speaker_s"] == 0


def test_false_alarm_in_silence_is_not_a_speaker_miss():
    result = review_regions([], [turn(0, 2, "S1")], {}, [(0, 2)], [])
    assert result["totals"]["false_alarm_s"] == 2
    assert result["totals"]["missed_speaker_s"] == 0
