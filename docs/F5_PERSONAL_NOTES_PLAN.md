# F5 — Personal notes and important moments: implementation plan

Prepared 19 November 2026 against `feature/f4-action-tracking` `55701ba` (schema 27).
Status: **plan only; no F5 code written yet.** F1/F2 are closed by user approval;
F3 and F4 slices 1–4 are implemented with their acceptance slices remaining. F5
starts from `main` after F4 merges. Schema 28 is provisional; F5 also changes
summary consumers used by F4. Revised after the
[plan audit](F5_PERSONAL_NOTES_PLAN_AUDIT.md); the decisions below address all nine
findings at plan level. Implementation and acceptance remain unverified.
Requirements: [delivery plan §F5](MEETING_WORKSPACE_DELIVERY_PLAN.md#f5--personal-notes-and-important-moments).
Closeout artifact: `PERSONAL_NOTES_CLOSEOUT.md`.

## Outcome and boundaries

Jot down context or mark a moment without leaving the call. One personal-note
document per meeting autosaves the user's exact words; Mark moment bookmarks anchor
to the recorder timeline; both stay editable after the meeting ends. Notes are
private workspace data: searchable locally, excluded from AI context unless the
user turns inclusion on per meeting, and cited as **"Your note"** — never as
something a participant said, and never with a fabricated audio timestamp.

F5 reuses the encrypted workspace, the recording gate's shared sample clock, the
retained-audio playback index, the F1 search index and the F2/F3 grounded
generation pipelines. It does **not** add: a second summarizer or a second editor
paradigm, note sync, Markdown/outliner rendering, note-to-task conversion (manual
task creation in F4 already covers that link), new providers, or reminder
notifications. Typing itself uploads nothing. When inclusion is on, authorized
summary/Ask requests may send note text and eligible note-derived context to the
configured destination shown in the UI. Toggling inclusion never dispatches a
provider request by itself. Cloud embeddings never receive personal notes or
note-dependent summaries, even when summary/Ask inclusion is on. The
browser demo client does not receive this feature (the same native-first choice
F4 made).

In scope:

- Autosaving personal-note editor — one document per meeting, exact text preserved
  separately from ASR and LLM output; survives restart, transcript replacement and
  audio-expiry.
- Mark moment during recording (shortcut + recording-bar button) and post-hoc
  marks from saved playback, with the defined pause policy and expired-audio labels.
- Local keyword search of personal notes; local-embedding semantic search of notes;
  opt-in per-meeting AI inclusion for summaries and Ask with "Your note" evidence.
- Deletion, person-forgetting and reprocess (transcript replacement) behavior.
- Native macOS UI with keyboard operation, autosave recovery and every
  loading/empty/success/error state.

Out of scope (recorded as limitations if requested later): transcript-row inline
mark markers (scroll-performance risk in the virtualized transcript), mark-label
search indexing, browser demo UI, Linux GTK parity, notes exports beyond the
existing email flow, collaborative/multi-note documents.

## First implementation step

Branch `feature/f5-personal-notes` from `main` once F4 merges. Recheck the current
schema and allocate its next unused version; the schema 28 examples below assume
F4 ends at 27. Starting early requires an isolated branch with a provisional
migration, reconciled against main before merge; neither 28 nor 29 is reserved.
F4 integration is explicit: grounded summaries feed task suggestions, so slice 3
must prevent note-supported actions from losing their evidence type there.

All Python paths below are relative to `core/scribeloop_core/`; native paths are
relative to `shell/macos/Desktop/`. Line references describe the reviewed base
and must be rechecked when implementation starts.

## Code findings and proposed changes

| Area               | Existing implementation                                                                                                                                                                              | F5 change                                                                                                                                                         |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Storage            | `storage/db.py` `SCHEMA_VERSION=27`, additive `if version < N` migrations                                                                                                                      | Provisional schema 28: stable documents, immutable context snapshots, reversible marks and consumer provenance                                                    |
| Store pattern      | `storage/tasks.py` (`_known_session`, bounds checks, row builders, soft delete)                                                                                                                  | New `storage/personal_notes.py` `PersonalNoteStore` with the same guards                                                                                      |
| Delete cleanup     | `storage/sessions.py:810` `finish_delete` — explicit child-first table list, then `purge_checkpoint`                                                                                          | Delete note-dependent contexts across meetings, then marks/snapshots/documents; purge indexes and caches before `purge_checkpoint`                              |
| Recording timeline | `capture/recording_gate.py` — one shared sample clock; `_watermark`, `_intervals`, `_removed`, `_paused_at`; `_slice` computes saved position `start-removed`                         | Expose atomic `mark_target()` with captured sample position + pause state; track written and durable intervals per stream separately                            |
| Recording runner   | `pipeline/recording.py` `RecordingRunner.paused`/`set_paused` validate the active session and refuse while stopping                                                                            | `mark_moment(session_id)` snapshots the captured target without queue waits; durable metadata may remain pending until audio is checkpointed                    |
| Desktop reads      | `api/desktop_contract.py:79` `meeting()` returns bounded `meeting_page` (notes truncated at 100,000 chars)                                                                                     | Return full bounded personal document with stable ID, revisions/context version; marks include availability per stream and pending/error reasons                  |
| Desktop mutations  | `api/server.py:1009+` strict field-set dispatch with receipts and `invalidate` publishes (F4 task commands are the template)                                                                     | Six commands including label update and restore; exact payloads below, scoped errors/receipts and explicit transport limits on both sides                         |
| Search index       | `storage/search.py:103` `_documents` builds `title`/`transcript`/`summary` docs; `:89` `_fingerprint` drives reindex; `:263` `_semantic_chunks` embeds `transcript/summary` only | Add nonempty `user_note` documents; separate lexical and provider-eligible semantic fingerprints; Local-only note embedding                                     |
| Ask evidence       | `llm/meeting_ask.py:106` `retrieve` skips `title` hits; passages carry `source_type`; `_evidence_hash` includes it                                                                         | Gate direct and derived evidence, prior turns and in-flight results by context version; user-note citations have no speech or playback metadata                   |
| Summary generation | `storage/sessions.py:444` `summary_input`; `llm/grounded.py` prompt rules; `llm/schemas.py:34` `CitedText.segment_ids` (`min_length=1`)                                                  | Extend actual `NotesGeneration`/`RollingNotes` section/merge/request/cache/save flow with frozen note snapshots, validated `note_refs` and freshness checks |
| Native notes       | `DesktopApp.swift:1528` `CitedNotes` renders `[source](#anchor)` chips; citation jump at `:479,490` routes `source_type=="summary"` to the Notes tab                                       | Render `user_note` chips as "Your note" without time/speaker; open frozen evidence with a separate current-document link                                        |
| Native meeting UI  | `DesktopApp.swift:612` `notes` view; `:343–355` recording bar (Pause/Stop); `:418` segmented tabs Notes/Transcript/Audio                                                                    | "My notes" card at the top of the Notes tab; "Mark moment" button + ⌘⇧M in the recording bar; moments list; keep three tabs                                     |
| Playback           | `AudioView` + `audio_info`/`audio_window` seek by `start_ms` on `mic`/`loopback` streams; unavailable while recording                                                                    | Mark chips seek a stream covering the target; pending, missing and expired audio have explicit states; saved playhead creation validates coverage                 |
| Keyboard           | ⌘F Search, ⌘K Ask, ⌘. Stop (`DesktopApp.swift:261,266,350`)                                                                                                                                     | ⌘⇧M mark moment, ⌘⇧N focus the note editor (⌘M is macOS window minimize — avoided)                                                                          |
| Fixtures/smoke     | `api/desktop_fixture.py` synthetic workspace; `PlaybackSmoke` flow in `DesktopApp.swift:2179` asserts playback/seek and prints an ok-flags JSON                                                | Seed a personal note and marks in the fixture; extend the smoke with autosave, mark, seek, pause-label and recovery checks                                        |

## Data model — provisional schema 28

Use the existing additive migration pattern, updating `PRAGMA user_version` only
after the complete migration succeeds. Existing meetings need no eager note
backfill. An absent document reads as empty, inclusion off, revision 0 and context
version 0; its stable ID is deterministic from the meeting ID. First save/toggle
materializes that same ID at revision 1. Once created, it persists until meeting
deletion, including when content is cleared.

```sql
CREATE TABLE personal_notes (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
  revision INTEGER NOT NULL CHECK(revision >= 1),
  context_version INTEGER NOT NULL CHECK(context_version >= 1),
  content TEXT NOT NULL,
  ai_included INTEGER NOT NULL DEFAULT 0 CHECK(ai_included IN (0,1)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL);

CREATE TABLE personal_note_contexts (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  note_id TEXT NOT NULL REFERENCES personal_notes(id),
  note_revision INTEGER NOT NULL,
  context_version INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL,              -- frozen included text; encrypted workspace
  created_at INTEGER NOT NULL,
  UNIQUE(note_id, context_version));

CREATE TABLE note_marks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  sample_position INTEGER NOT NULL CHECK(sample_position >= 0),
  position_ms INTEGER NOT NULL CHECK(position_ms >= 0),
  during_pause INTEGER NOT NULL DEFAULT 0 CHECK(during_pause IN (0,1)),
  label TEXT,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  deleted_at INTEGER,
  created_at INTEGER NOT NULL);

CREATE INDEX idx_note_marks_session ON note_marks(session_id, position_ms);
CREATE INDEX idx_note_contexts_session ON personal_note_contexts(session_id);
```

Add `personal_context_json TEXT NOT NULL DEFAULT '[]'` to `note_generations`,
`summary_jobs`, `grounded_summaries` and `meeting_answers`. It records dependency
entries `{session_id, note_id, context_version, snapshot_id}`. Generation/job
entries with `snapshot_id=null` are version stamps for an off/empty document,
not note-derived evidence; verify their version and current inclusion state
against the frozen run configuration. Output dependency lists include only
non-null snapshot entries and their transitive dependencies. Thus enabling
inclusion invalidates an old off-state run, while a transcript-only historical
output remains independent of note text.
Immutable generation snapshots carry their source snapshot identities; existing
immutability triggers stay intact. All new writes set provenance explicitly.
Legacy artifacts predate F5 and get an empty dependency list. The migration must
also add provider-eligible semantic fingerprint support in
`embedding_index_state`; existing values are invalidated for a rebuild, not
reinterpreted. Bump the search index contract version.

Snapshots are immutable, created only for nonempty included notes and deduplicated
by document/context version. `note_refs` contain these opaque snapshot IDs, not
mutable document IDs. Only snapshots supplied to the current request are valid.
History resolves the frozen text; opening the current editor indicates when it
has changed. Clear/recreate never recycles document identity or revisions.

Content is limited to 60,000 Unicode scalar values; labels to 200. Python rejects
unpaired surrogates; Swift counts `unicodeScalars`, not grapheme clusters. Preserve
content verbatim: no trim, Unicode normalization or newline conversion. Only the
exact empty string is omitted from search. Whitespace-only text remains authored
data. Limit active marks to 500; deleted rows do not count, and restore checks
the cap. All guards reject missing/deleting/non-meeting sessions.

Both `DesktopClient.send` and desktop server dispatch must explicitly allow
`save_personal_note` envelopes up to **1,000,000 UTF-8 bytes**; other command limits
stay action-specific. This includes JSON escaping and protocol overhead. Full
60,000-scalar text fits even with twelve JSON bytes per non-BMP scalar
(surrogate-pair escaping), leaving room for the bounded envelope. Measure actual Foundation payloads at the boundary. `meeting_page`
returns the full note (no editable truncated slice); verify the combined response
with 500 maximally labelled marks against client/server read limits and raise
those bounds explicitly if necessary. Never save a partial read over a full note.

## Autosave contract

- **Trigger and deadline.** Debounce 700 ms after the last edit. Measure durable
  acknowledgement within 1 second of inactivity on healthy fixtures; debounce
  expiry alone is not a pass. Flush immediately on meeting/tab change, sheets,
  Stop and normal quit. Failures retain a visible unsaved draft.
- **Revision protocol.** Save and toggle both require `base_revision` (0 only for
  an absent row). An absent-row no-op returns revision 0 without materializing a
  row. First nonempty save or toggle-on creates revision/context version 1.
  Atomically compare before writing. Any content or inclusion
  change increments document revision and context version once; no-op writes
  return the existing version without invalidation. Clear retains metadata and
  inclusion. Accepted receipts follow commit and contain the authoritative
  `{id, revision, context_version, updated_at, ai_included}` and request ID.
  Stale bases return a scoped conflict with the full current document.
- **Client state.** Keep drafts keyed by meeting ID with edit generation,
  acknowledged revision, sent edit generation and request ID. Allow one save in
  flight per document; coalesce subsequent typing and send it using the returned
  revision. A late receipt marks only its submitted generation saved. Refreshes
  never overwrite a dirty editor, including A→B navigation and delayed A replies.
  Typing before the initial read stays a draft until its base is known.
- **Mutation sequencing.** Replace the single global pending-mutation exclusion
  with explicit compatibility rules: short saves for different documents and
  mark/label/restore/Pause actions may coexist, while same-document saves and
  toggles serialize. Unknown outcomes block dependent mutations, not unrelated
  documents or Mark/Pause. Destructive operations drain or invalidate relevant
  requests; preserve current guards for unrelated mutations. Stop/revoke retain
  their emergency path. Mirror session/deletion validation on the server.
- **Navigation and quit.** Navigation sends immediately and retains the old
  meeting's draft until acknowledged. Quit waits for pending saves before
  shutting down the core. A rejection/disconnect keeps the app open with Retry,
  Copy text and an explicit Quit without saving choice. Stop dispatches promptly
  even when its concurrent save fails; the draft remains available afterwards.
- **Conflict/reconnect.** Show Reload saved version and Keep my draft; neither
  automatically discards or resubmits text. Keep my draft explicitly retries
  against the displayed current revision. Reconcile unknown outcomes via request
  status and a full document read; never replay interrupted mutations. Single
  windows can conflict too, particularly around toggles and reconnects.
- **Status and recovery.** Show Saved · time, Saving…, Unsaved changes, save error,
  or conflict. Kill −9 preserves acknowledged text; unacknowledged drafts are
  memory-only. Test ack → kill → restart and unsent edit → kill → restart.
  Only `PersonalNoteStore` mutates authored text; summary/Ask generation never
  writes to the document.

## Marks and the recorder timeline

The gate uses one sample clock for both tracks and collapses pauses before
queueing frames. A mark selects the last included **captured** sample at command
arrival, not the last dequeued/written/checkpointed frame. The gate exposes an
atomic `mark_target()` snapshot `{sample_position, position_ms, during_pause}`
under its existing lock. Compute the included endpoint from watermark minus
removed intervals (freeze the endpoint at `_paused_at` while paused); select
endpoint minus one sample to avoid an unseekable EOF position. Reject when no
included sample exists. Millisecond conversion follows the recorder's rounding.

Store this exact target as durable mark metadata before accepting the command.
Do not wait for disk audio or a provider in desktop dispatch. A mark can therefore
be accepted with `anchor_state=pending`: “Marked at 12:34 · audio saving”. Never
substitute an older written position. Resolve it when a writer checkpoint covers
that sample; if capture fails before that, keep the original anchor with
“Audio unavailable at this position”. Metadata acceptance does not promise that
its audio was durable. Generated healthy fixtures must verify both ≤500 ms target
error and prompt availability; report acknowledgement and audio-ready latency
separately, including stalled-disk cases.

`_capture` tracks half-open written intervals per stream after a successful
`write`, using `sample_offset + pcm.size` for endpoints. Durable intervals advance
only after `checkpoint`/close fsync succeeds. Publish progress to the owning DB
thread; never access its SQLite connection from the writer thread. Gate targets
and pause state come from one snapshot; progress is separate and cannot change
that target. Reconcile pending marks against authenticated retained audio on
restart. Delayed/missing mic or loopback packets do not imply availability on
both tracks. Existing checkpoint frequency may delay readiness and must be
measured; do not add blocking per-mark fsync to dispatch.

Reads derive `anchor_state` and available streams from interval coverage, retention
and recovered prefix information. During recording, playback remains disabled
with “Available after recording” even for durable targets. For saved playback,
seek only a stream covering the sample; round/clamp within its actual interval
without changing the stored target. Session-level “has audio” is insufficient.
Expired audio says “Audio no longer retained”; partial/recovered/missing audio has
its own explanation. Anchors survive expiry, restart and transcript replacement.

Pause policy: use the last included sample and `during_pause=1` from the atomic
arrival snapshot; no excluded audio is implied. Test before-pause/during-pause/
after-resume, exact resume boundaries, EOF, delayed streams, startup, checkpoint
failure and recovery. Error is measured against the selected captured target,
never merely against persisted position. Stall fixtures must preserve target
accuracy or explicitly fail creation, not silently move the mark backwards.

## Desktop API surface

`meeting_page` adds full `personal_note: {id, content, revision, context_version, updated_at, ai_included}` and active `note_marks: [{id, revision, position_ms, during_pause, label, anchor_state, available_streams, unavailable_reason}]`.
The absent note uses the revision-0 representation defined above. Selection,
reconnect and scoped invalidation refresh these fields without replacing drafts.
The recording snapshot also exposes `can_mark` and its disabled reason.

Each payload below includes `action` in addition to the listed fields. No extra
fields are allowed; all IDs must belong to the given session. All **six** commands
have request-ID-bound receipts/errors and native request tracking.

| Command                       | Fields                                                   | Behavior                                                                                                              |
| ----------------------------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `save_personal_note`        | `session_id, content, base_revision`                   | Revision-checked exact-text upsert; accepted means committed                                                          |
| `set_note_ai_included`      | `session_id, included, base_revision`                  | Boolean toggle, same revision protocol; no provider dispatch                                                          |
| `add_note_mark` (recording) | `session_id, label?`                                   | Atomic captured target; client position/pause overrides forbidden                                                     |
| `add_note_mark` (saved)     | `session_id, from_playhead: true, position_ms, label?` | Integer position must resolve inside retained audio; requires no active capture for this meeting; pause flag is false |
| `update_note_mark`          | `session_id, mark_id, label, base_revision`            | Label only; exact bounded text or null to clear; cannot move anchor                                                   |
| `delete_note_mark`          | `session_id, mark_id, base_revision`                   | Soft delete, bump revision; receipt includes revision for undo                                                        |
| `restore_note_mark`         | `session_id, mark_id, base_revision`                   | Restore same identity/target/pause/creation time, bump revision, enforce cap                                          |

Save/toggle emits `note_changed` only on change; mark mutations emit scoped
invalidation. Deleted rows survive for undo until meeting deletion; only active
rows are returned/counted. Retrying with the same request ID uses existing receipt
deduplication; a new ID with a stale revision conflicts. Undo is explicit restore,
never a new mark. Mark updates, delete and restore also return authoritative
revisions. Reject creation while stopping/deleting; saved-playhead validation
rejects booleans, negatives, beyond-end positions and uncovered audio gaps.

## Search and AI inclusion — the evidence contract

### Local search and embedding privacy

Append a nonempty `user_note` document with ID `{sid}:personal_note`, stable
`source_id=note.id`, no timestamps or speakers. Its source hash includes exact
content identity. Local keyword search (FTS5 and LIKE fallback) always finds it,
regardless of AI inclusion. Lexical synchronization remains atomic and indexes
clears as removal of text, not deletion of document metadata.

Separate lexical fingerprints from semantic fingerprints. Lexical identity
includes note content/hash and summary freshness; semantic identity hashes only
the actual eligible chunks plus provider/model/index contract. With Local
embeddings, notes and locally searchable note-dependent summaries can join the
index even when AI inclusion is off. With any cloud embedding provider, exclude
both direct notes and **all note-dependent summaries**, even while inclusion is
on. This conservative rule prevents indirect upload through summary text.

A note-only edit on a meeting whose eligible cloud sources are unchanged causes
zero document embedding calls; a query may still need its own query embedding.
Handle Local→cloud→Local configuration changes and old vector generations without
reusing ineligible chunks. Test embedder inputs, not only result filtering. Add
`note_refs` and context metadata to `_summary_text`'s excluded identifier fields.

### Context eligibility, revocation and historical output

Every summary run freezes the current personal-document context version, even
when inclusion is off. Included text becomes an immutable snapshot; each output
inherits the union of all supplied note dependencies, whether or not the model
cites them. Ask inherits dependencies from direct notes, retrieved summaries and
prior turns. Do not infer dependence solely from emitted citations.

Before every provider dispatch, cached-job/prior-turn reuse, and result commit,
verify that every note dependency still belongs to the meeting, its context
version matches, and inclusion remains on. Summary runs also reject an off→on
version change. Capture versions at scheduling and recheck after every awaited
provider boundary. A change invalidates affected jobs/results and dependent
continuations; stop remaining section/merge dispatches and require an explicit
new run. Already transmitted content cannot be recalled.

Turning inclusion off does not erase historical generated output. Show it locally
with “Uses an earlier version of your notes; excluded from new AI requests” and
retain frozen source labels. It cannot be retrieved as AI evidence, embedded in
the cloud, or sent through `prior_turn`. A dependent follow-up is rejected with
“Personal-note context changed; start a new question”; it is never silently
resent. Turning back on does not revive older context versions. Note edits and
clears also invalidate dependent history for reuse, leaving authored text intact.

### Ask

Filter eligible evidence **before ranking/limit** so excluded note hits cannot
consume the passage budget or starve transcript retrieval. Apply the same policy
to lexical and semantic candidates, all Ask scopes, summary hits, evidence reads
and prior-turn validation. Recheck dependencies at dispatch and commit.

Before dispatch, resolve each included direct-note hit into a frozen snapshot
and record its dependency in the packet. User-note passages carry
`source_type='user_note'`, no timestamps/speakers, stable document identity and
frozen snapshot/context identity. Restrict allowed source
types explicitly when constructing/validating packets; current `validate_answer`
checks citation membership, not a source-type allowlist. Answer citations must be
members of the supplied packet. Prompts attribute notes as “your note,” preserve
contradictions with speech, and treat all text as untrusted data.

### Summary pipeline and grounding

Extend `NotesGeneration.prepare` to freeze personal context alongside F3's
editorial/provider snapshot. Pass it through server manual/automatic dispatch,
`RollingNotes.run`, section requests, reductions, provider schema construction,
node validation and retries. Updating `summary_input` messages alone is
insufficient: the current server requests rows with `build_messages=False`, and
`RollingNotes` builds the actual requests itself.

Each included snapshot appears as delimited untrusted user-authored data. Keep
all requests within existing byte/token limits; if an included note cannot fit,
fail visibly with guidance to shorten it or disable inclusion. Never truncate
silently or introduce a separate paid summarizer. Rolling runs that see a context
change stop; a subsequent authorized run freezes a new context. Section/reduction
cache keys and input hashes include snapshot/version identity and inclusion state.
Reduction propagates both segment IDs and note refs, including note-only nodes
with no segment IDs (remove the current `next(iter(ids))` assumption). Reuse cached
nodes only when all frozen dependencies remain eligible.

`CitedText`/`CitedAction` gain `note_refs: list[str]` defaulting to empty for legacy
outputs, bounded to the supplied snapshots. Allow empty `segment_ids` only when
valid note refs exist; require at least one reference overall. Provider enums and
`validate_summary` restrict refs to snapshots supplied to that particular request,
matching meeting, version and hash. Unknown, cross-meeting and old refs fail.
Claims may cite both evidence kinds when both support them. Prompt rules forbid
attaching speech IDs merely to give note-only text a playback affordance.
Structural validators cannot prove semantic support; contradictory-provider
quality checks verify attribution, uncertainty and absence of invented speech.

Extend `save_grounded_summary`, summary freshness/cache logic, Markdown/native
rendering and existing export/email consumers to understand note refs. Input and
content hashes include frozen personal context, with a legacy transcript-only
branch for pre-F5 summaries. Do not derive the meeting title from a run supplied
with personal-note text: preserve its existing title or obtain a transcript-only
title through an already authorized transcript-only path. A note-derived auto-title
would otherwise leak into unrelated search/Ask title metadata after revocation.
No additional provider request is introduced for title generation. Preserve F3
editorial section behavior. Historical
citations open frozen note evidence labelled “Your note”; offer a separate link
to the current document if it differs. No time, speaker or play control appears
on a note ref, including exports and reduction-produced citations.

### F4 task-suggestion compatibility

F5 does not extend task evidence storage. Exclude any generated action containing
`note_refs` (including mixed evidence) from F4 suggestion seeding; only actions
with transcript evidence and no note dependency at item level use the existing
suggestion path. Such actions remain visible with “Your note” citations in the
summary; manual task creation remains available. Never strip `note_refs` and
present the result as spoken evidence. Test normal transcript suggestions,
excluded note-only/mixed actions, acceptance, carryover and source deletion.

### Deletion, transcript replacement and person forgetting

Meeting deletion purges authored rows, deleted marks, snapshots, lexical/vector
text, generation/jobs, note-derived histories and caches. For cross-meeting Ask
threads, purge the whole dependent thread, including descendants and prior-turn
copies, before removing its source context. Walk stored dependency metadata, not
only `meeting_answer_sources`. Extend existing child-first cleanup in one owning
DB transaction where supported, then checkpoint/purge encrypted storage.

Transcript replacement preserves authored text, inclusion and mark targets while
invalidating transcript-dependent generated contexts as today. Person forgetting
preserves exact authored notes and unlinked mark labels; it removes managed person
links and invalidates affected derived summaries, jobs, Ask threads and caches.
Explain that forgetting a managed identity does not redact names manually typed
in personal notes; never rewrite that text automatically. Determine affected
meetings from existing registry links and dependencies, then exercise the full
invalidation path, including note-containing outputs in those meetings.

### Required evidence regressions

Transcript says budget 60k; note says 50k. Search finds the note with inclusion
both off and on. With inclusion on, Ask and every notes template preserve both
sources, disagreement and uncertainty; note citations have no speech metadata.
With inclusion off, neither direct notes nor note-derived summaries/prior turns
enter a new provider request. Cover on→generate→off→follow-up, off→on during a run,
note edit/clear during generation, stale cache reuse, re-enable, cross-meeting
scope, auto-title leakage and source deletion. Separate deterministic fake-provider contract tests
from actual configured-provider quality review and record both results.

## Native UI/UX design

Placement follows the delivery plan's user outcome — "jot down context or mark a
moment while staying in the call" — and the app's existing structure:

### Information architecture

- **Personal notes live in the Notes tab**, as the top card above generated
  notes. Rationale: it is available in both recording and saved states without
  adding a fourth segment to the meeting-view picker (four segments with
  Vietnamese labels risks the narrow-layout overflow the UX review warns about),
  and it visually enforces the separation between the user's own document and
  LLM output. The generated-notes content below it can never scroll the personal
  card out of reach while typing.
- **Marks are actions, not a destination.** During recording, the recording bar
  gains a Mark button; afterwards, the Audio tab and the moments list provide
  access. No modal is ever shown for marking — the call keeps the user's
  attention.

### "My notes" card (Notes tab, both states)

```
┌ My notes ─────────────────────────── ⌘⇧N ┐
│ [TextEditor · placeholder: "Jot context,  │
│  decisions you heard, or anything to      │
│  remember…"]                              │
│ Saved · 14:32   ·  1,204 characters       │
│ [ ] Include my notes in AI notes and Ask  │
│     Next AI request uses the selected    │
│     provider · model · destination.       │
│     Cited as "Your note".                 │
│ Moments (3)                    ⌘⇧M marks │
│ ● 12:34 · During pause ─ "pricing doubt"  │
│ ● 27:10                                   │
└───────────────────────────────────────────┘
```

- **Editor.** Bordered `TextEditor` in the notebook's body style (not
  monospaced); min-height 120pt growing with content up to a scroll cap. Focus ring visible; `⌘⇧N` focuses it. Typing is never blocked by
  any loading state: the editor renders immediately from local state and
  reconciles with `meeting_page` using per-meeting revisions and edit generations;
  dirty drafts are never replaced by refreshes.
