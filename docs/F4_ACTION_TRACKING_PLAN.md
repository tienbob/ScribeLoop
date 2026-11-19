# F4 — Action-item tracking: implementation plan

Prepared 18 November 2026 against main `364aa95`. Status: **slices 1–4 implemented on
2026-11-18 (core, API, native UI); slice 5 acceptance/closeout remains.**
Revised 18 November 2026 after code and SQLite contract review; verification
record and required regression cases are below.
Requirements: [delivery plan §F4](MEETING_WORKSPACE_DELIVERY_PLAN.md#f4--action-item-tracking).
Closeout artifact: `ACTION_TRACKING_CLOSEOUT.md`. F1/F2 are closed; F3 slices 1–4 are
implemented with slice 5 (acceptance/closeout) remaining. F4 has no code dependency
on F3 beyond migration numbering (schema 26), so it can start on a separate branch;
see "First implementation step".

## Outcome and boundaries

Turn a cited action item in saved notes into a durable task, and see what remains
open across all meetings. Users review each summary suggestion, accept/edit/dismiss
it, create manual tasks, change owner/due/status later, and review open tasks from
earlier related meetings before a repeat meeting. Tasks survive summary
regeneration, restarts, transcript revisions and provider changes.

F4 reuses the existing cited-summary pipeline and people registry; it does not add
a second extractor, a reminder/notification system, natural-language due-date
parsing, external task-system sync (that is F6b), email sending, or new provider
calls. Tasks are local workspace data: no ASR model loads, no audio or transcript
text leaves the Mac, and no task mutation is ever triggered by transcript content.
Search indexing of tasks is out of scope for F4.

## Code findings and proposed changes

| Area | Existing implementation | F4 change |
| --- | --- | --- |
| Extracted actions | `CitedAction{task,owner,due,segment_ids}` in `llm/schemas.py`; owners/dates only when explicitly stated (`llm/grounded.py` prompt rules) | Suggestions read from saved `GroundedSummary.action_items`; nothing re-extracted |
| Saved summaries | `grounded_summaries`, `sessions.py` `save_grounded_summary`/`grounded_summary`, `stale` flag, regeneration inserts a new row | Regeneration triggers suggestion reconciliation; tasks are never rewritten by it |
| Storage | `storage/db.py` `SCHEMA_VERSION=25`, additive `if version < N` migrations, trigger-immutable patterns | Schema 26: `tasks`, `task_suggestions` tables, indexes and a fingerprint uniqueness guard |
| People/owners | `speakers`, `person_links`; `SpeakerRegistry.forget()` clears derived data; `sessions.py` delete uses an explicit table list | Owner link to a person plus a free-text label; forget and meeting-delete cleanup extended to both new tables |
| Desktop reads | `api/desktop_contract.py` `meeting_page`, bounded `people_page` pagination (200/page) | `meeting_page` gains `tasks`/`suggestions`; new bounded `tasks_page` and `task_carryover` reads |
| Desktop mutations | `api/server.py` strict command dispatch, receipts, `invalidate` publish; `create_person/link_person/forget_person` pattern | Task CRUD/restore, suggestion accept/dismiss/undo/link, explicit carryover acceptance |
| Native meeting UI | `CitedNotes` renders action items read-only with `Owner: … · Due: …` caption and Source chips (`DesktopApp.swift:1466`); Notes tab `DesktopApp.swift:569` | Review strip under action items, per-meeting Tasks card, task editor sheet, delete-with-undo |
| Native global UI | Sidebar Search button opens `MeetingSearchView` sheet (`DesktopApp.swift:259,452`); People sheet | Sidebar "Tasks" opening a global task-list sheet with filters and recently-deleted restore |
| Carryover input | Meeting titles in `sessions`; acceptance fixture includes repeated meeting names | Deterministic same-title series detection feeding an explicit carryover card |
| Tests | `tests/test_template_ui_contract.py` contract-style tests, fake-provider suites, migration reopen tests | New `tests/test_tasks.py` + UI contract tests; no provider calls anywhere in F4 |

## Task data model

Schema 26, additive, in the encrypted workspace database:

```sql
-- Durable, user-owned tasks. Meeting-scoped: a task always belongs to one
-- meeting; the global list aggregates across meetings.
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  origin TEXT NOT NULL CHECK(origin IN ('suggestion','manual','carryover')),
  title TEXT NOT NULL,                       -- user-editable, 1..2000 code points
  owner_person_id TEXT REFERENCES speakers(id), -- NULL = not linked to a person
  owner_label TEXT,                          -- free text, <=100; NULL = unset
  owner_removed INTEGER NOT NULL DEFAULT 0 CHECK(owner_removed IN (0,1)),
  due_date TEXT,                             -- 'YYYY-MM-DD' or NULL (never invented)
  status TEXT NOT NULL CHECK(status IN ('open','done','dismissed','deleted')),
  restore_status TEXT CHECK(restore_status IN ('open','done','dismissed')),
  source_session_id TEXT,                    -- evidence meeting; no FK, may be deleted
  source_summary_id TEXT,                    -- provenance; no hard FK (summaries age out)
  source_revision INTEGER,
  source_segment_ids TEXT,                   -- JSON array of segment IDs; NULL for manual
  suggestion_fingerprint TEXT,               -- NULL for manual tasks
  carryover_task_id TEXT,                    -- provenance task ID in the source meeting
  carryover_note TEXT,                       -- "Carried over from {title}, {date}" copied at accept
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER);
CREATE UNIQUE INDEX idx_tasks_fingerprint ON tasks(session_id,suggestion_fingerprint)
  WHERE suggestion_fingerprint IS NOT NULL;
CREATE UNIQUE INDEX idx_tasks_carryover ON tasks(session_id,carryover_task_id)
  WHERE carryover_task_id IS NOT NULL;
CREATE INDEX idx_tasks_session ON tasks(session_id,status);
CREATE INDEX idx_tasks_global ON tasks(status,due_date);

-- One row per action item extracted into a saved summary, with its disposition.
-- Seeded at upgrade, reconciled on summary save; the UI reads dispositions.
CREATE TABLE task_suggestions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  summary_id TEXT NOT NULL REFERENCES grounded_summaries(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  task TEXT NOT NULL, owner TEXT, due TEXT,
  segment_ids TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  similar_task_id TEXT REFERENCES tasks(id), -- ambiguous match; requires review
  state TEXT NOT NULL CHECK(state IN ('proposed','accepted','dismissed','linked')),
  task_id TEXT REFERENCES tasks(id),
  created_at INTEGER NOT NULL,
  UNIQUE(summary_id,ordinal));
```

Integrity rules enforced in code and schema:

- One task identity per fingerprint per meeting, including deleted rows (partial
  unique index). Reaccepting a deleted suggestion restores that same row; it never
  creates a replacement that could block Undo. Manual tasks carry `NULL`
  fingerprints and are never deduplicated or overwritten. Carryover similarly
  reserves one destination task identity per source task, including deleted rows.
- `owner_person_id` has no `ON DELETE` clause: `forget()` clears it explicitly in
  the same transaction so the removal is deliberate, logged and testable.
- `source_summary_id` deliberately has no foreign key: summaries can be removed by
  person-forgetting while the accepted task (user-approved work) must survive; the
  UI degrades the provenance chip to "From meeting notes" when the row is gone.
- Source chips resolve against the currently selected revision like summary
  citations do today; a segment ID that no longer resolves hides the chip instead
  of showing a broken link. Replacing a transcript never rewrites task text.
  Carryover is the exception: retain the original `source_session_id` and resolve
  evidence only in that source meeting/revision. Source provenance columns have
  no hard foreign keys so deleting an earlier meeting cannot delete its copies.
- Bounds: at most 500 non-deleted tasks per meeting, title 1–2000 code points,
  owner label at most 100 (matching `CitedAction`), due date strict `YYYY-MM-DD`,
  suggestion count at most 20 per summary (schema cap), pages of 200 with the
  201-row look-ahead used by existing paged reads.

### Suggestion fingerprints and reconciliation

`fingerprint = sha256(canonical_json({"t":norm(task),"o":norm(owner),"d":norm(due)}))`
where `norm` = NFC, trim, collapse whitespace, casefold; scope is one meeting;
`owner`/`due` are the raw extracted values or `null`. Segment IDs are deliberately
excluded from the fingerprint (they shift between regenerations) and are kept as
provenance on the row. This is a structural fingerprint — normalized text plus
owner plus date within a meeting — not raw text matching alone.

Reconciliation runs in the same transaction that saves a grounded summary
(`save_grounded_summary`) and is deterministic and idempotent:

1. The previous summary's suggestions simply stop being the active set; they are
   keyed to their `summary_id` and are never shown as current suggestions again.
2. For each `action_items[i]` in the new summary: exact fingerprint match against
   an existing non-deleted task → suggestion `state='linked'`, `task_id` set. No
   duplicate row, no edit to the task.
3. Exact fingerprint match against a previously dismissed suggestion (same
   meeting, any summary) → `state='dismissed'`. Re-generating notes does not
   re-nag a dismissed commitment.
4. No exact match, but normalized similarity against an open task is high
   (token-overlap threshold recorded in tests) → `state='proposed'` with
   `similar_task_id` set; the UI must ask: "Keep existing" or "Add anyway". Never
   auto-merge, never auto-create on ambiguity.
5. Otherwise → `state='proposed'`.

Manual `title`/`owner`/`due`/`status` edits are never overwritten by steps 1–5;
regeneration only ever writes `task_suggestions`.

An exact fingerprint belonging to a deleted task stays `proposed` until explicit
acceptance restores the task. Existing user edits and the prior status are
preserved unless the acceptance command explicitly supplies edits. Within a batch,
duplicate fingerprints resolve to the same task identity. A prior explicit
`Keep existing` mapping also links the same fingerprint on later regenerations
while its target is non-deleted; the target's own fingerprint is unchanged.
Dismissal Undo clears dismissal for that fingerprint across saved summaries in
the meeting, then reconciles against existing tasks, so old rows cannot silently
reinstate the dismissal on the next generation.

### Upgrade and existing saved notes

Schema 26 backfills suggestions from each meeting's latest saved grounded summary
using the same `created_at DESC, rowid DESC` selection as `grounded_summary`.
Create the tables and seed the rows in the migration transaction, using an
idempotent helper shared with summary-save reconciliation. This creates no tasks,
does not call providers, and leaves summary JSON, freshness, and generation
provenance unchanged. Stale summaries are included; unresolved citations remain
hidden. Reopening the workspace must not reset review dispositions. Legacy rows
that cannot be decoded within the existing schema limits remain readable through
existing behavior and produce a diagnostic rather than aborting the upgrade or
inventing suggestions. Test this fallback explicitly.

### Timezone and overdue rules

`due_date` is a calendar date string, not a timestamp: transcripts state dates
without times. Overdue is computed at read/display time against **today in the
machine's local timezone** (`datetime.now().astimezone()`), so a long-open window
stays correct and travel/DST changes behave like a personal calendar. Due today is
not overdue; due yesterday is; `done`/`dismissed`/`deleted` tasks are never
overdue. The global filter offers Overdue / Next 7 days / Undated in addition to
owner and status. Tests use a patched local clock and a non-UTC timezone.

### Delete, forget and undo semantics

- **Meeting deletion** (existing `delete` at `sessions.py:828`): delete
  `task_suggestions` **before** `tasks`, then the remaining meeting data, in the
  existing transaction. Both `task_id` and `similar_task_id` reference tasks and
  would otherwise block deletion. Enforce same-meeting suggestion references in
  every mutation. Run `purge_checkpoint` as today. Carryover copies in other
  meetings survive because their provenance is copied text (`carryover_note`),
  not a join.
- **Person forgetting** (`SpeakerRegistry.forget`): extend affected-session
  discovery with tasks linked by `owner_person_id`, tasks whose free-text owner
  normalizes to the canonical name, and suggestions with that normalized owner.
  Include deleted tasks and carryover copies, even without a speaker/roster link.
  In the same transaction, clear both owner fields on linked tasks regardless of
  the copied label; clear matching free-text labels on unlinked tasks; set
  `owner_removed=1`. Apply the existing derived-summary removal to this expanded
  session set; summary deletion cascades to suggestions, removing copied owner
  names. Surviving user-approved task text stays intact. Reuse existing cache,
  derived-file, redaction-event and checkpoint cleanup; publish invalidations for
  every affected session. Explicitly setting or clearing an owner later resets
  `owner_removed=0`. Exported/derived data outside the workspace remains out of
  reach, as documented today.
- **Manual task deletion with undo**: `status='deleted'` keeps the row with
  `restore_status` and `updated_at`; the UI shows an "Undo" toast immediately and
  a "Recently deleted" filter in the global list; `restore_task` returns the prior
  status. Deleted tasks are purged when their meeting is deleted. Suggestions are
  never hard-deleted by task deletion; their state returns to `proposed` so the
  user can accept again deliberately. Clear affected `task_id`/`similar_task_id`
  references as part of deletion. Restore reconciles those suggestions again;
  repeated delete/restore requests are idempotent. Reacceptance uses the reserved
  identity described above, so subsequent Undo cannot hit a uniqueness conflict.
  Restoring at the 500-task limit fails atomically with an actionable error.

## API surface (desktop protocol, protocol 2)

Reads (strict field sets, bounded pages, `ID`-regex session validation):

- `meeting_page` gains the first page of non-deleted `tasks`, `tasks_next_offset`,
  task status counts, and `suggestions` (the active summary's items with
  reconciliation state). Task pagination is independent of transcript `offset`.
- `tasks_page`: `{session_id?, status?: 'open'|'done'|'dismissed'|'deleted'|'all',
  owner_person_id?, due?: 'overdue'|'next7'|'undated', offset}` → `tasks_page`
  event, 200/page with 201-row look-ahead and `next_offset` (`null` at end).
  Omitted status means open; all excludes deleted; deleted selects only deleted.
  Optional session scopes the same read to the meeting Tasks card, making all
  500 tasks reachable. Ordering is due ascending with undated last, then created
  descending, then ID ascending to break ties. Counts ignore the status filter
  but respect session/owner/due filters. Responses echo the applied filters and
  offset; clients discard superseded requests and reset pagination on changes.
- `task_carryover`: `{session_id}` → open tasks from earlier meetings whose
  normalized title matches, at most 25, each with the source meeting title/date
  and a task snapshot. Purely derived; no write.

Mutations (each: strict fields, receipt, `invalidate` publish for the meeting,
immediate persistence; local-only, never gated on providers):

- `accept_task_suggestions` `{session_id, items:[{suggestion_id, title?,
  owner?: {person_id?|label?}|null, due_date?: string|null,
  allow_similar?: bool}]}` — atomic batch of 1–20 distinct suggestion IDs from
  the active summary, with optional edits. Omitted fields use the suggestion for
  a new task; for a restored task they preserve user edits. Null explicitly clears
  owner/due. Prefill only an unambiguous stated owner label and a validated ISO
  calendar date; other raw values remain visible as source text but unset in task
  fields. Never resolve a name to a person without user selection. Similar
  matches require `allow_similar=true` for the explicit Add anyway choice.
- `dismiss_task_suggestions` `{session_id, suggestion_ids:[…]}`.
- `restore_task_suggestions` `{session_id, suggestion_ids:[…]}` — Undo dismissal;
  clears the matching fingerprint's dismissal history and reconciles current rows.
- `link_task_suggestion` `{session_id, suggestion_id, task_id}` — Keep existing;
  target must be the current same-meeting, open similarity candidate. Links the
  suggestion without editing the task; revalidate the candidate before writing.
- `accept_task_carryover` `{session_id, source_task_id}` — revalidate that the
  source is an open task from an earlier same-normalized-title meeting. Atomically
  create a destination `carryover` task with copied title/owner/due and original
  evidence session/revision/summary/segment IDs, plus the source task ID and meeting
  title/date snapshot. Repeated acceptance returns the existing destination;
  acceptance after deletion restores it without overwriting edits. The source
  task is unchanged. Destination status starts open. Never accept provenance or
  copied fields supplied by the client.
- `create_task` `{session_id, title, owner_person_id?|owner_label?, due_date?}`.
- `update_task` `{task_id, title?, owner_person_id?|owner_label?|clear_owner?,
  due_date?|clear_due?, status?}` — status transitions open⇄done and →dismissed;
  `completed_at` is maintained.
- `delete_task` `{task_id}` (soft delete) and `restore_task` `{task_id}`.

All suggestion-ID batches are bounded to 1–20, validated in full before any write,
and committed atomically. Add a bounded 512 KB desktop command allowance for
`accept_task_suggestions` in both transport validation and client checks: the
existing 12,288-byte limit cannot fit 20 edited 2000-code-point Unicode titles.
Keep all other existing command limits. Validate real calendar dates, IDs, owner
existence, field conflicts, task bounds, and same-meeting references strictly.

Use the existing `invalidate` event for mutations; the new reads return their
named response events (`tasks_page`, `task_carryover`). Extend DesktopClient's
invalidation handling explicitly: any task/meeting/people invalidation refreshes
an open global Tasks sheet, regardless of the selected meeting. Only selected-
meeting detail refreshes are gated on matching `session_id`. Refresh open meeting
task pages and carryover candidates too; recheck the selected meeting's carryover
when another meeting changes. Coalesce refetches and discard stale responses.
Reset task paging after invalidation, and refresh overdue filters/badges at local
midnight and app activation/timezone changes. The existing handler alone does not
provide these behaviors. On read failure retain prior rows with a retryable error.

## UI/UX design (native macOS first)

The notebook visual language stays: serif headings, `notebookCard()` grouping,
small-caps section labels, `ScribeTheme.accent` actions, secondary gray metadata.
Every control gets an accessibility label; every list supports full keyboard
traversal; sheets use `.keyboardShortcut(.cancelAction)` for Cancel and
`.defaultAction` for the primary confirmation, matching existing sheets.

### Per-meeting: review suggestions and manage tasks (Notes tab)

Under the saved notes' **Action items** section (inside `CitedNotes`), each item
row keeps its current read-only rendering and gains a state-aware review strip:

- **Not in tasks:** `Add to tasks` button → opens the task editor prefilled from
  the suggestion (title always; owner/due only when explicitly stated — unset
  values show the existing "Not stated" caption and start empty).
- **Linked:** a quiet `In tasks ✓` badge instead of buttons (prevents duplicate
  accepts and shows the mapping at a glance).
- **Ambiguous:** the row shows "Similar to an existing task" with both texts side
  by side and two explicit choices: `Keep existing` (links the
  suggestion) or `Add anyway`. Nothing merges silently.
- **Dismissed:** the row collapses; a section footer shows
  `1 dismissed suggestion · Show` plus a temporary Undo toast.

A **Tasks card** sits directly below the notes on the Notes tab — the
suggestions come from the notes above, so the review-to-task flow reads top-down:

- Header `TASKS` with counts (`3 open · 1 done`), an `Add task` button (manual,
  origin badge "Manual"), and a compact `Open | Done | All` filter.
- Rows: status checkbox (done⇄open, one click, immediate persist), title (wraps
  long English/Vietnamese text), owner chip, due chip with an `Overdue` badge,
  origin badge (`From notes` / `Manual` / `Carried over`), resolvable Source
  chips jumping to the transcript, and edit/delete controls.
- Empty states distinguish "no tasks yet — accept a suggestion above or add one
  manually" from "no tasks match this filter — Clear filter". Loading uses the
  existing inline ProgressView pattern; failures keep the previous list and show
  a retryable error line (the store is unchanged on failure).

**Task editor sheet** (accept, create and edit share one layout): Title,
Owner (combo listing this meeting's roster and workspace people, plus free text;
`Not stated` clears both), Due (date picker with an explicit "No due date"
toggle), Status (on edit only), Save / Cancel. Validation errors render inline
beside the field; nothing is invented from an ambiguous phrase — the
suggestion's blank owner/due stay blank until the user types them.

When notes regenerate, the Tasks card is untouched and a small caption under the
suggestions header says: "Tasks keep their edits when notes regenerate." This
turns the reconciliation contract into something users can observe and trust.

### Global task list (sidebar → Tasks sheet)

A "Tasks" sidebar button beside Search opens a `MeetingTasksView` sheet (same
shell as Search: serif header, filter row, paged list):

- Filters: Status segmented control (Open default | Done | All | Recently
  deleted), Owner popup (workspace people), Due popup (Any | Overdue | Next
  7 days | Undated). Fixed sort: due ascending with undated last, newest first
  within a day. Counts on the status chips reflect the current owner/due filters.
- Rows: checkbox, title, owner, due + overdue badge, origin badge, and a meeting
  button (`title · date`) that closes the sheet and opens that meeting — the
  task list is a doorway back into context, not a dead end.
- Recently deleted rows offer `Restore` and show relative deleted time; the
  sheet footer explains "Deleted tasks are removed permanently when their
  meeting is deleted."
- Empty states: no tasks at all (guidance to open a meeting) vs. no matches
  (Clear filters). Results stay bounded and paged; an "Older tasks" paging
  button mirrors the sidebar's "Older meetings" affordance.

### Carryover: suggested, never asserted

When a meeting's normalized title matches earlier meetings that still have open
tasks, the Notes tab shows one explicit card above Tasks:
`Open tasks from earlier “Standup” meetings (2)` → rows with source meeting and
date and a `Bring into this meeting` button calling `accept_task_carryover`.
Accepting creates a `carryover`
task whose badge reads `Carried over from {title}, {date}`. The card never says
or implies the commitment was repeated or approved in this meeting; the wording
is reviewed in slice 5 against the acceptance fixtures.
Carryover citations resolve in the source meeting and revision, never against
the destination transcript. If the source meeting or cited segment is removed,
hide its evidence link while retaining the copied task and carryover label.

### Removal, forgetting and trust

- The existing delete-conversation confirmation gains one line: "Linked tasks
  will be removed."
- Tasks whose owner was forgotten show `Owner removed` instead of a dangling
  name/link.
- Every mutation persists immediately (no implicit save), matching the
  "persist immediately and retain provenance" flow step; destructive or
  ambiguous actions (dismiss, delete, ambiguous merge) are the only ones that
  ask.

## Implementation slices and finish conditions

| Slice | Deliverable | Finished when |
| --- | --- | --- |
| 1. Storage + reconciliation | Schema 26 tables/indexes, `storage/tasks.py` TaskStore, existing-summary backfill, reconciliation on summary save, bounds | Populated schema-25 upgrade immediately exposes saved suggestions without generation; reopen preserves dispositions; regenerate/no-duplicate/dismissed-persist/ambiguous-review and manual-edit-preservation tests pass |
| 2. Core service + API | Global/meeting pagination, overdue logic, CRUD + undo, suggestion link/undo, carryover acceptance, contract commands, server dispatch, forget/delete cleanup | Every command validates strictly, batches are atomic, receipts and invalidations fire, restore/reaccept preserves identity, timezone tests pass, and forgetting/deletion cleanup passes with foreign keys enabled |
| 3. Native per-meeting UI | Review strip, Tasks card, editor sheet, undo toast, DesktopClient state | Accept/edit/dismiss/create/done/reopen/delete+undo work keyboard-only; loading/empty/success/error/cancel states verified; Vietnamese long content wraps; zero provider requests |
| 4. Global list + carryover | Sidebar Tasks sheet with filters/restore, carryover card, meeting deep-link, cross-meeting refresh, UI contract tests | Filters compose, all 500 meeting tasks are reachable, mutations in other meetings refresh the sheet/carryover, source links resolve in the source meeting, contract tests pin the JSON shapes |
| 5. Acceptance + closeout | Fixture-based acceptance, in-app check record, `ACTION_TRACKING_CLOSEOUT.md`, delivery-plan updates | All delivery-plan F4 checkboxes addressed with evidence or explicit non-pass notes; a development macOS build is installed and user-reviewed; hosted checks green |

Tests must cover: migration from a populated workspace; fingerprint stability and
collision handling across three successive regenerations; dismissed-suggestion
persistence; manual task independence; batch accept with partial edits; owner
linking, display and person-forgetting; meeting deletion with carryover copies
elsewhere; restore-after-delete; overdue with fixed clocks in a non-UTC timezone
and across a DST boundary; every command's invalid-input rejections; page bounds;
and the summary-stale interaction (stale notes still show tasks; suggestions come
only from the current saved summary). Fake data only; no live provider exists in
this feature.

Required regression cases from plan review:

- Upgrade a populated schema-25 workspace with fresh and stale saved summaries;
  suggestions appear without generation, malformed legacy data does not block
  upgrade, and reopening preserves dispositions.
- Delete an accepted task, regenerate, reaccept, then Undo/restore: exactly one
  identity survives, with prior edits/status preserved. Cover duplicate
  fingerprints within one batch, retry delivery, rollback and the 500-task limit.
- Dismiss then Undo across regeneration; Keep existing then regenerate; Add
  anyway with and without explicit confirmation. Reject stale/cross-meeting IDs.
- Forget a person linked only through a task, with copied/free-text owner labels,
  deleted tasks and carryover copies. Check suggestions, summaries, caches and
  derived files as well as the owner fields and `Owner removed` display marker.
- Delete a meeting with both linked and similar suggestions under
  `PRAGMA foreign_keys=ON`; preserve carryover copies elsewhere and hide missing
  evidence links. Repeated carryover acceptance must preserve one destination.
- Page 500 meeting tasks with tied due/created values through independent task
  offsets. Test all/deleted filter semantics and counts, Unicode batches near the
  transport bound, and invalid date/owner/clear-field combinations.
- Open the global Tasks sheet while a different meeting is selected; mutate tasks
  in another client, forget an owner, and delete a meeting. Verify refreshed rows,
  counts and carryover, stale-response rejection, retained rows on failed reads,
  and overdue refresh at midnight/activation/timezone change.

### Plan verification record — 18 November 2026

This is design verification, not F4 implementation acceptance. Against `364aa95`,
58 baseline tests passed in `test_db.py`, `test_grounded_summary.py`,
`test_people_registry.py` and `test_template_ui_contract.py`. The original proposed
DDL parsed in SQLite; targeted probes reproduced a restore uniqueness failure
after delete/reaccept and a foreign-key failure when deleting tasks before their
suggestions. This revision reserves deleted task identities and specifies child-
first cleanup. Revised DDL probes passed: deleted fingerprints remain reserved,
restoring the original row preserves its title/status, and child-first meeting
deletion passes foreign-key checks. F4 tests, native UI checks, hosted checks and
user acceptance remain pending implementation.

## Risks and decision points

1. **Fingerprint definition** — normalized text+owner+due per meeting is the
   recommendation; the alternative (including segment IDs) makes every
   regeneration ambiguous whenever citations shift, flooding users with review
   prompts. Recorded so slice-1 tests can pin the trade-off.
2. **"Related meeting" heuristic** — v1 uses exact normalized title equality
   (the fixture already contains repeated meeting names). Calendar series IDs or
   prefix matching are later refinements, not initial scope.
3. **Meeting-scoped tasks** — tasks always belong to a meeting. A meeting-less
   global task inbox would need its own delete/forget story and weakens the
   provenance contract; rejected for v1.
4. **Migration numbering** — F4 is schema 26; if it branches before the F3 PR
   merges, stack the branch so migrations land in one order.
5. **No notifications by design** — overdue is shown where the user looks; a
   reminder system would introduce background behavior and consent questions the
   delivery plan does not authorize for F4.

## First implementation step

Create `feature/f4-action-tracking` from `main` (stacked after the F3 PR merge)
and implement slice 1 as a small dedicated `storage/tasks.py` module plus the
schema-26 migration, keeping all existing summary behavior bit-identical until
reconciliation is wired into `save_grounded_summary` behind its own tests. Do not
start with the UI: the reconciliation contract is the risk center, and the native
review strip depends on the suggestion states it emits.

## Slices 1–4 implementation record — 18 November 2026

Implemented on `feature/f4-action-tracking` against `364aa95`.

- **Slice 1 (storage + reconciliation).** Schema 26 adds `tasks` and
  `task_suggestions` exactly as revised above, including the reserved deleted
  fingerprint index, the carryover destination index and `owner_removed`. The
  upgrade seeds suggestions from each meeting's latest summary with the shared
  idempotent seeder (`storage/tasks.py` `backfill_suggestions`), skipping
  malformed legacy items with a diagnostic. `save_grounded_summary` seeds
  suggestions inside its existing transaction; the summary insert is otherwise
  unchanged.
- **Slice 2 (core + API).** `TaskStore` covers reconciliation, atomic 1–20 batch
  accept with edit slots and `allow_similar`, dismissal with fingerprint-wide
  Undo, Keep-existing linking, manual CRUD with undo and prior-status restore,
  reserved-identity reacceptance, the 500-task limit, paged filtered reads with
  counts and due filters, local-date overdue logic, carryover candidates and
  acceptance with copied provenance, and `forget_owner` cleanup used by
  `SpeakerRegistry.forget`. Meeting deletion removes suggestions child-first
  before tasks under `PRAGMA foreign_keys=ON`. The desktop protocol gained
  `tasks_page`/`task_carryover` reads, `meeting_page` task fields, six mutation
  commands with receipts and invalidation, and the 512 KB batch allowance in
  both transport checks.
- **Slice 3–4 (native UI).** `DesktopClient` gained Tasks state and request
  tracking for the global sheet and the meeting card, invalidation-driven
  refreshes for open sheets regardless of the selected meeting, carryover
  rechecks, midnight/activation/timezone overdue refresh, and the extended
  transport limit. `DesktopApp` gained the per-suggestion review strip inside
  `CitedNotes` (Add to tasks / Keep existing / Add anyway / Dismiss with a
  collapsed dismissed footer), the meeting Tasks card with filters, undo-free
  one-click done toggling, edit and delete, the carryover card, the shared task
  editor sheet, and the global Tasks sheet with status/owner/due filters,
  paging, restore and meeting deep links. The delete-conversation confirmation
  now names linked tasks.
- **Verification so far.** `tests/test_tasks.py` (17) and
  `tests/test_tasks_api.py` (5) cover the required regression cases: populated
  schema-25 upgrade with fresh/stale/malformed summaries and reopen-stable
  dispositions; delete→regenerate→reaccept→undo identity reservation; batch
  duplicate fingerprints, rollback and the 500-task limit; ambiguous review
  paths; timezone overdue with a patched clock; carryover reservation and
  source-meeting deletion; person-forgetting across linked, copied, deleted and
  carryover owners; and protocol receipts, invalidation, filter reads and the
  Unicode transport bound. Full suite: **1,039 passed, 2 skipped**; ruff clean.
  Swift parses and `DesktopClient` compiles locally; full SwiftUI type checking
  and the smoke build still require the hosted toolchain.
- **Not claimed.** No in-app native check has been recorded, no development
  build has been installed for user review, and `ACTION_TRACKING_CLOSEOUT.md`
  does not exist. Slice 5 (acceptance and closeout) remains.

## Audit remediation record — 18 November 2026

All twelve findings in [F4_ACTION_TRACKING_AUDIT.md](F4_ACTION_TRACKING_AUDIT.md)
and its additional observations were applied on this branch. The audit document
remains the point-in-time record; the fixes are summarized here.

1. **First manual task (P1)** — the Notes tab renders the Tasks card with its
   empty state for every meeting; Add task no longer depends on existing rows.
2. **Silent re-extraction on clear (P1)** — the editor serializes explicit
   clears as JSON `null` (`NSNull`) and omits only untouched fields, so cleared
   owners/dates stay cleared; untouched fields still prefill.
3. **Evidence context (P1)** — task chips resolve `source_session_id` and
   `source_revision`: same-meeting same-revision chips navigate to the cited
   segment, cross-meeting evidence offers an explicit source-meeting jump,
   stale-revision evidence renders as text instead of a broken link, and
   carryover rows display their copied provenance note.
4. **Meeting pagination (P2)** — the card cursor initializes from the meeting
   read's `tasks_next_offset`, the More control renders independently of the
   locally filtered rows, and deeper pages append through the scoped read.
5. **Editor draft loss (P2)** — saves wait for their receipt; send failures,
   rejections and unknown outcomes surface inline with retry while the draft
   and editor stay open.
6. **Swallowed storage errors (P2)** — suggestion seeding separates malformed
   legacy data (skipped with a diagnostic) from storage errors, which now
   propagate and roll back the summary save or schema upgrade (regression
   tests cover injected failures at save and migration).
7. **Missing required fields (P2)** — create/update require `session_id`,
   `title` and `task_id` before indexing; rejections return receipts and the
   connection stays usable (regression-tested).
8. **Stuck task reads (P2)** — read failures are correlated by request ID, stop
   loading, retain prior rows, show a retryable error, and clear an owner
   filter that a person-forgetting just invalidated (one automatic retry).
9. **Stale ambiguity (P2)** — acceptance, linking and review reads recompute
   the similar-task candidate against current open tasks; stored candidates are
   refreshed and Keep-existing revalidates text similarity (regression-tested).
10. **Dismissal Undo and delete Undo (P2)** — dismissed suggestions expose an
    Undo invoking `restore_task_suggestions`; task deletion shows a
    "Task deleted · Undo" toast backed by the soft-delete restore.
11. **Deletion/reconnect refresh (P2)** — `session_deleted`, snapshots and
    reconnects refresh the open Tasks sheet, carryover and paging state.
12. **Suggestion-history scans (P2)** — schema 27 adds the
    session/fingerprint/state and task-reference indexes; `EXPLAIN QUERY PLAN`
    uses the scoped index (regression-tested).

Additional performance work: `page()` reads tasks with one joined query (rows
now carry `meeting_title`/`meeting_started_at`, which also fixes the carryover
card's empty earlier-meeting title) and computes all five counts in one
aggregate; `meeting_overview` reuses those counts; `carryover_candidates`
bounds its meeting scan (LIMIT 500, at most 100 joins). Verification after
remediation: **1,044 passed, 2 skipped; ruff clean; the fixture preview app
builds and the desktop smoke passes.** The audit's native-UI caveat stands:
findings were source-verified, and interactive acceptance remains slice-5 work.

## Follow-up fixes — 18 November 2026

A re-check of the remediation found four gaps, all fixed:

1. **Task read errors lacked the request ID.** `tasks_page` and
   `task_carryover` were missing from the desktop error set, so their failures
   fell through to the replay branch with `request_id: None` and the recovery
   handler could never correlate them. Both actions now emit desktop-scoped
   errors with their request ID (regression-tested with an explicit request).
2. **Deleted-source evidence stayed clickable.** The cross-meeting evidence
   jump now requires the source meeting to exist in the client's session list;
   deleted sources render explanatory text instead of a button.
3. **Task pagination could duplicate rows.** The card cursor was re-synced on
   every meeting read — including transcript pages and merged windows whose
   task payload resets to the first page — regressing the cursor while
   appended rows remained. The cursor now initializes only from a pristine
   state; resets belong to invalidation, selection and deletion.
4. **Malformed legacy owner/due could block migration.** Non-string owner or
   due values reached SQL binding and aborted the upgrade. The seeding shape
   check now requires string-or-null owner/due and skips such items with a
   diagnostic (regression-tested with an owner object).

Verification: **1,045 passed, 2 skipped; ruff clean; the shell compiles, the
fixture preview app builds and the desktop smoke passes.**
