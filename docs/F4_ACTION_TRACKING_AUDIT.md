# F4 action tracking audit

Reviewed commits `a2c0fa4` and `31e3ad1` against base `364aa95` and
`F4_ACTION_TRACKING_PLAN.md`. Scope: feature correctness, performance and error
handling. This is an implementation audit, not native UI acceptance. Findings
below record the original audit; application code was not changed during the
audit. See the recheck below for the status after remediation commit `b3b7bf5`.

## Findings

### 1. P1 — Creating the first manual task is inaccessible

`shell/macos/Desktop/DesktopApp.swift:621`

The Notes view only renders `MeetingTasksCard` when a non-deleted task already
exists. That card contains the only manual Add task action. A meeting with no
tasks, including one with no extracted suggestions, cannot create its first
manual task through the UI. Deleting its last task hides the action again.

Render the card and its empty state for eligible saved meetings regardless of
task count. Verify a meeting with no summary and no tasks can create a task.

### 2. P1 — Clearing suggested owner/date silently restores extracted values

`shell/macos/Desktop/DesktopApp.swift:1730`

Suggestion acceptance assigns optional `nil` to `[String: Any]` entries for empty
owner and disabled due date. Swift removes those keys; it does not serialize JSON
null. The server interprets omitted fields as prefill from the suggestion. Clearing
Alice or switching off a stated due date therefore still creates a task owned by
Alice or with that due date. A Foundation serialization probe confirmed the keys
are absent from the emitted JSON.

Use `NSNull()` for explicit clears and preserve omission only for untouched
fields. Test the actual client payload through the acceptance endpoint.

### 3. P1 — Task source controls ignore the evidence meeting/revision

`shell/macos/Desktop/DesktopApp.swift:1833`

Task chips forward only a segment ID to a callback that opens the currently
selected meeting's transcript. They never use `source_session_id` or
`source_revision`, nor check that the source still resolves. Carryover tasks
therefore navigate to the destination transcript rather than their evidence;
source deletion and transcript replacement leave clickable broken chips.

Resolve provenance before rendering, navigate to the correct source context, and
hide unavailable evidence controls. Verify carryover across meetings, transcript
revision changes and source-meeting deletion.

### 4. P2 — Meeting task pagination cannot advance past 200

`shell/macos/Desktop/DesktopClient.swift:917`

The first page supplies `detail["tasks_next_offset"]`, but More tasks calls
`loadMoreMeetingTasks`, which requires `cardTaskNextOffset`. That property is reset
to nil and only assigned after a later task-page response; the first later page
can never be requested. Separately, the card hides the pagination button when the
locally filtered first page is empty, even if later pages contain matching tasks.

Initialize the task cursor from the first response or use the same cursor in the
view and loader. Make paging available independently of filtered visible rows,
or paginate with the selected filter on the server. Test all 500 tasks through
the native client with mixed Open/Done sort order.

### 5. P2 — Failed saves close the editor and discard the draft

`shell/macos/Desktop/DesktopApp.swift:1727`

Every editor save calls `send` and immediately dismisses the sheet, without
checking whether sending succeeded or waiting for its receipt. Disconnection,
pending-mutation blocking, an overlong title, a changed suggestion or a database
failure loses the editor draft. There are no inline field errors, and task
rejections fall through to the server's generic replay error message.

Keep the editor and draft until an accepted receipt arrives. Handle local send
failure and rejected/unknown outcomes explicitly, with actionable errors and retry.

### 6. P2 — Database errors are swallowed during summary reconciliation

`core/scribeloop_core/storage/tasks.py:191`

The broad exception handler in `seed_summary_suggestions` covers database writes,
not just malformed legacy input. A trigger injecting an INSERT failure confirmed
that `save_grounded_summary` still commits and advances the active summary, with
zero active suggestions. Failures after several inserts can leave a partial set.
Migration also advances to version 26 after swallowed seeding failures, and normal
reopening does not repair them.

Separate legacy-data validation from storage errors. Let database errors roll
back the summary/migration transaction, or implement an explicit recoverable
incomplete state and repair path. Inject failures at multiple insert positions.

### 7. P2 — Missing required command fields terminate the WebSocket reader

`core/scribeloop_core/api/server.py:1040`

Create/update validation rejects extra fields but does not require `session_id`,
`title` or `task_id` before indexing the command. A real desktop WebSocket probe
with `{"action":"create_task","title":"Missing session"}` raised uncaught
`KeyError('session_id')`. `KeyError` is outside the command exception handler, so
the connection fails instead of returning a rejected receipt and usable error.