- **Autosave status line** (see contract): secondary text at ≥ 4.5:1 contrast —
  the UX review flagged 3.93:1 secondary text and 8–11 px labels, so status uses
  `.caption` minimum and `ScribeTheme` ink colors, not pure secondary gray.
- **AI inclusion toggle.** Off by default. Label: "Include my notes in AI notes
  and Ask". When on, the destination line names the currently configured notes
  provider/model and cloud/local destination from the actual frozen request
  configuration; show separate Notes/Ask destinations if they differ. Reuse
  `core.notesDestination` presentation where applicable, but its current text
  alone does not include the model. Mid-recording changes are allowed; changes
  invalidate dependent runs and cached context according to the evidence contract.
  Historical outputs keep their provenance and are labelled when no longer eligible.
  No per-run consent checkbox is introduced (spec requirement) — the app-level
  agreement and this visible destination line are the consent surface.
- **Moments list.** Rows show `MM:SS` position, "During pause" badge when
  applicable, optional label, and a delete control with undo toast. Clicking a
  row with a covering retained stream switches to the Audio tab and seeks. During
  capture, pending persistence, expiry or a missing interval, show the specific
  availability reason and disable seeking; never label every failure as expiry. Empty state: "Use ⌘⇧M or
  the Mark button to bookmark a moment." — shown only while recording or when
  audio exists; a saved meeting with expired audio shows the list without
  seek affordances.
