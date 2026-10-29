# Streaming recognition quality findings — October 29, 2026

This engineering summary contains aggregate findings only. Private-call details,
recording timestamps, audio, transcripts and full provider responses are excluded
from Git. Authorized test responses remain encrypted in ignored local build output.

## Language-mode comparison

A separately authorized comparison used one 30-second excerpt on each of two
independent tracks, streamed at real-time pace. All six requests completed with
30 seconds acknowledged per request, no retries and no saved-transcript changes.
Every request selected Nova-3 with the same transport and diarization settings.

| Mode | Track A final words | Track B final words |
|---|---:|---:|
| Fixed Vietnamese | 5 | 0 |
| Multilingual with Vietnamese hint | 21 | 30 |
| Multilingual with English/Vietnamese hints | 21 | 35 |

Provider metadata returned version **2025-09-26.11905** for fixed Vietnamese and
**2026-10-16.2** for both multilingual modes. These are observations from those
requests, not a permanent routing guarantee. Fixed-language streaming dropped
most final text in this sample. Word counts and confidence are not accuracy scores;
this experiment does not establish a general winner or a measured WER.

## Vocabulary-hint comparison

A second explicitly authorized experiment compared the same opening 30 seconds
on both tracks with and without two plain vocabulary hints. All four requests
completed, with 120 total audio seconds and no retries. One greeting spelling
improved on one track; one English phrase improved on the other. The English
phrase remained incorrect on the first track. Other language-detection errors
remained despite language hints. Vocabulary hints demonstrated a partial benefit,
not a complete remedy. No production vocabulary list was hardcoded.

## Product implications

- Keep language selection as explicit hints rather than treating it as a strict
  allowlist. Preserve per-word language tags across the documented supported set.
- Consider an editable vocabulary field for names and expected terminology.
- Retain bounded request/model/hint acknowledgments and confidence diagnostics.
  Current production code still discards word confidence, so this remains work.
- Do not globally replace similar-sounding brand names or silently suppress
  cross-track speech. Such transformations can corrupt legitimate conversations.
- Distinguish a saved-audio replacement revision from the original live transcript
  when investigating user feedback. Preserve both revisions.
- Benchmark another recognizer on a corrected reference if the best Deepgram
  configuration still performs poorly. A provider change needs measured evidence
  and explicit authorization for any new private-audio upload.

No playback or speaker output was initiated. No raw audio or transcript file was
created, and the application's saved transcripts and settings were not changed
by the comparisons. Additional paid tests require their own authorization.

## Primary references

- [Nova-3 language prompting](https://developers.deepgram.com/docs/nova-3-language-prompting):
  hints bias recognition and do not restrict the output language.
- [Keyterm prompting](https://developers.deepgram.com/docs/keyterm): repeated plain
  terms on Nova-3; the legacy weighted-keywords syntax does not apply.
- [October 5 update](https://developers.deepgram.com/changelog/2026/10/5): the
  Vietnamese improvement described there is for batch, not proof of an equivalent
  streaming improvement.

The call-stop and automatic-summary fixes are documented separately in
[Call workflow repair](CALL_WORKFLOW_FIX_2026-10-29.md). Recognition word accuracy
remains an open quality issue.
