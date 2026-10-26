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

## Third increment — grounded summaries and Markdown export

Build a bounded structured summary contract that requires source segment IDs for
all factual items, including overview, decisions and actions. Prompt separation
keeps transcript text untrusted; validate citation membership and show AI output
as a draft requiring review (citation validation cannot prove semantic accuracy).
Bind each saved summary to revision + corrected-transcript fingerprint, provider
and model. Stale output must never replace a newer transcript's result. Preserve
previous summaries on provider/schema/storage failure; edits mark old summaries
outdated. Summary requests are explicit, bounded and asynchronous, with no retry,
provider fallback. The initial manual-only summary flow below is superseded by
the per-call permission workflow described later. The user selected an
OpenAI-compatible endpoint configured with SUMMARY_BASE_URL, SUMMARY_MODEL,
SUMMARY_API_KEY and optional SUMMARY_PROVIDER in .env (environment overrides).
Remote endpoints require HTTPS and separate consent; loopback HTTP is allowed.
No provider/model is preselected and no request is made during setup.

Export is an explicit browser download of plaintext Markdown: selected corrected
transcript, timestamps, stable citation anchors, revision/model provenance and the
matching current summary if available. Escape user/model Markdown and HTML. No
backend arbitrary path writing or silent overwrite. Reconnect restores summaries
and freshness; session deletion removes them. Cover citations, injection-shaped
text, staleness, failure, consent and export offline before any live provider test.

### Third increment completed 2026-10-26

- Added strict cited-summary sections and schema v5 persistence, bound to revision
  and corrected-transcript fingerprint. Unknown citations and stale results fail;
  previous drafts survive errors. Speaker edits and transcript updates mark notes
  outdated; session deletion removes saved drafts.
- Configurable OpenAI-compatible summary transport reads only SUMMARY_* settings
  from .env/environment. No default model/provider or automatic request. Remote
  requests require separate text consent, bounded input/output/time and one attempt.
- Meeting notes show configured destination, saved provider/model, source buttons,
  request state and outdated drafts. Explicit plaintext Markdown download exports
  corrected transcript and matching notes with citation anchors and unique names.
- Verification: **358 Python tests passed**, ten frontend regression groups, Ruff,
  63-module import smoke and diff checks passed. Brave verified mocked generation,
  source controls and a browser save dialog (cancelled); desktop screenshot inspection passed. Preview
  stopped. No real provider request, key use, audio capture or private upload.

See [summary configuration and limits](MEETING_SUMMARIES.md). Real provider/model
compatibility and summary quality, narrow-layout visual QA and remote CI remain
open acceptance work. Later Phase 2 retrieval/search and native packaging remain
outside these increments.

## UX revision — consented automatic call notebook (2026-10-26)

Replace the default setup-first screen with call monitoring and a per-call
permission prompt. One affirmative action authorizes the displayed app + microphone
capture, selected ASR processing, and (when configured) one post-call summary to
the displayed text endpoint. No recording precedes this grant. Remove checkboxes
from this primary path; retain explicit advanced/manual controls for fallback.
Create the record at acceptance with app/date/time as its temporary name.

Track app activity in the backend, independently of browser connection. Automatic
stop requires 60 continuous seconds of fresh, complete, explicitly inactive app
activity; unknown/stale observations reset the countdown. Show the pending end
and let the user keep recording or stop immediately. This is an activity heuristic,
not verified Teams/Slack/browser call state; playback, mute and helper behavior can
confuse it. Keep the existing five-minute development cap visible. Browser scope
still includes all tabs, not an identified meeting tab. Declines and completed
recordings suppress re-prompts for that activity episode.

After successful capture/transcription completion, use the per-call grant for one
bounded summary attempt (no retries/fallback), even after UI disconnect. Never
summarize partial/error sessions automatically. Bind consent to endpoint/model;
store the request before dispatch. Derive a cited short topic title from the same
summary response, replacing only the automatic placeholder. Preserve time/source
metadata. Missing configuration or summary failure leaves the recording available
with a manual recovery path; never pretend notes were generated.

Review findings driving this change: default setup form and duplicate checkboxes
interrupt the call; activity suggestions currently require a separate source
confirmation; call ending has no recording lifecycle binding; summary generation
is manual; titles stay unrelated to content. Keep keyboard/focus and live status
semantics, clear recording scope, explicit Stop and provider visibility.

### Automatic call UX increment completed 2026-10-26

- Default screen monitors for activity. A short modal contains scope/provider
  disclosure and one affirmative recording/processing/notes action; detailed
  setup and manual checkboxes are collapsed under preferences. Keyboard decline
  and per-episode suppression prevent repeated prompts.
- Backend AutoCallMonitor tracks only approved sessions. Sixty seconds of fresh,
  known inactivity ends capture; gaps/unknown evidence reset the countdown. Keep
  recording restarts it. Discovery heartbeats do not flood browser clients.
- A per-call endpoint/model grant authorizes one automatic summary after successful
  transcription completion, independent of browser connection. Persisted attempt
  markers prevent automatic paid retries. Summary failure leaves manual recovery.
- Cited summary titles replace automatic app/time names; session timestamps/source
  stay intact. Saved title updates reach both history and the conversation header.
- Verification: **360 Python tests passed**, eleven frontend regression groups,
  Ruff, 64-module import smoke and diff checks passed. Fake activity and ASR/LLM
  verified consent rejection, endpoint binding, automatic end, disconnect completion
  and topic naming. Brave accessibility/screenshot review covered the default
  layout and permission prompt using a preview with native recording disabled.
  Preview stopped; no real call, device capture or provider upload was performed.

Remaining: controlled real-call idle/mute/end validation across Teams/Slack/browser,
five-minute cap removal only after long-session design/validation, native desktop
notifications/packaging and narrow-layout QA. These are not passed by fake events.