- **States matrix** (shared finish gate): loading (editor skeleton never blocks
  typing; moments show a compact ProgressView row), empty (placeholder text),
  success (Saved · time), error (save failure: orange inline note + Retry now
  button; the draft is retained in memory), interruption (unknown outcome with
  reconciliation; no automatic replay), conflict (two-option bar above). Mark
  controls also show pending audio, unavailable interval and expired states.
- **Delete meeting copy.** The confirmation at `DesktopApp.swift:499` gains
  "your personal notes and marks" alongside audio/transcript/notes/tasks so the
  destruction is explicit before confirm.

### Mark moment interaction

- **Recording bar** (`DesktopApp.swift:343–355`, next to Pause/Stop): a
  `Label("Mark", systemImage:"flag")` bordered button. Pressing it sends
  `add_note_mark` and, on the receipt, pulses the button briefly and posts a
  transient toast: "Marked at 12:34" (or "Marked at 12:34 · during pause"),
  appending "audio saving" while the target is pending.
  No focus is stolen; the user stays in whatever they were doing. Disabled with
  tooltip "No included audio captured yet" until the first included sample; a
  captured target may then be pending while audio is being saved.
- **Pause coupling.** When `core.recordingPaused`, the button label stays Mark
  and the toast carries the "during pause" wording; the recording bar's existing
  paused explainer row gains one sentence: "Marks attach to the last included
  audio position; paused audio is not saved."
