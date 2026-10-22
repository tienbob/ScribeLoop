# Codebase audit against revised Phase 1 plan

Audit date: **2026-10-22**. Baseline: commit `6d55a7b` and
[meeting capture requirements](MEETING_CAPTURE.md). Static code inspection plus
existing automated tests; no new recording, app permission request, real call,
or implementation change was performed for this audit.

**Verdict:** the replay milestone and encryption primitives are reusable, but
the revised meeting-capture MVP is not delivered. No Teams, Slack, Zoom, or
browser meeting support should currently be advertised. Findings below separate
missing scope from defects that would arise if replay components were reused
without adaptation; they are not claims that the existing replay feature fails.

## Prioritized findings

### P1 — No meeting discovery or meeting-recording control path

`api/server.py:165–197` exposes fixture snapshots and replay start/stop only;
`pipeline/replay.py:101` always constructs `WavFileSource`.
`api/static/app.js:64–92` handles replay events and fixture selection only.
There is no metadata observer, app identity registry, candidate state machine,
debounce, dismissal suppression, end grace, source/microphone selection, or
manual live-recording path. Existing consent checks apply to replay only.

Required: add metadata-only discovery and authenticated candidate controls, with
server-side consent and explicit capture scope before opening native sources.
Discovery must be independently fallible without disabling manual recording.

### P1 — Dual-stream capture has no shared clock or microphone source

`capture/macos_client.py:39–74` provides only loopback audio. The Swift helper
registers only `.audio` output (`main.swift:79–90`); no mic source exists.
`capture/pcm_process.py:98–103` subtracts each source's first PTS, making every
source start at zero independently. Combining two such sources would erase their
real start offset; `wall_ts` is Python delivery time, not a preserved native
capture timestamp suitable for reconstructing that offset under buffering.

Required: define a session-wide monotonic clock contract before adding a second
source. Preserve native timestamp mapping, measure alignment/drift, and retain
separate mic/output streams. Do not simply run two independent replay runners.

### P1 — Encrypted audio is not connected to a live recorder

`storage/audio.py:186–218` supplies encrypted stream primitives, but application
code does not instantiate `SessionAudioStore`. `pipeline/replay.py:107–121`
feeds ASR directly on the source consumer thread and can block on inference/UI
queue backpressure. This is acceptable paced file replay, but cannot establish
lossless native recording priority; the Swift output queue also writes directly
to the pipe (`main.swift:52`). A stalled consumer can block that pipe.

Required: a recorder-first lifecycle with bounded transcription work, explicit
overload behavior, storage-failure handling, and coordinated source shutdown.
Measure capture continuity under slow ASR/storage/UI, not just replay throughput.

### P1 — Session persistence assumes replay and loopback

`storage/sessions.py:25–34` creates one loopback fixture stream and hardcodes
consent true; `:51` labels every segment loopback. `recover():13–18` recognizes
only loading/replaying states. The API currently checks consent first, so this
is not a replay consent bypass; these methods are unsafe to treat as a complete
live-recorder persistence interface.

Required: explicit recording source/scope and consent inputs, per-stream rows
and transcript identity, recording-specific transitions, and coordinated stream
creation/failure rollback. Extend recovery before introducing recording states.

### P1 — Interrupted encrypted audio has no recovery/checkpoint workflow

`storage/audio.py:117–129` deliberately leaves an incomplete encrypted prefix on
abort; `read_audio():156–171` requires a complete authenticated footer. Existing
recovery only changes session status, without inspecting audio, repairing to a
verified boundary, or resuming transcription. Deletion is a key-store primitive,
not an authorized session deletion/retention workflow.

Required: specify safe authenticated-prefix recovery and checkpoint semantics
(without weakening normal full-file integrity checks), then implement recovery
and key-aware deletion with active-reader coordination. Do not claim crash-safe
resume from the existing interrupted-state marker.

### P2 — Live speech processing still needs timeline-aware integration

`pipeline/vad.py:42–49` concatenates PCM and emits iterator-relative indices;
it ignores `frame.t_ms`, including a nonzero start or capture gap. Connecting it
unchanged would shift speech boundaries away from the recorded timeline.
Whisper chunked ASR explicitly lacks LocalAgreement (`pipeline/asr.py:55–58`).
Near-online diarization and attribution raise `NotImplementedError`
(`pipeline/diar.py:56–57`, `pipeline/attributor.py:21`).

Required: map VAD windows onto recorder timestamps and handle discontinuities;
then implement confirmation, speaker identity, and overlap-aware attribution.
Existing batch evaluation scripts do not constitute live integration.

### P2 — Platform support and some descriptions overstate implementation

Core Audio taps and Linux capture are placeholders (`capture/macos_client.py:27`,
`capture/linux_pipewire.py:29`), despite module prose describing implementation
and validation. Only the ScreenCaptureKit fallback is implemented; actual
Teams/Slack helper-process routing, muted-call detection, and browser ambiguity
have not been validated. Its empty bundle list selects whole-system output
(`main.swift:71–77`), so future UI/API must require an explicit scope and never
treat a missing selection as permission for system-wide recording.

`ARCHITECTURE.md` still contains legacy WAV-recorder and Tauri descriptions
alongside the delivered SLPA container and browser-host decisions. These should
be reconciled before implementing the recorder; they are not evidence of a
second delivered capture/storage path. Signing and Linux validation remain
explicitly deferred.

## Coverage and verification

| Revised requirement | Code status | Verification still needed |
| --- | --- | --- |
| Teams/Slack active-call detection | Missing | Real calls, idle apps, mute/listen-only, reconnect, false positives/delay |
| Debounce, dismissal, multiple candidates, end grace | Missing | Deterministic state-machine tests plus real activity traces |
| Source and mic selection; manual fallback | Missing | Authenticated UI/API and permission-denial checks |
| Per-app meeting output | SCK primitive only | Actual Teams/Slack app/helper coverage and scope isolation |
| Microphone and common clock | Missing | Separate tracks, alignment, drift, device changes |
| Encrypted DB and audio | Primitives delivered | Recorder wiring, coordinated failure/recovery/deletion |
| Consent and visible Stop | Replay controls delivered | Live-source startup ordering and stop during failures |
| VAD, confirmation, speaker attribution | Partial primitives/placeholders | Timeline gaps, live latency, overlap and identity validation |
| Restart | Replay marked interrupted | Authenticated audio checkpoint and live-session recovery |
| Linux/browser coverage | Linux capture stub; no detector | Host checks; browser ambiguity and explicit capture scope |

Verification on this audit: **122 pytest tests passed**, and
`node tests/frontend_replay.mjs` passed. These cover existing primitives/replay;
they do not establish revised meeting acceptance. No code changes were made.

## Recommended implementation sequence

See the [detailed implementation plan](PHASE_1_IMPLEMENTATION_PLAN.md) for
dependencies, concrete deliverables, and milestone acceptance checks.

1. Reconcile stale implementation descriptions; define source selection,
   candidate events, session states, and the shared capture-clock contract.
2. Implement metadata-only macOS discovery with deterministic candidate tests;
   surface authenticated prompts and manual selection without opening PCM.
3. Add microphone capture and recorder-first encrypted dual-stream lifecycle;
   adapt persistence/consent and enforce one active session across all inputs.
4. Implement recovery/deletion, failure/device handling, then timeline-aware
   live processing. Preserve MOSS float16 + SDPA development defaults.
5. Validate short Teams/Slack calls, then five-minute continuity/resource checks;
   expand browser/Zoom/Linux support only with explicit verification.
