# Phase 2b — long meetings, speakers and summaries

Updated 2026-10-26. Phase 2a is merged. Work starts on
`feature/phase-2b-long-meetings`. This plan precedes implementation and supersedes
the handoff's conflicting suggestion to make retrieval/search the primary 2b scope.
The architecture's speaker/summaries direction remains authoritative, implemented
in the sequence below. Search/ask-past-meetings is a separate later increment.

## Preserved contracts

One ASR backend per recording; no fallback or extra final ASR pass. Deepgram is
live; local MOSS remains after Stop until separately measured. Audio stays in SLPA
containers and transcript/notes in the encrypted store. Activity detection remains
a heuristic with per-call authorization. Cross-meeting voice learning is never
implied by renaming a speaker. Cloud audio/text requests need displayed scope,
model/destination and bounded budgets. No real recording/provider call is authorized
by offline validation work. Phase 2a physical/quality and packaging gates remain open.

## Implementation order and exit checks

1. **Bounded long-meeting capture/transcription.** Replace repeated whole-recording
   PCM materialization with authenticated incremental reading and bounded timeline
   windows. Add restart-safe checkpoints, complete-prefix validation and bounded
   recovery. Then introduce explicit duration/budget profiles (up to three hours
   for the cloud path), including native-helper limits, disk/resource behavior and
   backend/UI guards. Retain the five-minute app limit until all layers and budget
   disclosure support the longer profile. Local batch MOSS keeps its measured cap.
   Exit: generated long recordings show bounded buffered PCM, linear reads, correct
   timestamps, tamper/truncation detection, Stop/cancel and interrupted recovery.
2. **Durable rolling/hierarchical notes.** Partition confirmed transcript into
   bounded sections and persist section identity, revision/fingerprint, citations,
   provider/model and request state. Summarize only new eligible sections; derive
   final notes from cited section results while preserving original segment IDs.
   Model budgets and multiple text requests must be disclosed in a new grant.
   Old one-summary grants must not silently authorize repeated uploads. No retry
   after an ambiguous dispatch without a new user action. Edits invalidate affected
   sections and derived final notes. Exit: long synthetic transcripts, restart,
   failed section/reduce, staleness, cost caps and deletion pass offline tests.
3. **Explicit speaker profiles.** Link named people to meetings without requiring
   voice learning. Add per-person opt-in for retained-audio exemplar extraction;
   retain model/version/source provenance and allow learning to be paused. Never
   store embeddings for unnamed/unconsented people. Keep this separate from ASR.
4. **Recognition and calibration.** Use vetted, sufficiently long/pure exemplars;
   present uncertain matches as suggestions. Calibrate on consented repeat-speaker
   data and report false matches/misses by condition before auto-name assignment.
   Do not ship guessed thresholds or equate model speaker IDs with people.
5. **Forget person and derived data.** Purge profile exemplars, links, names in
   retained presentation mappings, affected chunks and references; mark summaries
   for redaction review and remind users that plaintext exports/backups may remain.
   Verify encrypted DB/vector deletion and rollback/retry behavior.
6. **Acceptance and handoff.** Full Python/frontend/import/lint/package checks and
   Linux/macOS CI; Windows remains deferred. Separate automated results from real
   summary quality, speaker recognition and long native capture acceptance. Do not
   close all of 2b when only one increment passes.

## First implementation slice

Add an authenticated SLPA cursor with bounded batches and immutable read reports.
It must retain no whole-recording PCM, authenticate from the header/sequence,
resume across appends, handle partial records without consuming them, verify the
footer and refuse changed/replaced/truncated files. Cursor ownership is explicit;
replacement/recovery requires opening a new cursor. A cursor contains derived key
material like existing unlocked readers and must be closed before key retirement.
Add a streaming prefix scan/window primitive that can inspect long containers
without lifting old whole-recording materialization guards. Validate with generated
PCM beyond five minutes and malicious/partial tails, using no devices or providers.
This slice establishes the storage contract; it does not advertise long recording
or rolling notes before the following wiring and acceptance are complete.

## User changes — cloud-only ASR, retained audio and first-use agreement

These decisions supersede the local-MOSS and per-action upload-consent contracts
above. The application will use cloud streaming ASR only. Stop finalizes the same
stream; it never initiates a full audio rerun. While retained audio exists, users
may play it or explicitly rerun the cloud flow. Retain audio for 30 days by default;
Archive audio exempts a recording from automatic audio expiry. Expiry removes
its audio/key, preserving transcript and notes; explicit whole-meeting deletion
remains separate. Long-session wiring must respect retained-audio and reader leases.