- **Shortcut.** ⌘⇧M registered on the notebook window, active only while a
  recording is active (and also allowed while paused). Documented in the button
  help text.
- **Optional label.** The toast row offers an inline "Add label…" text field for
  ~6 s; submit via `update_note_mark` with the returned revision. Ignoring it
  keeps an unlabeled mark; failed updates retain the draft and offer Retry.
  Labels are bounded to 200 Unicode scalars and validated server-side. The list
  also allows later label editing. Delete/undo uses revision-checked soft deletion
  and `restore_note_mark`, retaining the original anchor even during recording.
- **After the meeting (saved meetings).** Audio tab gains an "Add mark at
  12:34" control at the playhead (uses `from_playhead`), and transcript-row
  context menus are explicitly **not** modified (keeps the virtualized transcript
  untouched; revisit only if requested).
- **Moments in the Audio tab.** Mark chips render above the transport controls
  (a horizontal chip row — no custom timeline drawing, no scrollbar surgery):
  click = seek; the current-position chip is highlighted as playback passes it.

### Search and Ask rendering

- `MeetingSearchView` result rows: "Your note" badge instead of the timestamp
  chip; opening the result selects the meeting and switches to the Notes tab with
  the note card highlighted briefly (border pulse), matching how F1 opens
  transcript passages today.
