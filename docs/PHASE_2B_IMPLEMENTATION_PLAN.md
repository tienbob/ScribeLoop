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

The agreement explicitly describes current storage and marks automatic 30-day
audio expiry/archive as pending. Cloud-only routing and audio-only retention,
archive, playback/manual rerun availability remain the next implementation slice;
the agreement change does not claim those features are complete. The five-minute
capture limit and the deferred long-meeting/speaker-registry work remain open.

Validation for this slice: 372 Python tests passed; 12 frontend regression groups
passed; Ruff and `git diff --check` passed. Generated-audio revocation coverage
verifies that active capture stops before the saved transcription pass and keeps
meeting metadata. No live API requests or private meeting recordings were used.
