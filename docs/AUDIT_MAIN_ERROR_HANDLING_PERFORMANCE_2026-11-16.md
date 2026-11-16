# Main error-handling and performance audit

Audited commit: `1fe3ba79a53f7dcbded65ebf859cdc01f564271e` (`main`).
Date: 2026-11-16. Original audit was read-only. The remediation below is implemented
on its dedicated branch; the findings above describe the audited main commit.

## Scope and evidence

Reviewed desktop command dispatch and Swift search state, recording lifecycle and
bounded capture queues, provider requests, retained-audio workers, summary
freshness/export, and Search/Ask retrieval/indexing. This is a focused correctness
and performance review, not an exhaustive security scan or release acceptance.
No private recording, live provider request, microphone capture or paid API was used.

Four findings were verified with isolated synthetic inputs. The existing focused
suite passed **133 tests in 4.22 seconds**: streaming performance, retained audio,
meeting search, meeting Ask, grounded summaries and desktop protocol tests.
Passing these tests does not cover the failure cases below.

## Findings

### P1 — Search blocks subsequent commands on the desktop connection

Locations: `core/scribeloop_core/api/server.py:736` and
`core/scribeloop_core/api/desktop_contract.py:159`.

The sole WebSocket reader awaits `desktop.read()`. Search performs remote indexing
and query embeddings inside that await. Until it returns, the reader cannot
consume another command, including agreement revocation or a new recording
request. Async provider I/O yields to other tasks, but does not free this reader.
Search has no separate cancellation command or whole-operation deadline; the
indexing path can issue up to 128 sequential embedding batches.

Verification: substitute a synthetic 0.5-second search in the real server, send
`search_meetings`, then `desktop_snapshot` over the same authenticated WebSocket.
The snapshot waited **0.504 seconds** and arrived after `search_results` and its
receipt. This proves command ordering/blocking; the measurement is synthetic,
not a real provider latency measurement.

Fix: dispatch retrieval as a tracked cancellable task, keep the reader available
for control commands, coalesce superseded searches, and use an operation deadline.
Keep SQLite access on its owner thread and serialize index mutations. Recheck
authorization before each upload and before publishing a result. Test a delayed
provider followed by revoke, recording and cancellation commands.

### P2 — Speaker renaming incorrectly removes a current summary from export

Location: `core/scribeloop_core/llm/grounded.py:130`.

`SessionStore.grounded_summary()` uses the name-independent `content_hash` for
saved-note freshness. Markdown export additionally compares the old name-sensitive
`input_hash` with `transcript_hash(rows)`. Changing only a display name therefore
keeps the summary current in the app while export labels it outdated and omits it.

Verification: save a grounded summary, change its speaker label to Minh, reload
the saved summary and export. Result: `stale=false`, no `## Summary draft` in the
export, and the message “outdated and has been omitted.” No provider is involved.

Fix: use the same content-hash freshness contract in export, retaining revision,
source-content and legacy-record checks. Add export coverage to the existing
speaker-name freshness tests, including reopening the workspace.

### P2 — Embedding response size limits apply after full buffering

Locations: `core/scribeloop_core/storage/embeddings.py:111` and `:153`.

Both compatible and Gemini providers use `await client.post()` and only then
check the 16 MB response limit. HTTPX has already downloaded and buffered the
body. Oversized or continually trickling responses can consume memory and hold
the operation far beyond its intended size budget before rejection. HTTPX's read
timeout is not a whole-response duration limit.

Verification: a fake HTTP transport emitted twenty 1 MB blocks. All **20,000,000
bytes** were consumed before `Embedding response too large` was raised. The
compatible-provider path was executed; the identical buffering pattern in Gemini
was confirmed by inspection. This is not a measured peak-RSS value.

Fix: use a streaming response, enforce a decoded-byte cap while iterating, abort
at the first oversized block, and add a total deadline. Preserve status handling,
shape checks, safe diagnostics and no automatic retries. Test compressed bodies,
oversized chunks, slow streams and cancellation.

### P2 — Date-filtered Hybrid search indexes excluded meetings first

Locations: `core/scribeloop_core/storage/search.py:250` and `:308`.

Semantic indexing receives meeting IDs, but not the date/person constraints used
for retrieval. With no explicit meeting selection, the first filtered search
embeds all changed meetings before applying the result filter. A large archive
can add unrelated uploads, latency and quota consumption to a narrow search.
Returned results are filtered correctly; this finding concerns unnecessary work.