Validate required fields before access for both commands. Test missing fields and
confirm that another valid command succeeds on the same connection afterward.

### 8. P2 — Rejected task reads leave the global list loading indefinitely

`shell/macos/Desktop/DesktopClient.swift:895`

`loadTasks(reset:true)` sets `taskLoading=true`, but only a successful `tasks_page`
response clears it. Rejected receipts, errors and local `send` failure do not
clear the task request/loading state. Forgetting the currently filtered owner
causes a concrete trigger: the owner ID remains selected, while the server rejects
that now-nonexistent person. Errors are not correlated to task reads in the
server's desktop error branch either.

Handle task read failure by request ID, stop loading, retain prior rows, show a
retryable error and clear invalid owner filters after forgetting.

### 9. P2 — Ambiguity review is stale after task creation or edits

`core/scribeloop_core/storage/tasks.py:450`

Acceptance trusts the suggestion's stored `similar_task_id`, computed when the
summary was saved. Creating or editing tasks afterward does not recompute it.
A probe saved a suggestion, manually created an identical task, then accepted
the suggestion without `allow_similar`; two tasks were created with no ambiguity
confirmation. Keep existing likewise checks the stored candidate ID/status but
does not recheck text similarity after edits.

Revalidate current matches during accept/link, and update the displayed candidate
when relevant task changes occur. Preserve explicit Add anyway behavior.

### 10. P2 — Dismissed suggestions have no native recovery action

`shell/macos/Desktop/DesktopApp.swift:1567`

The dismissed footer only reveals a Dismissed label. No native control invokes
`restore_task_suggestions`; there is no dismissal Undo toast. Because the server
persists dismissal across generations, an accidental dismissal cannot be
recovered through the UI. Task deletion also lacks the planned immediate Undo
affordance, though it is recoverable through Recently deleted.

Wire dismissal Undo and a recoverable Show flow, plus the planned delete Undo
action. Test recovery across regeneration and failed mutations.

### 11. P2 — Global task state is not refreshed after meeting deletion

`shell/macos/Desktop/DesktopClient.swift:789`

The new refresh logic runs on `invalidate`, but meeting deletion emits
`session_deleted`. Its handler updates only sidebar sessions/detail; it never
refreshes the open Tasks sheet or carryover. A second client deleting a meeting
leaves its removed tasks visible and actionable in the first client's open sheet.
Snapshot/reconnect handling similarly does not reload an already-open Tasks sheet.

Refresh the task views on deletion and reconnect, reset stale task paging, and
reconcile selected owner/meeting filters. Verify with two clients.

### 12. P2 — Reconciliation scans workspace-wide suggestion history

`core/scribeloop_core/storage/tasks.py:142`

Both prior-link and dismissal lookups filter by session/fingerprint, but there is
no corresponding index. `EXPLAIN QUERY PLAN` reports `SCAN task_suggestions`.
This history grows with all summaries, regardless of the 500 live-task cap.
Twenty absent-fingerprint lookups over 100,001 synthetic suggestion rows took
66.54 ms in an in-memory SQLite probe, versus 0.03 ms after adding a scoped
fingerprint/state index. These are synthetic measurements, not encrypted-workspace
or end-user latency benchmarks. Reconciliation performs multiple such lookups
per action item synchronously on the API thread.

Add indexes for actual session/fingerprint/state and task-reference access paths;
verify query plans and growth with realistic regeneration history.

## Additional performance and presentation observations

- A 200-task `meeting_overview` executed 211 SQL statements in a trace. `page`
  performs one meeting lookup per task plus five count queries, and overview
  repeats counts. This runs as part of every transcript `meeting_page`. Join
  meeting metadata and aggregate counts once; avoid repeatedly loading unchanged
  task data while browsing transcript pages.
- `carryover_candidates` scans earlier meeting titles in Python and builds an
  unbounded SQL parameter list before applying the 25-task output cap. Bound
  candidate discovery or index a normalized series key; validate scale against
  the SQLite variable limit used by the packaged database driver.
- Carryover SQL selects `meeting_title` and `meeting_started_at`, but `task_row`
  discards both before returning the response. A probe confirmed both are absent.
  The card consequently shows an empty earlier-meeting title and substitutes
  task due date for the source meeting date. Task rows also show only Carried over
  rather than the stored source title/date note.

