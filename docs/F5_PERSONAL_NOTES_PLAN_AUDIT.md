# F5 personal notes plan audit

Reviewed 19 November 2026 against `55701ba` on `feature/f4-action-tracking`
(schema 27), including the current working tree. Reviewed
`F5_PERSONAL_NOTES_PLAN.md` against delivery-plan §F5, the shared finish gate,
and the current Python/native implementation. Existing uncommitted F4 changes
were left untouched.

**Verdict: revise before implementation.** The outcome, native placement,
separate authored storage, default-off inclusion, and pause-collapsed timeline
are consistent with the delivery plan. The contracts below are incomplete or
contradict the implementation. This is a static plan review; F5 does not exist
yet, so no F5 runtime or acceptance results are claimed.

## Findings

### 1. P1 — Inclusion filtering misses previous Ask turns and derived summaries

Plan: “Search and AI inclusion,” Ask gating and inclusion-toggle behavior.
Evidence: `llm/meeting_ask.py`, `MeetingAsk.answer`, `answer_messages`,
`_evidence_hash`; `storage/search.py`, `_documents`.
All Python paths in this audit are relative to `core/scribeloop_core/`.

After an included note produces an Ask answer, turning inclusion off leaves its
source document and source hash unchanged. The existing follow-up path validates
that hash and sends the prior answer to the provider in `prior_turn`. Filtering
new `user_note` hits does not stop that upload. A summary generated with notes
can also contain note-derived text and enters retrieval as `source_type=summary`,
bypassing the proposed filter. An inclusion change during an in-flight request
is likewise invisible to existing source-hash validation.

Define provenance for every note-dependent summary/answer and a per-meeting
context version. Check eligibility before dispatch, before reusing prior turns
or cached jobs, and before accepting results. Turning inclusion off must prevent
future requests from sending that content through derived sources. Define what
remains visible as historical output, separately from eligibility for reuse.
Test on → generate → off → follow-up, derived-summary retrieval, and toggling
while requests are running. Revocation cannot recall an already sent request.

### 2. P1 — The proposed summary-input edit does not reach actual generation

Plan: summary generation row, “Summaries,” and slice 3.
Evidence: `api/server.py`, `generate_summary`; `llm/note_generation.py`,
`NotesGeneration.engine`; `llm/rolling.py`, `run` and `_request`;
`llm/summary_provider.py`; `storage/sessions.py`, `save_grounded_summary`.

The server reads `summary_input(..., build_messages=False)` and sends transcript
rows to `RollingNotes.run`. That engine builds its own `summary_messages`,
sections, merges, validation, and cached jobs. Appending text only to
`summary_input` messages will not include notes in those requests. Existing
summary save/freshness hashes cover transcript fields only. The provider schema
also restricts citation identities, while merge code propagates segment IDs.

Plan a frozen note evidence snapshot passed through manual, automatic, rolling,
section and merge requests, validation, retry/cache identity, save checks and
freshness. Validate `note_refs` against that snapshot, including meeting and
revision/content identity; merely allowing arbitrary strings is insufficient.
Preserve legacy parsing. A schema cannot determine whether prose is semantically
supported only by a note: distinguish structural citation checks from quality
acceptance with contradictory evidence and real provider output.

### 3. P1 — Deleting empty notes breaks stable IDs and optimistic concurrency

Plan: data model, empty-content deletion, autosave and inclusion commands.

Clearing content deletes its ID, revision and inclusion flag. Recreating it can
reset revision to 1 and change the ID referenced by existing `note_refs`. A stale
writer can then match a recycled revision (an ABA conflict). The toggle creates
an empty row, directly conflicting with the rule that empty content removes it.
The initial `base_revision`, toggle revision behavior, and unchanged-save return
value are unspecified. `meeting_page` also omits the note ID used as evidence.

Keep a stable per-meeting document/metadata row with monotonically increasing
revision; omit empty content from search instead of deleting its identity.
Alternatively specify a persistent generation/tombstone scheme. Define empty,
first-save, no-op, toggle and stale-client behavior and expose the source ID.
Test clear → recreate → delayed stale save, and toggle-before-first-keystroke.

### 4. P1 — The assumed 512 KB save limit is not available to this command

Plan: content bounds and desktop API surface.
Evidence: `api/server.py:784`; `shell/macos/Desktop/DesktopClient.swift:580`.

Both current limits allow 512,000 bytes only for `accept_task_suggestions`;
ordinary commands are limited to 12,288 bytes. A valid 60,000-character note
therefore fails on both sides unless both allowlists change. Worst-case UTF-8
size alone is also insufficient: JSON escaping and envelope overhead count.
Python code points and Swift `String.count` use different character units.

Specify the command-specific byte limit on both sides and one Unicode counting
contract. Verify actual Foundation-serialized payloads through desktop dispatch
at the limit, with Vietnamese combining characters, emoji, quotes, backslashes
and control characters. Include response-size behavior for the full editor text.

### 5. P1 — Mark anchoring confuses captured, queued, written and durable audio

Plan: “Persisted-vs-captured resolution” and slice 2.
Evidence: `capture/recording_gate.py`, `filter`/`_slice`;
`pipeline/recording.py`, `_capture`; `storage/audio.py`,
`EncryptedAudioWriter.write`/`checkpoint`.