Verification: create an older excluded meeting and a newer eligible meeting,
query with `date_from` using a fake embedder. Only the new meeting appeared in
results, but **both meetings were embedded and indexed**, including the excluded
older transcript.

Fix: resolve eligible meeting IDs before on-demand lexical/semantic synchronization
and pass them through indexing and retrieval. Keep any deliberate whole-library
indexing separate and visible. Test that excluded meetings trigger zero embedding
calls while eligible results and pagination remain correct.

## Existing controls verified by inspection

- Capture has a bounded queue, disk reserve checks and explicit failure instead
  of silently dropping queued audio.
- Retained-audio work runs off the event loop and holds its busy/key lease until
  its worker finishes, including cancellation.
- Summary responses are streamed with a body cap; summary jobs also have an
  outer timeout, persist failed deliveries and avoid automatic unknown retries.
- Ask runs in a tracked task with safe provider diagnostics and authorization
  checks. These protections do not make synchronous Search dispatch cancellable.
- Swift's search view retries a pending debounced query after an in-flight query
  finishes; the client loading guard alone is not a dropped-query bug.

## Recommended order

1. Separate retrieval jobs from control-command ingestion.
2. Bound embedding response reads and total operation time.
3. Align export freshness with saved-summary freshness.
4. Scope indexing before remote embedding work.

After fixes, rerun the synthetic failure cases and the focused suite, then hosted
macOS/Linux checks. Native timing, full-library latency and provider accuracy need
separate measured validation; this audit does not claim those passed.

## Approved implementation plan

Branch: `fix/search-responsiveness-and-audit-findings`.

- Track cancellable desktop Search/evidence jobs separately from command ingestion;
  serialize retrieval work on the DB owner thread, impose a total deadline, cancel
  superseded/disconnected jobs and cancel search before recording or revocation.
- Keep the 500 ms debounce, expose preparation/search progress and Cancel, preserve
  query/filters on failure, and distinguish failure from an empty result. Stale
  responses must not replace the latest query's state.
- Stream and bound embedding bodies with a total request deadline; preserve shape
  validation, safe errors and explicit retries.
- Apply eligible meeting/date/person scope before indexing; reuse existing vectors.
- Align Markdown export freshness with saved-summary content hashes.

Finish conditions: regression tests for each audited failure, command responsiveness
under delayed retrieval, cancellation/no late result, safe provider error states,
and excluded-meeting zero uploads; full tests/lint and native UI build validation.
Record validation and any remaining limits here before committing.

## Implementation and validation

All four findings have code fixes and regression coverage. Search/evidence jobs
are tracked and serialized with Ask retrieval while the WebSocket reader remains
available. Cancellation uses both task cancellation and an independent flag;
same- and cross-connection revocation tests include a fake provider that ignores
in-flight cancellation. Disconnect, immediate cancellation receipts, supersession,
total deadline and safe unexpected-error handling are covered. Only the latest
request can update the native UI.

The UI retains 500 ms debounce, shows preparation/progress/search states, offers
Cancel and Retry, preserves query/filters and distinguishes failure/cancellation
from no matches. No-eligible-meeting results are not mislabeled as provider failure.
Provider failures keep keyword fallback explicitly labeled with safe quota,
access or timeout guidance. Full-library indexing was not added as a new feature.

Embedding tests exercise both cloud adapter formats, early read termination,
compressed bodies, stream closure, cancellation, total deadline and HTTP errors.
Filtered indexing tests cover dates, people, no eligible meetings and vector reuse.
Speaker-name freshness tests now verify exported notes, including legacy hashes;
unrelated content hashes and changed source content still reject saved notes.

Validation completed locally:

- Full suite including lifecycle additions: **926 passed, 37 optional skips**.
- Final targeted retrieval/export suite: **86 passed**; added lifecycle suite:
  **9 passed**. Same/cross-connection revocation variants are included. An additional
  elapsed-deadline guard also rejects late results from cancellation-resistant adapters.
- Ruff, frontend replay, whitespace and Bandit's reviewed-baseline gate passed.
- Production Swift UI compiled; isolated native DesktopAuditTests passed, including
  new Search progress, error, retry, cancellation and obsolete-response cases.
- Isolated synthetic preview app rebuilt and code-signing verification passed at
  `build/macos/ScribeLoop Preview.app`. Production app/workspace were not replaced.

Visual UI interaction remains unverified: the computer-control service reported
“native pipe startup failed” and no available app surfaces. No real provider,
private recording, microphone or paid API was used. Hosted CI has not been run for
this unpushed branch. Native state checks and compilation do not substitute for
an on-screen layout check or real provider latency validation.
