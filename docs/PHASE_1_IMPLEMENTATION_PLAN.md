# Phase 1 — current implementation and closeout plan

Updated 2026-10-23. This replaces the superseded local-MOSS and Groq plans;
previous decisions remain in Git history and the provider evaluation documents.
The current backend is **Deepgram Nova-3 streaming with v1 diarization**. Each
meeting uses one provider, with no automatic fallback or final second pass.

## Development scope

The current development host is macOS with native capture and a local authenticated
browser notebook. The user deferred real-meeting testing and signing. This is not
packaged desktop or cross-platform release acceptance. The original quality and
app-coverage requirements stay required and visibly open below.

| Milestone | Implementation | Acceptance still required |
| --- | --- | --- |
| M1 Contracts/native feasibility | Source identities, scope, metadata observations and shared-clock contracts implemented | Physical helper/process routing and real-call feasibility per app |
| M2 Discovery/source UI | Metadata-only candidates, dismissal, source/microphone confirmation, manual fallback, authenticated commands | Teams/Slack false positives, misses, mute/listen-only and detection delay |
| M3 Native dual-track capture | Microphone/output sources, shared timestamps, resampling, bounded transport and generated five-minute checks | Controlled physical alignment/drift ≤50 ms; headset/device scenarios |
| M4 Encrypted recorder | Recorder-first SLPA/SQLCipher, keys before sources, bounded capture queue, visible failures, independent Stop/drain | Physical continuity and storage/device failures on actual calls |
| M5 Recovery/lifecycle | Authenticated-prefix recovery into fresh files, durable recorded/confirmed checkpoints, explicit resume, deletion/retention | End-user recovery exercise after a real interrupted recording |
| M6 Streaming/speakers | Provisional text, final word speaker splits, persistent anonymous labels and notes, Auto/fixed languages, durable cloud caps | Representative WER ≤18%, partial p50 <2 s, reliable 2–3-person attribution and overlap evaluation |
| M7 Support/closeout | Current support matrix, regression evidence, known limits and later acceptance checklist published | Actual Teams/Slack/browser app matrix and Linux implementation/host checks |

## Current behavior

- Capture selected app output and microphone into separate encrypted tracks;
  browser scope includes every tab in that browser. Detection never records.
- Stream each source to the same Deepgram provider. Keep Stop available during
  processing; provider failure preserves otherwise healthy encrypted recording.
- Persist only confirmed text. Speaker identity, rows and checkpoints commit
  together. Labels are anonymous and scoped to a connection/source; new connections
  receive fresh labels. Missing attribution stays unknown; names are not inferred.
- Auto mode uses Nova-3 multilingual recognition (en/es/fr/de/hi/ru/pt/ja/it/nl).
  Fixed choices include those ten plus Vietnamese. No ineffective hint controls,
  unsupported `detect_language` parameter or fake first-language lock. The user
  explicitly chose Auto + fixed language instead of unsupported multiple hints.
- Require participant permission and separate provider-named upload consent;
  saved Resume/Reprocess require their own consent. No private audio is uploaded
  by development checks. Keys stay in environment/ignored `.env`, never snapshots.
- Continuous audio includes silence. No additional client VAD is introduced.
  Reserve each ten-second block durably before upload, shared across both sources
  and saved actions: 80 reservations / nominal $0.15 per saved meeting at the
  conservative $0.60/audio-hour estimate. Native capture remains five-minute bounded.
- No automatic paid retry, alternate model or final pass. Explicit reprocessing
  retains the prior transcript until the replacement succeeds.

## Finalization work authorized by “finish phase 1”

Audit follow-up: validate provider completion before advancing the saved audio
checkpoint. A graceful close must not discard provisional words or acknowledge
audio beyond the provider's reported processed duration. Add offline fault tests
for both cases and retain normal silence-only completion.

Completed: final metadata now checks the processed duration, and outstanding
provisional speech blocks the final checkpoint advance. Three offline regressions
cover unfinished speech, truncated processing and successful silence completion.
Verification: 302 Python tests, five frontend regression groups, 57 module imports,
changed-file Ruff and `git diff --check` passed. No provider credits were used.

1. Remove the obsolete mandatory public-WAV argument from encrypted cloud startup;
   keep a fixture required only for explicit public replay mode.
2. Run the Python/frontend/import/package checks and reconcile current docs.
3. Close the Phase 1 **development scope**, retain the original unpassed quality,
   physical and platform gates, and commit. Do not record private calls or consume
   more provider credits to manufacture acceptance evidence.

See [the authoritative closeout and later test checklist](PHASE_1_CLOSEOUT.md),
[streaming implementation](DEEPGRAM_STREAMING.md), and
[meeting acceptance requirements](MEETING_CAPTURE.md).
