# Phase 1 implementation plan — meeting detection and capture

Created **2026-10-22** from the [code audit](PHASE_1_PLAN_AUDIT.md) and
[revised requirements](MEETING_CAPTURE.md). Status: **implementation in progress**.
Progress: M1 contracts/native query and the pure detector portion of M2 are
implemented; host output activity is visible, but input visibility and real-call
feasibility remain unverified. See [the first increment](MEETING_DETECTION.md). No complete milestone
or meeting-app support is claimed from this foundation.
Each milestone should be a reviewable commit on a `feature/...` branch, with
implementation, relevant tests, and a documentation status update together.

## Scope and dependency order

First deliver a usable **macOS Teams/Slack** workflow: automatic candidate prompt
→ confirmed source and microphone → encrypted dual-stream recording → live text
→ Stop/recovery. Manual source selection must work independently of discovery.
Zoom, ambiguous browser candidates, and Linux follow explicit platform checks;
they remain required coverage, not implied by macOS success. Signed distribution
remains deferred under the existing local-development decision.

Order: **M1 → M2 → M3 → M4 → M5 → M6 → M7**. Early native discovery/capture
feasibility checks happen in M1 so unsupported signals do not force a late redesign.
Real-call validation starts as soon as M4 is usable, before expensive ASR work.

## M1 — Contracts and native feasibility

Addresses audit findings 1, 2, 4, and 7.

- Reconcile architecture descriptions: browser host for local v0, SLPA encrypted
  recording containers, implemented SCK fallback, and clearly labeled tap/Linux
  placeholders. Keep WAV as test/import/export format rather than plaintext
  session storage.
- Verify macOS metadata API availability and actual Teams/Slack app/helper
  identities. Observe metadata only; check whether mute and listen-only states
  remain distinguishable from idle apps. Document ambiguous cases rather than
  inventing a reliable meeting signal.
- Define typed observations, candidates, and explicit capture selection:
  per-app identity or explicitly chosen system output, microphone device,
  capability/permission state, evidence, and candidate generation/version.
  Do not persist ongoing app activity history.
- Define recording states: preparing → recording → stopping →
  completed/failed; interrupted on restart. Replay keeps its existing semantics.
  Reserve one session slot across replay and recording. Discovery has its own
  available/unavailable state and cannot own that slot.
- Specify a shared native monotonic session origin, stream timestamp mapping,
  gaps/discontinuities, and device-clock drift handling. Extend/version SLP1 if
  necessary; preserve replay compatibility. Delivery time is not capture time.

Exit: checked API/identity notes, explicit data/state/clock contracts, and a
documented fallback for ambiguous detections. No claim of app support yet.

## M2 — Metadata-only discovery and source-selection UI

Addresses audit finding 1 and the discovery portion of 7.

- Add a macOS observer separate from PCM capture, a supported-app identity
  registry with explicit helper mappings, and a pure detector state machine.
- Use configurable debounce and end grace, per-candidate dismissal suppression,
  stable candidate IDs, and reset after a genuinely new activity episode.
  Output-only activity may yield an ambiguous candidate; microphone use alone
  must not be presented as proof of a meeting.
- Extend authenticated snapshots/events for capabilities and candidates;
  commands select/dismiss candidates and manually choose sources. Revalidate
  identities and candidate freshness server-side before recording.
- Add app/scope/microphone confirmation and permission/error states to the UI.
  While recorder integration is unfinished, clearly disable Record. Never open
  a PCM source from discovery, selection, dismissal, or an unauthenticated client.

Exit: deterministic tests cover idle/open apps, transient activity, mute/listen
ambiguity, repeated/dismissed/multiple candidates, app exit, observer failure,
and reconnect snapshots. Manual selection still works with discovery unavailable.

## M3 — Separate capture sources and aligned timestamps

Addresses audit findings 2 and 7.

- Implement a native microphone source and explicit source/device enumeration.
  Adapt the existing per-app SCK source to the shared-clock contract. Retain
  Core Audio taps as a separate implementation task if the fallback is sufficient
  for this local milestone; do not require a second output backend just to start.
- Verify Teams/Slack output routing, including their helper processes. Empty or
  vanished app selection is an error, never an implicit whole-system fallback.
- Preserve source identity, capture timestamps, and gaps. Detect device loss,
  sample-rate changes, helper exit, and permission denial. Reopen only within
  the already authorized scope; report a discontinuity or stop safely.
- Use bounded native transport buffering and surface overflow/stall failures;
  do not promise lossless capture with an unbounded or blocked output callback.

Exit: controlled synthetic transport tests plus a short authorized native check
show separate local/remote tracks and preserved start offset. Provisional local
gate: alignment error and drift each ≤50 ms over five minutes; measure and revise
explicitly if native constraints require it. This is an engineering gate, not an
already measured result. Record no real meeting until M4's encryption path exists.

## M4 — Recorder-first encrypted session lifecycle

Addresses audit findings 3 and 4.