- `AskChatTurn`/`AskChatAnswer`: "Your note" citation chips without time or
  speaker; clicking opens frozen evidence, with an explicit link to the current
  document. Ineligible historical answers show the context-changed label and
  cannot be continued. Retained-audio
  seek appears only for transcript citations whose source meeting still has
  audio — the delivery-plan flow's Yes/No branch, which the existing
  cross-meeting evidence logic already implements for tasks.

### Accessibility, keyboard and visual standards

- **Keyboard-only operation**: Tab reaches editor → status → toggle → moments
  rows; ⌘⇧M works from any tab while recording; ⌘⇧N focuses the editor;
  Escape in the label field cancels the pending label. No keyboard trap; focus
  stays at its original control after a mark toast; leaving the explicit label
  editor returns focus to its invoking control.
- **VoiceOver**: editor labelled "My notes for this meeting"; moments rows read
  "Mark, 12 minutes 34 seconds, during pause, pricing doubt"; toggle reads its
  state; status changes are announced politely (state-only announcements, per
  the earlier transcript fix pattern — no per-save spam).
- **Contrast/size**: all new secondary text ≥ 4.5:1, minimum `.caption`
  (11 px); no 8 px labels; long Vietnamese note content wraps and scrolls
  without clipping; the card reflows down to the 980×680 minimum window.
