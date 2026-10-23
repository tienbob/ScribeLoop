# Phase 1 — current implementation and closeout plan

Updated 2026-10-23. This replaces the superseded local-MOSS and Groq plans;
previous decisions remain in Git history and the provider evaluation documents.
The default backend is **Deepgram Nova-3 streaming with v1 diarization**. The
explicit **Local · MOSS Q6_K** option runs after Stop, without uploading audio.
Each operation uses one selected backend, with no automatic fallback or final
second pass. See [local model setup and limits](LOCAL_MOSS.md).

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
- Cloud streams each source to the same Deepgram provider; Local MOSS processes
  authenticated saved tracks sequentially after Stop. Keep Stop available during
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

Final Phase 1 handoff/CI: reproduce the open PR's missing optional SQLCipher and
Whisper metadata failures and macOS SQLite-extension limitation. Keep portable
checks free of heavy speech runtimes; exercise encryption and vectors in a Linux
CI job with required native dependencies. Check the current branch's CI after
pushing fixes to its existing PR. Preserve open physical/quality gates and carry
the local live-window prototype into the Phase 2 plan, not the shipped feature list.
Linux/macOS now pass remotely. Windows stalls despite per-test timeout coverage;
add a whole-session faulthandler watchdog to cover collection/executor shutdown
and expose its blocking stack rather than waiting for the runner job limit.

Local live feasibility experiment: benchmark bounded overlapping MOSS Q6_K windows
against existing public English meeting audio, with timing and normalized WER.
Check repeated/missing boundary words and speaker-ID consistency across independent
generations. Keep cloud disabled and the app's proven after-Stop default unchanged
until the experiment establishes a usable live strategy. Do not replay private
recordings or equate faster-than-real-time batch inference with native streaming.

Experiment completed: resident 5-second overlapping windows decode in 0.526 s
median / 0.949 s maximum, with 17.39% WER after rejecting an invalid initial
silence response. Whole-minute WER is 13.77%. Raw speaker IDs reset between short
windows, so they cannot become persistent person labels. Full-prefix decoding at
five minutes takes 31.664 s and cannot sustain a five-second cadence. Keep the app
default unchanged; a future live path needs bounded context, a resident worker,
boundary validation and speaker continuity. Reports and reproduction instructions
are in [Local MOSS](LOCAL_MOSS.md). No capture or cloud API was used.

Demo connection change: the development CLI opens the local web UI without a
launch key by default, as requested. Keep loopback binding, exact same-origin
browser/Host validation and explicit capture/upload consent. Retain token auth
for embedded API usage and an explicit `--require-launch-key` CLI switch. Convey
the mode in server-rendered HTML so the UI opens directly without an unlock dialog.

Completed: demo CLI startup prints a plain localhost URL; the page opens its
socket directly, with no key frame or unlock dialog. Non-loopback clients,
missing/cross-origin requests and invalid Hosts remain rejected. Optional token
mode and normal embedded API authentication remain tested. Recording/upload
consent is unchanged. Verification: 329 Python tests, eight frontend groups,
61 lightweight imports, Ruff and diff checks.

Failure investigation: the latest three local mic-only sessions failed during
capture before a recording-complete event or model invocation. Persist sanitized
failure diagnostics and expose actionable native capture errors without leaking
transcript, audio or credentials. Fix recovery to accept an intentionally empty
output track only for explicit microphone scope. Do not restart capture or upload
saved audio during investigation; physical failure reproduction requires a user retry.

Concrete finding: the latest authenticated prefix contains 170/171-sample mic
frames at 84/95 ms. Rounding continuous 16 kHz packets to integer milliseconds
can produce up to nearly 1 ms of apparent overlap (e.g. the next packet at 105 ms
versus the previous rounded end at 105.6875 ms). The encrypted writer/reader's
0.5 ms threshold rejects this valid sequence. Use a documented 1 ms quantization
tolerance in both, preserve actual clock checks, and test larger overlaps still fail.

Fixed: encrypted writer/reader accept the 1 ms rounding bound; the synthetic
84/95/105 ms sequence round-trips all 300 packets, while 2 ms overlaps still fail.
Mic-only recovery accepts its intentionally empty output container and preserves
strict dual-track recovery. Capture failures now persist allowlisted stage/code/
message diagnostics for reconnect, without raw exception text. No new recording,
audio upload, provider retry or permission change was performed during diagnosis.

Microphone-only change: expose an explicit Microphone only source in the recording
UI and API. Use the existing mic-only native helper mode, never open an output
capture session or require Screen Recording permission for that scope. Encrypted
capture must become ready from mic frames alone; retain an empty authenticated
output container for saved-format compatibility. Deepgram streams the mic live
using one connection; Local MOSS processes it after Stop. Preserve participant
and cloud consent, Stop, model selection, speaker labels and saved actions. Add
offline coverage without recording private microphone audio or using API credits.

Completed: microphone-only is selectable in UI/API and routed to the existing
native mic helper. Readiness/failure checks respect selected streams; unexpected
output frames fail closed. Empty output tracks create no cloud connection or
local inference. Offline API/lifecycle tests prove live mic text before Stop,
single connection, speakers, saved Resume, local after-Stop behavior and consent.
Verification: 316 Python tests, seven frontend groups, Ruff and import smoke.

Model selection change: add explicit Cloud · Deepgram Nova-3 (live) and Local ·
MOSS Q6_K (after recording) choices. Each operation uses exactly one backend;
local execution never uploads or falls back to cloud. Preserve encrypted capture,
source-scoped anonymous speakers, consent, Stop and saved replacement semantics.
MOSS uses pinned transcribe.cpp bindings in an isolated worker and a verified GGUF; availability
and English/Chinese language limits must be visible before recording. Install and
validate the runtime against public audio without consuming provider credits.

Completed: the UI/API selects one backend for new or saved audio, keeps cloud
consent separate, scopes speaker IDs to each track/generation and rejects mixed
provider Resume. Local saved replacements are atomic. Q6_K is downloaded and
hash-verified; the pinned runtime is built. Public 20-second worker and encrypted
pipeline checks succeeded (2.08 s and 5.686 s respectively), with no cloud requests.
Verification: 310 Python tests, six frontend groups, 60 lightweight imports,
repository Ruff, diff checks and wheel-content inspection. Real-call/quality
acceptance remains open. See [setup and measured limitations](LOCAL_MOSS.md).

Cleanup scope: reconcile historical cloud/local-ASR wording, index current versus
historical documentation, apply mechanical lint fixes, and remove disposable
build/test caches. Preserve runtime data, credentials, model files and optional
evaluation tools; no transcription behavior changes are planned.

Cleanup completed: current/historical documentation is indexed, contradictory
Groq-default text and obsolete local-ASR TODO claims are corrected, and mechanical
lint fixes are applied. All 302 Python tests, five frontend regression groups,
57 module imports and repository-wide Ruff pass. Disposable build/test/Python
caches were removed after verification; runtime and evaluation data are retained.

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