Enqueuing a frame does not persist it. `sample_offset` identifies its start,
not its end; a maximum across tracks does not ensure that position exists on
both tracks. Writer writes flush, while checkpoints fsync. The pause snapshot
and writer progress also come from different threads and need a coherent policy.
Finally slice 2 compares the mark with the persisted position, whereas delivery
§F5 requires ≤500 ms from the selected captured position. That proposed test
can pass even when the marker is seconds late under backlog.

Define the selected captured position at command arrival and distinguish it
from per-stream written/durable extents. Specify frame-end math, synchronization,
EOF seeking, delayed streams and missing/recovered audio. Choose whether a mark
waits for its target to be written, remains pending, or fails explicitly; do not
silently substitute an older point and call it accurate. Measure error against
the captured target, including before/during/after pause and backlog fixtures.
Use interval availability per stream rather than session-level retention alone.

### 6. P2 — Promised mark labels and undo have no supported mutation contract

Plan: desktop command table and “Mark moment interaction.”

The toast offers a label after the mark is created, but no update command exists.
Undo promises to restore the same row fields, while `add_note_mark` during
recording always resolves a new current position. It cannot restore the original
ID, pause flag and timestamp. The table also omits the playhead fields described
in its behavior column, despite requiring strict field sets.

Specify an update-label and restore operation, or a reversible deletion design.
Define exact allowed payloads for recording/playhead creation; validate saved
playhead bounds and reject attempts to override a live recording anchor.
Test label editing, undo while recording/paused, undo at the mark cap and invalid
or unavailable playheads. Update the “four commands” error/test inventory.

### 7. P2 — Autosave lacks the sequencing required by the native mutation gate

Plan: autosave contract and immediate navigation/quit flush.
Evidence: `shell/macos/Desktop/DesktopClient.swift`, `send` and receipt handling.

The client blocks a new ordinary mutation while an earlier one is pending,
dispatching or unknown. An autosave can therefore block Mark/Pause/toggle, or be
blocked by them. A 700 ms debounce alone does not guarantee a durable save within
one second. Multiple snapshots can also overwrite newer typing if state is keyed
only by the selected meeting; conflicts can occur in a single window if saves
overlap or a toggle advances revision. “Last-write-wins” contradicts the explicit
revision-conflict design. A send before quit does not ensure its receipt arrives.

Define per-meeting drafts and edit generations, one in-flight save with
coalescing, receipt-bound revisions, selection-safe refresh and explicit unknown
outcome reconciliation. Specify how Mark/Pause remain available and how normal
quit waits for acknowledgement or surfaces a save failure without losing text.
Stop must retain its emergency path. Test rapid typing across acknowledgements,
A→B navigation with delayed A replies, pending mutations, reconnect and quit.

### 8. P2 — Note edits will still trigger cloud re-embedding of other sources

Plan: semantic index rule and “Autosave churn vs. reindex cost.”
Evidence: `storage/search.py`, `_sync_semantic`.

Semantic state currently compares against the complete lexical session
fingerprint. Adding note revisions/content to that fingerprint makes every note
edit invalidate cloud semantic state. Excluding notes from chunks prevents direct
note upload, but the next hybrid query still re-embeds transcript/summary chunks.
The plan's claim that note edits never cause cloud re-embedding is false under
the proposed changes. Summaries containing note-derived text also need the
explicit embedding privacy policy from finding 1.

Use provider-eligible semantic content fingerprints, separate from lexical
fingerprints. Test an already indexed cloud meeting → edit only personal note →
hybrid query with zero new document embedding calls (query embedding may still
occur). Test Local→cloud→Local transitions and stale vector generations.

### 9. P2 — Person-forgetting and downstream F4 provenance are not specified

Plan: outcome/scope, “no code dependency on F4,” and acceptance mapping.
Evidence: `speakers/registry.py`, `forget`; `storage/tasks.py`,
`seed_summary_suggestions` and task evidence storage.

The plan promises person-forgetting behavior but supplies no policy or test.
Current forgetting removes derived summaries for meetings associated with a
person; authored note text has no person linkage. Decide explicitly whether exact
authored notes remain and how affected derived contexts are invalidated. Do not
silently rewrite the user's prose to perform identity redaction.

F5 also changes F4's inputs: saving a grounded summary calls task-suggestion
seeding. Note-only `CitedAction` items with empty `segment_ids` are accepted by
that seeder, which stores only segment provenance and loses `note_refs`. Define
whether such suggestions are excluded or preserve distinct note evidence, with
no speech/playback claim. This is an actual cross-feature dependency beyond
migration numbering. Test note-only and mixed action evidence through suggestion
acceptance, carryover, source edits and source deletion.

## Required plan revisions and verification

Resolve findings 1–5 before schema/API implementation; carry findings 6–9 into
explicit slice contracts and tests. In particular, finalize stable identity,
context invalidation and mark APIs before treating schema 28 as settled.

Add migration/rollback guidance required by the shared finish gate: supported
older-app behavior with a newer workspace schema, backup/recovery expectations,
and how derived indexes are rebuilt without ASR/audio upload. The current plan
mentions upgrade testing but contains no rollback policy. Recheck actual main
and the next migration number after F4 merges; do not reserve 29 as if it avoids
an unknown concurrent migration.

Verification performed for this audit: traced the existing request limits,
native mutation gate, recorder queue/writer/checkpoint boundaries, search
fingerprints, Ask prior-turn/source checks, summary dispatch/rolling engine,
grounded validation, person forgetting and F4 suggestion consumers. No production
code changed and no runtime tests were run. F5 implementation tests, provider
quality checks, crash recovery, installed native build review and human
acceptance remain future work.