- **Motion**: mark pulse and highlight are subtle (≤ 300 ms) and respect
  reduced-motion.
- **Error tone**: save errors never imply data loss beyond what actually
  happened ("Saved copy is older than your draft — Retry now or copy your text").

## Slices and verification

Slice 1 — **Storage, provenance and migration.** Stable `PersonalNoteStore`,
context snapshots/dependency columns, reversible marks and cleanup wiring. Tests:
first save/toggle from revision 0; no-op receipt; exact Unicode preservation;
empty clear retains ID/inclusion; clear→recreate rejects stale writes; concurrent
save/toggle conflicts; 500-active-mark cap and restore at cap. Upgrade populated
schema 27 to the selected migration, preserving meetings/settings/keys; test
rollback guidance and rejection of newer schemas by older app versions. Delete
removes snapshots, deleted marks, cross-meeting derived threads, caches and index
text before `purge_checkpoint`. Person forgetting preserves authored text while
invalidating affected generated contexts. Bounds tests must preserve the exact
accepted text, including whitespace and combining marks.

Slice 2 — **Captured targets and mark lifecycle.** Atomic `mark_target`, per-stream
written/durable interval tracking and all four mark mutations. Timing fixtures
measure target error against the **selected captured sample** (≤500 ms), receipt
latency and time to audio-ready separately. Cover speech, before/during/after
pause, exact resume, startup rejection, frame-end/EOF, delayed/missing stream,
backlog, checkpoint failure, restart recovery and retained gaps. Pending marks
retain their target through stalls; no test substitutes persisted position as its
reference. Test saved-playhead bounds, label updates, stale revisions and
undo while capturing/paused; marks survive transcript replacement and expiry.

