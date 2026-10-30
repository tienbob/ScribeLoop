# Transcription and summary quality test — 30 October 2026

## Scope and result

Evaluated the latest saved call (29 October, 15:09 local; 137.1 seconds). Its original recording used Nova-3 multilingual with English and Vietnamese hints. The original live transcript and a later batch replacement both remain saved; the displayed summary belongs to the replacement.

With explicit user authorization, streamed 00:30–01:30 separately from the microphone and meeting-audio tracks to Deepgram and sent the current complete eight-segment transcript to the configured Gemini summary model once. No original sessions, transcript revisions, settings or summaries were changed. No audio playback or new microphone capture occurred.

**This call does not yet demonstrate acceptable transcription or summary quality.** Fresh streaming output remains sparse and unstable relative to the retained revisions. The fresh summary failed JSON parsing; the existing saved summary overinterprets ambiguous transcript fragments. There is no human-verified reference transcript for this call, so no WER, accuracy percentage or end-to-end factual accuracy score is claimed.

## Fresh streaming results

| Track | Audio sent / provider acknowledged | Recognized words | Word language tags | First partial text |
| --- | --- | --- | --- | --- |
| Microphone | 60 / 60 seconds | 7 | English 5, Vietnamese 1, Indonesian 1 | 3.162 seconds |
| Meeting audio | 60 / 60 seconds | 18 | English 10, Vietnamese 7, Indonesian 1 | 8.707 seconds |

Both requests completed without reconnects or retries. Both reported Nova-3 model version `2026-10-16.2`; final responses explicitly acknowledged English and Vietnamese hints. Model word counts and language tags measure output characteristics, not recognition accuracy. Clip-start context differs from the full original call, so this is not a controlled full-call WER comparison.

Important observations:

- English + Vietnamese hints were already present in the original call and acknowledged again in the fresh tests. This case cannot be explained by missing hints alone.
- The saved live revision has 15 segments, including Indonesian and Hungarian language tags. The batch replacement has eight segments tagged English or Vietnamese. The actual wording differs substantially between revisions and the fresh streams.
- Fresh raw provider responses themselves contain the problematic fragments. These discrepancies therefore occur before the desktop renders or stores the new text. Capture quality, processing, context and provider recognition remain possible contributors.
- Signal checks on this 60-second excerpt: microphone peak 0 dBFS, RMS −21.11 dBFS, 0.0854% of samples at absolute amplitude ≥0.98. Meeting-audio peak −12.21 dBFS, RMS −38.20 dBFS, zero near-ceiling samples. The small microphone near-ceiling fraction and the quieter meeting track are observations, not proof of the cause. Whole-clip RMS includes silence.

## Summary quality

The fresh request used the application's actual summary prompt and configured `gemini-3.5-flash-lite` provider. One response arrived in 3.459 seconds, but malformed Unicode escapes made its JSON invalid. The actual validator rejected it with `json_invalid`. No automatic repair was attempted in this diagnostic: approval covered one summary request. This first-attempt failure does not establish whether the app's separate bounded repair would succeed.

The visible saved summary has valid source IDs and appropriately leaves decisions and action items empty. However, it interprets ambiguous fragments as a discussion about accepting responsibility, although the transcript does not clearly establish that topic. The fresh response's readable portions similarly infer responsibility and exception handling from uncertain fragments. Structural citations alone do not demonstrate that a claim follows from its cited text.

The current prompt serializes Vietnamese input using ASCII Unicode escapes. The malformed provider response contains corrupted escapes; a causal relationship is not established. This is a concrete formatting risk to investigate alongside provider-supported structured output. Semantic conservatism is a separate issue: unclear speech should remain unclear rather than becoming a confident narrative.

## Next validation

1. Obtain a human-verified bilingual excerpt before scoring word errors or choosing an alternative recognizer.
2. Compare recognition on the same reference and identical source tracks; do not treat agreement between ASR models as ground truth.
3. Tighten summary formatting and test uncertainty handling with synthetic ambiguous transcript fixtures. Keep citation checks and fail-closed validation.
4. Validate summaries against both the transcript and the verified audio reference. A summary can accurately repeat an incorrect transcript.

Private transcripts, raw provider text and source IDs are excluded from this engineering report. Full diagnostic responses are encrypted locally in ignored `build/macos/quality-test-2026-10-30.enc`; aggregate measurements and the three-attempt journal are in the same ignored directory. Exactly two Deepgram requests (120 total audio seconds) and one Gemini request were made, with zero retries.
