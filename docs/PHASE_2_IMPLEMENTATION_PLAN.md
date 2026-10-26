# Phase 2 — handoff and implementation order

Phase 1 macOS development is closed; [release/quality gates](PHASE_1_CLOSEOUT.md)
remain explicit. This plan starts with Phase 2a finalization from the architecture.
Phase 2 implementation begins on `feature/phase-2-transcript-revisions`.

## Preserved decisions

- One selected transcription backend per operation. Deepgram is live; local MOSS
  Q6_K is after Stop. No automatic cloud fallback, paid retry or extra final ASR pass.
- Source-scoped anonymous speakers are required. Model IDs are not real names or
  persistent identities across independent generations.
- Encrypted capture/storage, explicit recording/upload consent and independent
  Stop remain invariants. Plaintext export is an explicit user action.
- Local live MOSS remains experimental. Five-second windows are computationally
  feasible, but IDs reset and boundary/silence handling needs work. Two-second
  updates with 10–15 seconds of context have not been benchmarked or shipped.
- Native packaging/signing, real app/device validation and Linux parity remain
  separate open work; the browser is still the development host.

## Phase 2a implementation sequence

1. **Revision contracts and persistence.** Distinguish provisional/live text from
   the selected saved transcript revision. Preserve old revisions on failure and
   record provider/model provenance. Add migrations only where current tables are
   insufficient; existing saved recordings must reopen without data loss.
2. **Speaker corrections.** Allow the user to rename/merge anonymous labels within
   a meeting, persist mappings and restore them in notes/history. Keep source and
   generation boundaries explicit; no unvalidated automatic biometric registry.
3. **Grounded final summary.** Use the selected transcript revision and the existing
   structured summary schema. Cite source segment IDs for decisions/action items,
   represent uncertainty and validate output. Provider/model choice and consent
   for any external text request must be explicit before wiring that request.
   Transcript and model output remain untrusted data, never tool instructions.
4. **Markdown export.** Export the selected corrected transcript and summary with
   timestamps/source attribution. Keep export explicit and preserve the fact that
   exported files are plaintext. Do not overwrite an existing file silently.
5. **Revision UI and recovery.** Show current revision, processing/failure state,
   speaker corrections and grounded summary. Failures retain the previous result;
   reconnect and deletion must handle all new data consistently.
6. **Acceptance.** Offline provider mocks verify schema/citations, prompt-injection
   handling, failed replacement, export behavior, migrations and deletion. Run
   Phase 1 regressions and all CI jobs before evaluating any real meeting/provider.

Automatic final retranscription from the original architecture is superseded by
the user's single-path decision. Explicit Transcribe again remains available.
Word-level alignment, automatic speaker reconciliation and a resident local live
worker require separate measured designs; none should invent timing or identity.

## Later Phase 2 work

Phase 2b covers retrieval/search and summary context; Phase 2c covers advanced
mic attribution/echo handling; Phase 2d covers second-OS capture and packaging.
Keep those increments separate from the first usable revision/summary/export flow.

## First increment — revision persistence

Before speaker correction UI, add a versioned transcript revision registry and
read-only history. Confirmed live rows and atomic saved replacements retain their
existing selection behavior (latest revision); provisional text remains ephemeral.
Record provider/model when known, with unknown provenance for legacy rows rather
than guessing. Migrate old databases without changing transcript IDs, text or
speaker scopes. Expose history in reconnect snapshots, preserve it on failed
replacement, and remove it during session deletion. Confirmed appends after saved
replacement must extend the selected revision,
so resumed text cannot disappear into revision 1. Historical revision selection,
speaker corrections, summaries and export remain subsequent increments.

### Completed 2026-10-26

- Schema v3 adds the revision registry. Existing transcript/speaker IDs are
  preserved; legacy provenance remains explicitly unknown. Upgrade retries are safe.
- New confirmed revisions and atomic replacements record provider/model when
  available. Partial replacements are rejected. Failed replacements retain the
  prior transcript, revision metadata and provider.
- Session metadata identifies the selected revision; reconnect snapshots include
  history and replacement events include refreshed revision metadata. Internal
  history reads can retrieve a specific revision without changing selection.
- Resume appends extend the selected revision, preserving earlier history.
  Session deletion removes the registry with the rest of the session data.
- Validation: full suite **338 passed**; after adding the upgrade-retry regression,
  all **10 focused revision tests passed**. Ruff, eight frontend regression groups,
  61-module import smoke and diff checks passed. No audio capture or cloud calls.

Next: speaker rename/merge persistence and API/UI, followed by grounded summaries
and explicit export. Revision history has no user-facing selection control yet.

## Second increment — speaker corrections (implementation scope)

Add explicit saved-revision rename/merge controls to the existing speaker roster.
Corrections are presentation mappings scoped to session + revision; original
segment speaker IDs and older revisions stay intact. Merge only within the same
source track and provider generation. Reject stale revision IDs, missing speakers,
active recording/processing, blank/oversized names and cross-scope merges. Apply
corrections to transcript, roster and regenerated excerpt notes, and restore them
on reconnect. Store edits and refreshed excerpt notes atomically and remove edits
on session deletion. No biometric identity learning or cloud request is involved.

### Second increment completed 2026-10-26

- Schema v4 stores revision-specific speaker display names and merge mappings;
  model speaker IDs and earlier revisions remain unchanged. Chained merges stay
  flat, and cross-track/cross-generation merges are rejected.
- Saved speaker roster includes name and merge controls. Authenticated commands
  validate the selected revision, saved state and speaker membership. Active work
  blocks editing. Equal display names retain separate roster entries.
- Correction mappings and regenerated excerpt notes commit atomically. Failed
  note writes roll back edits; reconnect restores corrected text and notes;
  session deletion removes the mappings before speaker/revision rows.
- Validation: **351 Python tests passed**, nine frontend regression groups, Ruff,
  61-module import smoke and diff checks passed. Brave accessibility and desktop
  screenshot inspection verified the editor and a successful rename using only
  invented text in a temporary public demo. Merge persistence/boundaries were
  verified through API and frontend tests; narrow-layout visual QA remains open.
  The temporary server was stopped. No audio devices or cloud ASR were used.

Next: grounded structured summaries and explicit Markdown export. External summary
provider/model and consent must be decided before any outbound transcript request.