Slice 3 — **Search and complete AI evidence pipeline.** Lexical documents,
separate semantic fingerprints, frozen context/version checks, all summary paths,
Ask/history, validators/rendering/export and F4 compatibility. Tests: FTS5/LIKE;
Local note embedding; cloud embedder never receives direct or derived note text;
already-indexed cloud meeting with note-only edits performs no document embedding;
Local→cloud→Local transitions. Exercise every Ask scope, pre-limit filtering,
prior turns, in-flight revocation, note edits/clear, re-enable and source deletion.
Test manual/automatic/rolling sections and multi-level reductions, including
note-only nodes, byte-budget failures, cached jobs and explicit retries. Reject
unknown/cross-meeting/stale `note_refs`; accept valid note-only/mixed refs and
legacy summaries. Structural tests validate source identities; configured-provider
contradiction/template acceptance checks semantic attribution and uncertainty.
Two explicit regeneration runs preserve immutable provenance. F4 tests verify
note-supported actions are excluded from suggestion seeding while transcript-only
suggestions and existing acceptance/carryover/source-resolution flows still work.

Slice 4 — **Desktop transport and native UI.** Notes card, recording-bar Mark,
moments, Audio chips, evidence/history rendering, request tracking and autosave
sequencing. Test all six commands' receipts, no-op/conflict/errors, request IDs
and reconnect reconciliation. Round-trip actual Foundation payloads at content
and envelope limits, including Vietnamese combining marks, emoji, escaping,
invalid scalars and full reads with 500 long-labelled marks. Native regressions:
one save in flight per document; newer typing during save; toggle serialization;
A→B with delayed A replies; Mark/Pause during autosave; unknown outcome isolation;
Stop/save failure; quit waits for ack and failed quit retains text. Smoke the
≤1-second durable autosave budget, kill−9 recovery, shortcut/focus, label/undo,
pending/expired/gap availability, mark seek, VoiceOver, long English/Vietnamese
content, reduced motion and minimum width. Rebuild the preview app; production
capture/Stop/playback/provider-selection smoke must still pass.

Slice 5 — **Acceptance and closeout.** Install a development macOS build and
exercise the finish gate with a human. `PERSONAL_NOTES_CLOSEOUT.md` records fixture
versions, model/provider/configuration, autosave and mark measurements (including
stall and audio-ready latency), privacy/context-invalidation evidence, actual
provider quality results, migration/rollback checks, limitations and failed gates.
Fixture tests do not complete signing/notarization or physical-call release gates.

## Migration, rollback and index recovery

- Allocate the migration from merged main. Apply all tables, dependency columns
  and index-contract changes atomically under the existing SQLCipher migration
  mechanism; test rollback of an injected migration failure before version bump.
- Before upgrading a real workspace, document and exercise a consistent encrypted
  backup/restore procedure, including required keys and retained audio metadata.
  Reuse existing tooling if available; its availability is a slice 1 check, not
  an assumed shipped feature. Never copy a live WAL database as a standalone file
  or include secrets in logs. Validate restoration on an isolated copy.
- No destructive down-migration is supported. The reviewed `db.migrate` has no
  explicit newer-version refusal before its migration branches. Add that guard
  to F5 and to any maintained rollback build before declaring it supported; do
  not assume previously installed binaries refuse schema 28. Unsupported older
  binaries must not open an upgraded workspace. Supported downgrade restores the
  pre-upgrade encrypted backup with matching keys, then opens it with the matching
  app. Later F5 edits will not exist in that restored copy.
- Lexical/local vector rebuilds read the encrypted workspace without rerunning
  ASR or uploading audio. Cloud vector rebuilding uses only eligible text under
  the existing explicit search authorization, budgets and selected provider;
  upgrade alone never starts cloud work. Old vector generations are invalidated
  or purged through existing provider-generation cleanup, never treated as fresh.

## Error handling and recovery

1. **Save failure mid-recording** — inline error + Retry now; draft retained in
   memory; a later acknowledged save of that edit generation supersedes the error.
   Never blocks Stop.
2. **Receipt unknown after disconnect** — the client reconciles from the next
   `meeting_page`/snapshot rather than replaying (no mutation replay after
   restart, per the macOS app plan audit).
3. **Mark command while stopping/cleanup** — rejected with "Recording is
   stopping"; the button disables during the existing "Stopping capture…" state.
4. **DB failure during autosave** — propagates as a scoped desktop error with
   the request ID (F4 follow-up pattern), connection stays usable.
5. **Oversized paste** — show the 60,000-scalar limit and retain the full draft
   in memory for copying; do not silently truncate exact text. Saving stays blocked
   until the user shortens it. Server rejects oversize/invalid Unicode with a
   scoped limit error; byte-limit errors remain distinct.
6. **Failed transcription** — authored notes work on valid non-deleting meeting
   sessions, including `failed`/`interrupted`, without a transcript. Live marks
   require included captured audio; saved marks require covered retained audio.
   Summary generation retains its existing nonempty-transcript requirement.
7. **AI context changed** — stop further dispatch, discard stale in-flight output,
   and explain that a new explicit generation/question is required. Historical
   output stays locally reviewable with its source-version label.
8. **Pending mark loses audio** — keep metadata, label the unavailable interval,
   and disable seek. Missing-stream and retention errors are distinct.

## Acceptance mapping (delivery plan §F5)

| Required criterion                                                                                                                             | Where it is delivered/verified                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Autosave ≤ 1 s of inactivity; immediate on navigation/normal quit; force-stop preserves acknowledged saves; unsaved visible; no LLM overwrite | Autosave contract + slice 1/4 tests + fixture smoke kill −9 case                                    |
| Marks resolve ≤ 500 ms on timing fixtures; pause/expiry labelled and tested                                                                   | Slice 2 captured-target fixtures + pending/durable intervals + expiry/gap UI labels                  |
| Notes/marks survive restart and transcript replacement; anchors are recording time; unavailable anchors explained                              | Integer `position_ms` anchors + slice 2 regression + "Audio no longer retained"                    |
| AI inclusion defaults off; on-state shows destination and source type under the existing agreement; no per-run checkbox                        | Toggle design + actual destination/model line + direct/derived/prior-turn revocation tests           |
| Search/Ask/templates regression with contradictory notes; attribution and uncertainty preserved                                                | Contradiction matrix across templates + structural validation and configured-provider quality checks |
| Keyboard shortcut, editor focus, autosave recovery, use while recording verified; deleting a meeting removes notes/marks/derived contexts      | Slice 4 keyboard/recovery matrix + dependency-aware deletion and person-forgetting regressions       |