- Add a recording coordinator that reserves the active slot, validates consent
  and selection, prepares keys/DB/stream writers, then opens native sources.
  Persist both stream identities and source/scope metadata. On partial startup
  failure, close every opened resource and reconcile files/keys/session state.
- Feed separate SLPA writers before optional ASR processing. Keep native reads,
  recorder I/O, inference, and UI delivery independently scheduled with bounded
  queues. If transcription falls behind, retain audio and schedule catch-up;
  if recording cannot keep up or storage fails, stop visibly with an error.
- Extend SessionStore to explicit consent, recording states, and per-stream
  transcript rows. Introduce versioned migrations if schema changes are needed;
  preserve existing replay history and encrypted DB behavior.
- Connect authenticated Record/Stop, always-visible activity state, elapsed time,
  scope/microphone labels, and errors. Stop closes sources and finalizes both
  streams without waiting for a blocked model call. A model failure must not
  silently terminate otherwise healthy recording.

Exit: tests inject denied consent, missing app, locked keystore, second-start
race, one-source startup failure, slow ASR/UI, recorder overflow/disk failure,
and stop during inference. No PCM before consent; no plaintext recording files;
all terminal paths release the slot and resources. Run a short Teams/Slack check
now to expose source-routing problems before proceeding.

## M5 — Recovery, checkpoints, and deletion

Addresses audit finding 5 and restart behavior from 4.

- Define durability boundaries and persist per-stream recorded/confirmed ASR
  checkpoints. After restart, mark active recording interrupted and inspect its
  encrypted artifacts; never automatically resume microphone capture.
- Keep strict full-file reads unchanged. Add a separate authenticated-prefix
  recovery operation that identifies the last valid complete record, preserves
  the original artifact, and writes recovered frames into a fresh encrypted file
  with a new file ID. Never append using an old nonce sequence or treat corruption
  as ordinary successful completion. Report recovered duration and any tail loss.
- Resume transcription from a verified audio checkpoint with overlap and
  deduplication; never duplicate already confirmed segments.
- Implement authorized session deletion/retention that first stops active users
  of keys, coordinates DB/audio cleanup, and can retry partial failures. Explain
  that backups and already unlocked readers limit crypto-shredding guarantees.

Exit: crash/fault tests at startup, record writes, checkpoint commits, footer
write, and deletion; tampered records fail authentication. Confirmed text remains
and any recovered audio is explicitly labeled interrupted/recovered.

## M6 — Timeline-aware live transcription and speakers

Addresses audit finding 6.

- Make VAD preserve recorder timestamps across nonzero starts and gaps; use
  independent state per stream and retain all recorded silence in storage.
- Add LocalAgreement partial/confirmation policy and bounded inference workers.
  Persist confirmed text only. Show catching-up state and preserve Stop while
  inference is busy; use durable audio for backlog processing.
- Implement near-online community-1, stable cross-chunk speaker identities,
  and tested attribution. Preserve concurrent turns for overlap; show unknown
  or uncertain attribution rather than fabricating a speaker. Label the mic
  stream “Microphone” until own-voice attribution is validated.
- Keep MOSS batch defaults **float16 + SDPA** and the canonical five-minute
  development sample. The existing tiny CPU/int8 replay backend is a development
  baseline, not evidence of production accuracy or an override of MOSS defaults.

Exit: timeline/confirmation/identity tests and paced five-minute measurements of
WER, speaker attribution/DER, partial/confirmed latency, CPU/GPU use, and memory.
Compare against architecture targets; record misses instead of declaring success.

## M7 — App matrix and Phase 1 acceptance

Addresses remaining platform gaps and closes the revised requirement only on
verified configurations.

- Teams desktop and Slack huddles: exercise idle app, real call, mute, listen-only,
  silence, reconnect, repeated meetings, dismissal, app exit, headset changes,
  permission denial, manual fallback, Stop, and restart recovery.
- Measure detector false positives/misses/delay separately from capture and ASR
  overhead. Set detector thresholds after M1/M2 observations, before final app
  sign-off. Verify the five-minute clock/continuity gate from M3 under slow ASR.
- Add Zoom and browser candidate coverage. Browser identity remains ambiguous
  unless a separately justified capability identifies the meeting; no history
  scraping or unverified tab-isolated capture claim.
- Implement Linux discovery/capture adapters and run equivalent host checks.
  Track unsupported or unavailable host cases explicitly; macOS completion alone
  cannot close cross-platform acceptance.
- Publish a support matrix with OS/app versions, measured outcomes, limitations,
  and unresolved targets. Keep Phase 0 corpus/signing gates separately visible.

Exit: revised meeting acceptance plus the remaining original Phase 1 criteria
are evidenced, or each missing gate is explicitly left open. Documentation must
distinguish delivered local macOS capability from full Phase 1 completion.

## First implementation increment

Start with **M1 and the pure detector portion of M2**: correct stale descriptions,
verify local metadata capabilities, define the shared clock/source contracts,
and add deterministic candidate tests. This produces a reviewable foundation
without enabling incomplete real-audio recording.