## Verification and limits

- **154 tests passed** across `test_tasks`, `test_tasks_api`, `test_db`,
  `test_grounded_summary`, `test_people_registry`, `test_template_ui_contract`,
  `test_desktop`, `test_desktop_packaging`, `test_conversation_management`,
  `test_note_templates` and `test_meeting_api`.
- Additional isolated probes used synthetic in-memory databases, injected SQLite
  write errors, the desktop WebSocket protocol and Foundation JSON serialization.
  No provider requests or production workspace changes were made.
- Native UI findings were established by source tracing. No interactive macOS
  acceptance session or full native build was performed in this audit. The
  current passing tests do not establish native task-flow correctness.

## Recheck of remediation commit `b3b7bf5`

Most remediation is present, but the feature still has the following open issues.
The original 12 findings above remain a historical record.

### R1. P2 — Task read errors still cannot reach the new recovery handler

`shell/macos/Desktop/DesktopClient.swift:859`;
`core/scribeloop_core/api/server.py:1541`

The client now invokes `taskReadFailed` when an error's request ID matches
`taskRequest`, but the server still omits task commands from its desktop error
branch. A real WebSocket probe with a nonexistent owner returned a rejected
receipt followed by an error with `context='replay'` and `request_id=null`. The
client receipt handler does not handle task-read rejection, so the spinner stays
active, `taskError` is not set, and the forgotten-owner retry never runs.

Add task commands to request-correlated desktop errors and handle rejected read
receipts explicitly. Regress the paired wire events and the client loading/filter
state together, rather than testing each side separately.

### R2. P2 — Cross-meeting evidence still links to deleted source meetings

`shell/macos/Desktop/DesktopApp.swift:1897`

The destination now opens the correct source meeting instead of using its own
transcript. However, the cross-meeting button is unconditional whenever the stored
source ID differs from the selected meeting. Carryover intentionally preserves
that ID after source deletion, so the button still appears and selects a
nonexistent meeting. It also opens the meeting without a segment anchor, and
same-meeting chip rendering checks only revision equality, not segment existence.

Resolve source availability server-side or through a dedicated evidence read.
Hide unavailable evidence and navigate to the actual segment when it resolves.
Do not treat presence in the paged sidebar history as proof of availability.

### R3. P2 — Transcript paging resets the already-advanced task cursor

`shell/macos/Desktop/DesktopClient.swift:748`

Initializing the task cursor fixed the original inability to load page two.
However, every `meeting_page` response unconditionally assigns its first-page
`tasks_next_offset` to `cardTaskNextOffset`, while retaining `cardTaskRows`.
After loading task rows 201–400, reading another transcript page resets the
task cursor from 400 to 200. More tasks then appends rows 201–400 again, creating
duplicate IDs and delaying access to rows 401–500. The same issue can re-enable
paging after the last task page has loaded.

Initialize the cursor only when replacing/resetting the task collection, or reset
the collection and cursor together. Regress interleaved transcript/task paging.

### R4. P2 — Malformed legacy owner/due fields can now abort workspace migration

`core/scribeloop_core/storage/tasks.py:188`

Propagating storage failures is correct, but legacy validation still checks only
task text and segment-list shape. An otherwise readable saved summary whose owner
is an object passes this check and reaches the INSERT as a Python dict. A
populated-schema-25 upgrade probe failed with
`ProgrammingError: Error binding parameter 6: type 'dict' is not supported`.
Before remediation, the broad catch skipped the row; the narrower error policy
now exposes this malformed-data path as an upgrade failure.

Validate the full legacy action shape and bounds before SQL binding, skipping
malformed items with diagnostics while continuing to propagate genuine database
failures. Cover invalid owner/due types, title bounds and citation-array bounds.

### Recheck verification

- **159 tests passed** across the same 11 test files listed in the original
  verification, including the five added remediation regressions.
- The new rollback, stale-ambiguity and missing-required-field regressions pass.
  Manual-task visibility, explicit JSON-null handling, dismissal recovery and
  deletion/reconnect refresh changes are present in the native source.
- A fresh trace for a 200-task meeting overview executed **6 SQL statements**, down
  from 211. Scoped history indexes are now installed and their query-plan test
  passes. Carryover responses preserve meeting metadata.
- Additional synthetic probes exercised actual WebSocket error events and a
  malformed legacy migration. Remaining native issues were established by source
  tracing; interactive macOS acceptance was not performed in this recheck.