Plus the shared finish gate (docs updated, regressions green, upgrade preserves
workspace, no hidden provider switch, evidence artifact, dev build review).

## Risks and mitigations

- **Context leaking through history/derived output** — dependency provenance and
  version checks gate retrieval, every dispatch/cache reuse and result commit.
  Cloud embeddings exclude note-dependent output entirely.
- **Autosave latency and mutation contention** — per-document coalescing, explicit
  compatibility rules and receipt-bound edit generations; measure durable saves,
  including Mark/Pause and navigation contention.
- **Mark readiness under storage backlog** — preserve the captured target as
  pending metadata, track durable coverage per stream and report readiness/error
  explicitly. Never present a substituted old point as accurate.
- **F4/schema coupling** — allocate version after merge and test suggestion
  seeding; v1 skips note-supported suggestions rather than losing provenance.
- **Provider quality and request size** — grounded identities are checked
  structurally; actual contradiction quality needs configured-provider acceptance.
  Oversized included context fails visibly without silent truncation or paid retry.
- **Focus during calls** — no modal or autofocus for marking, user-initiated note
  focus shortcut, and label controls return focus to their invoker.

## Deferred work and inherited contracts

F6 follow-up drafts must inherit the same per-meeting inclusion, dependency and
revocation contract; F5 does not implement F6. Mark-label search, macOS Services
integration and task provenance for note-supported suggestions remain out of scope
v1. Browser/Linux UI and transcript-row markers remain documented limitations.

## Plan-audit resolution map

These are design resolutions, not implementation verification or user acceptance.
The original audit remains the record of the reviewed version.

| Audit finding                             | Revised contract and required evidence                                                                          |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| 1 — Derived/prior-turn inclusion bypass  | Context eligibility/revocation; slice 3 on→generate→off→follow-up and in-flight tests                        |
| 2 — Actual summary pipeline missing      | Frozen context through NotesGeneration/RollingNotes, reductions, provider schema, cache/save/freshness; slice 3 |
| 3 — Empty deletion breaks identity       | Persistent document, revision-0 initial read, monotonic versions, no-op/toggle protocol; slice 1                |
| 4 — Transport and Unicode limits         | Explicit 1,000,000-byte save allowlists, Unicode scalars, full-read bounds; slice 4 Foundation round-trip       |
| 5 — Incorrect mark reference/persistence | Captured target with pending audio, durable intervals per stream, captured-reference timing fixtures; slice 2   |
| 6 — Missing label/undo APIs              | Revision-checked update/delete/restore, strict playhead variant; slices 2/4                                     |
| 7 — Autosave/native gate contention      | Per-meeting edit generations, compatible mutation lanes, quit acknowledgement; slice 4                          |
| 8 — Cloud re-embedding churn             | Provider-eligible semantic fingerprints and zero document-embedding regression; slice 3                         |
| 9 — Forgetting and F4 consumers          | Preserve authored text, invalidate derived contexts, skip note-supported suggestions; slices 1/3                |

Rollback, populated-workspace upgrades and authorized index recovery are explicit
shared-finish-gate requirements above.

## Implementation notes from pre-implementation verification — 19 November 2026

Recorded while rechecking this revised plan against `55701ba` before slice 1
(full test suite and ruff rerun on this branch; all cited line references
verified). These do not change the contracts above; they pin implementation
details so slice 1 starts with them resolved.

- **Baseline.** `1,046 passed, 2 skipped (42.4 s)`; `ruff check` clean;
  `SCHEMA_VERSION=27` (`storage/db.py:24`). The working tree contains only the
  F5 plan/audit documents.
- **`settings_saving` exemption.** While settings persist, dispatch rejects most
  actions (`api/server.py:821-822`, "Wait for the settings save to finish").
  The personal-note commands are independent of Keychain settings; add them to
  that exception set and cover typing-during-settings-save in slice 4, or
  autosave shows spurious errors whenever settings are edited mid-call.
- **Scoped error set.** Desktop-scoped errors with request IDs are emitted from
  the action list in the dispatch exception handler (`api/server.py:~1541`).
  All six F5 commands must be added there; omission reproduces F4 follow-up #1
  (failures fall to the replay branch with `request_id: None`).
- **`add_note_mark` toast payload.** Receipts carry status only
  (`DesktopContract.receipt`). The "Marked at 12:34" toast needs the resolved
  mark, and the command table promises authoritative revisions: return them via
  the `response_to` event pattern used by `delete_ask_history`
  (`api/server.py:824-830`) rather than inventing receipt payloads.
- **Search row labels.** `MeetingSearchView` renders `source_type.capitalized`
  (`DesktopApp.swift:843`), which would display "User_note". Map `user_note` to
  "Your note". Null `start_ms` already hides the timestamp and the speaker line
  stays empty, so only the label needs work.
- **Recording auto-selection.** `DesktopClient.swift:778` already selects the
  active meeting when recording starts, so the Notes tab — with the
  personal-notes card — is in front without extra work.
- **Threading precedent.** The recording pipeline already calls `store.*` from
  capture/transcription threads (`pipeline/recording.py:287-469`) while
  dispatch handlers run on the loop thread; dispatch-path note writes during
  recording follow existing practice. Slice 4 still regression-tests autosave
  during active capture.
- **`CitedText`/`CitedAction` use `extra='forbid'`** (`llm/schemas.py:35,42`),
  so `note_refs` must be added to the schemas before any provider output
  containing it can parse; legacy outputs without the field keep validating.
