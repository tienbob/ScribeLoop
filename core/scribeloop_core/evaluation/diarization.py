"""Diarization scoring and evidence review; inference dependencies are optional."""
from __future__ import annotations

from itertools import pairwise


def evaluate(reference, hypothesis, duration_s: float, collar_s: float = 0.25) -> tuple[dict, dict, list]:
    """Use one collar-aware global mapping for overall and overlap diagnostics."""
    from pyannote.core import Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate
    from pyannote.metrics.identification import IdentificationErrorRate

    original_reference, original_hypothesis = reference, hypothesis
    # Duplicate/overlapping segments of one speaker are one voice, not overlap.
    reference, hypothesis = reference.support(), hypothesis.support()
    uem = Timeline([Segment(0, duration_s)])
    metric = DiarizationErrorRate(collar=collar_s, skip_overlap=False)
    components = metric(reference, hypothesis, uem=uem, detailed=True)
    ref_eval, hyp_eval, scored_uem = metric.uemify(
        reference, hypothesis, uem=uem, collar=collar_s,
        skip_overlap=False, returns_uem=True,
    )
    mapping = metric.optimal_mapping(ref_eval, hyp_eval)
    # Avoid accidental matches between unmatched hypothesis and reference labels.
    safe_mapping = {label: mapping.get(label, ("unmapped-hypothesis", label))
                    for label in hypothesis.labels()}
    overlap = reference.get_overlap()
    overlap_components = None
    if overlap.duration() > 0:
        overlap_components = IdentificationErrorRate(collar=collar_s, skip_overlap=False)(
            reference, hypothesis.rename_labels(mapping=safe_mapping),
            uem=overlap, detailed=True,
        )
    summary = {"der": components,
               "overlap_only_with_global_speaker_mapping": overlap_components,
               "reference_speakers": len(reference.labels()),
               "hypothesis_speakers": len(hypothesis.labels()),
               "collar_s": collar_s, "skip_overlap": False,
               "mapping_protocol": "global Hungarian mapping on collar-filtered regions",
               "scoring_protocol": "same-speaker-union-reference-collar-v2",
               "reference_duplicate_label_s": sum(s.duration for s, _ in original_reference.itertracks()) -
                    sum(s.duration for s, _ in reference.itertracks()),
               "hypothesis_duplicate_label_s": sum(s.duration for s, _ in original_hypothesis.itertracks()) -
                    sum(s.duration for s, _ in hypothesis.itertracks())}
    return summary, mapping, [(segment.start, segment.end) for segment in scored_uem]


def review_regions(reference: list[dict], hypothesis: list[dict], mapping: dict,
                   evaluated_intervals: list[tuple[float, float]], words: list[dict]) -> dict:
    """Partition error time and find lexical evidence for uncovered speakers.

    All times are in seconds on the same recording timeline. Lexical evidence is
    separate from DER: unmatched identities can represent confusion as well as
    missing speaker-count coverage. Adjacent word intervals are unioned to avoid
    double-counting. No identity is inferred from reference data during inference.
    """
    points = sorted({point for turns in (reference, hypothesis) for turn in turns
                     for point in (turn["start"], turn["end"])} |
                    {point for interval in evaluated_intervals for point in interval})
    totals = {"reference_speaker_s": 0.0, "missed_speaker_s": 0.0,
              "confusion_s": 0.0, "false_alarm_s": 0.0,
              "overlap_missed_speaker_s": 0.0, "nonoverlap_missed_speaker_s": 0.0,
              "uncovered_lexical_speaker_s": 0.0,
              "unambiguous_missed_lexical_speaker_s": 0.0,
              "unambiguous_missed_gap_speaker_s": 0.0,
              "ambiguous_missed_speaker_s": 0.0}
    regions = []
    for start, end in pairwise(points):
        middle = (start + end) / 2
        if not any(a <= middle < b for a, b in evaluated_intervals):
            continue
        ref = {turn["speaker"] for turn in reference if turn["start"] <= middle < turn["end"]}
        raw_hyp = {turn["speaker"] for turn in hypothesis if turn["start"] <= middle < turn["end"]}
        hyp = {mapping.get(label, ("unmapped-hypothesis", label)) for label in raw_hyp}
        duration = end - start
        missed = max(0, len(ref) - len(hyp)) * duration
        confusion = (min(len(ref), len(hyp)) - len(ref & hyp)) * duration
        false_alarm = max(0, len(hyp) - len(ref)) * duration
        totals["reference_speaker_s"] += len(ref) * duration
        totals["missed_speaker_s"] += missed
        totals["confusion_s"] += confusion
        totals["false_alarm_s"] += false_alarm
        totals["overlap_missed_speaker_s" if len(ref) > 1 else "nonoverlap_missed_speaker_s"] += missed
        if missed + confusion + false_alarm <= 1e-9:
            continue
        missing = ref - hyp
        evidence = 0.0
        lexical_count = 0
        for speaker in missing:
            intervals = sorted((max(start, word["start"]), min(end, word["end"]))
                               for word in words if word["speaker"] == speaker
                               and word["start"] < end and word["end"] > start)
            lexical_count += len(intervals)
            last = start
            for a, b in intervals:
                evidence += max(0, b - max(a, last))
                last = max(last, b)
        totals["uncovered_lexical_speaker_s"] += evidence
        if missed > 0:
            if confusion < 1e-9:
                totals["unambiguous_missed_lexical_speaker_s"] += evidence
                totals["unambiguous_missed_gap_speaker_s"] += max(0.0, missed - evidence)
            else:
                totals["ambiguous_missed_speaker_s"] += missed
        regions.append({"start": start, "end": end, "reference_speakers": sorted(ref),
                        "hypothesis_labels": sorted(raw_hyp), "uncovered_reference_speakers": sorted(missing),
                        "missed_speaker_s": missed, "confusion_s": confusion,
                        "false_alarm_s": false_alarm, "reference_overlap": len(ref) > 1,
                        "uncovered_lexical_speaker_s": evidence,
                        "uncovered_lexical_word_intersections": lexical_count})
    return {"totals": totals, "regions": regions,
            "review_priority": sorted(regions, key=lambda row: row["uncovered_lexical_speaker_s"],
                                      reverse=True)[:10]}