Replace repeated consent checkboxes with a versioned first-use agreement, persisted
inside the app store with acceptance time and revocation support. Explain recording
scope, cloud audio/text destinations, automatic notes, manual reruns, retention,
archive and plaintext export. This is a functional data-processing disclosure, not
invented legal contract language or liability waiver. User-supplied formal legal
terms can be incorporated later. Existing sessions/old per-call grants do not count
as acceptance. Reconnect restores acceptance; all recording/upload entry points
check it. Revocation blocks new capture/upload and stops active recording and
summary requests. Settings/providers stay visible. No per-rerun checkbox is required
after acceptance; manual reruns remain explicit user actions with no retry/fallback.
Confirmed: keep Record / Not now for each detected call. This is a recording
choice, not repeated processing consent. Agreement acceptance never starts capture.

Implement first-use authorization and cloud-only app routing before the deferred
bounded-reader/long-session work. Keep experimental model scripts historical and
outside app routing; do not delete downloaded weights or private audio as cleanup.

## First-use agreement implementation status

Implemented: schema v6 acceptance/version/timestamps and revocation; backend gates
for recording, detected-call acceptance, resume, manual rerun and summaries;
first-use dialog and preference revocation; removed repeated recording/audio/text
upload checkboxes. Record / Not now remains required for every detected call.
Revocation stops active capture, signals ASR cancellation and cancels summary work;
it does not delete saved meetings. Existing grants never imply acceptance.

The first-use slice initially marked automatic expiry/archive as pending. The
following slice now implements cloud-only routing and audio retention/archive/
playback/manual reruns; see its current status below. The five-minute capture
limit and deferred long-meeting/speaker-registry work remain open.

Validation for this slice: 372 Python tests passed; 12 frontend regression groups
passed; Ruff and `git diff --check` passed. Generated-audio revocation coverage
verifies that active capture stops before the saved transcription pass and keeps
meeting metadata. No live API requests or private meeting recordings were used.

## Next slice — cloud routing and retained audio

Before implementation: expose only Deepgram streaming in the app and reject local
model commands. Keep historical local experiment runners/tests outside production
routing; do not delete downloaded models. Disable default local fixture inference.
Stop finalizes the existing stream; only an explicit saved-audio action may rerun.

Add persisted per-meeting archive/expiry state. Unarchived audio expires 30 days
after the meeting ends (start time fallback), including old recordings after app
agreement acceptance. Run cleanup at startup/periodically while idle and accepted;
remove encrypted audio/key first, then mark expiry, leaving transcripts and notes.
Failures remain retryable. Archive must not resurrect expired audio; unarchiving
an old recording makes it eligible at the next cleanup. Permanent whole-meeting
deletion remains a separate confirmed action.

Playback uses the authenticated local WebSocket and one selected audio track,
validated before release. Bound decoded audio to the current 305-second container
limit; make an in-memory WAV/blob, never a plaintext temporary file. Block cleanup,
reruns and deletion while a playback read owns the key. This preview mechanism is
explicitly limited to current five-minute recordings; long-meeting playback will
need a bounded HTTP/cursor design in the later slice. Expired/missing audio disables
playback/resume/rerun, with saved text and notes still readable. No provider request
is made for playback, archival, cleanup or loading history. Add archive, expiry and
playback state to the saved-meeting UI; no per-upload consent checkbox returns.

Manual rerun means the full requested flow: replace the transcript atomically, then
make one summary/title request to the currently displayed configured summary
endpoint. Only do so after successful transcript replacement and while the app
agreement remains accepted. Preserve prior notes on failure; prevent duplicate
initial-call summary scheduling and persist a per-revision manual-summary attempt.
Missing summary configuration still permits transcription and explains why notes
were not regenerated. No implicit rerun on Stop, startup or provider failure.

## Cloud/retained-audio slice — complete

- [x] Production cloud-only ASR router, local-model API rejection and UI removal;
  default local fixture inference disabled. Historical local experiments retained.
- [x] Schema v7 persistent audio archive/expiry state; 30-day, idle automatic
  key-first audio-only cleanup. Current agreement version v2 discloses the policy.
- [x] Authenticated, bounded in-memory playback with timestamp gaps, footer
  validation, per-reader key lease and one outstanding delivery per connection.
- [x] Saved audio state, archive/unarchive, player and unavailable/expired controls;
  whole-meeting deletion remains separate and explicitly confirmed.
- [x] Explicit rerun replaces ASR atomically, then requests notes/title once with a
  durable per-revision attempt; failure preserves prior content, without fallback.
- [x] Current README/docs cleaned up; local-selector instructions marked historical.

Validation: full Python suite **383 passed**; **13 frontend regression groups**;
Ruff and whitespace checks passed. Focused tests were rerun after reader/delivery
hardening. Coverage includes archive persistence, startup cleanup, text preservation,
key-first retry, tamper/size rejection, mic-only track selection, repeated reader
cancellation, redirect rejection and full manual-rerun success/failure. Existing
streaming tests assert Stop opens no second ASR pass. No live API requests or
private recordings were used; no downloaded model weights were deleted.

Long-meeting incremental readers/playback, rolling summaries, speaker registry,
calibration and forget-person remain unfinished. The capture cap is still five
minutes; this slice does not close all of Phase 2b.
