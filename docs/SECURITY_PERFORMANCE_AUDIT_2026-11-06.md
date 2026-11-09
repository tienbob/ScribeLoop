# Security & Performance Audit — 6 November 2026

Follow-up to [`SECURITY_PERFORMANCE_AUDIT_2026-11-04.md`](SECURITY_PERFORMANCE_AUDIT_2026-11-04.md), re-verified
against commit `22b17ea` (desktop `0.1.44` build `45`) and remediated in this change set. **No exploitable
security vulnerability was found.** The medium reconnect issue and two low-severity items were fixed; one
low performance follow-up was reduced.

## Scope

Python core/API/storage/LLM paths, the native desktop client, development web UI, packaging, OAuth/account
service, and all changes after the November 4 checkpoint. The November 4 findings were checked against the
implementation rather than accepted from the prior report.

## November 4 follow-up status

| Item | Status |
|---|---|
| M1 `RecordingGate` | **Fixed and verified** — 16,384-interval cap, adjacent merge, indexed skip and no whole-history copy. |
| M2 account proxy headers | **Resolved and verified** — opt-in `--trust-loopback-proxy`, loopback-only forwarding, one worker and deployment documentation. |
| L1 plaintext email exports | **Documented accepted design** — private owner-only temp files, 7-day TTL and startup/export cleanup. Graceful-quit purge remains a design choice, not a defect. |
| L2 calendar read | **Fixed and verified** — bounded JSON, strict model revalidation and sanitized read failure. |
| L3 roster scalability | **Partially fixed** — `re` is module-level and the covering index exists, but the full query still had per-speaker scalar scans. Improved in this change. |
| L4 private coupling | **Fixed on production paths** — public retained-audio wrappers and router `configure`. |
| L5 OAuth callback count | **Fixed and verified** — concurrent count decrements in `finally`. |
| L6 build-time TCC reset | **Fixed in this change** — now requires `SCRIBELOOP_RESET_TCC=1`. |

## Findings and remediation

### P-M1 — web reconnect snapshot was unbounded (medium)

The original review over-described the blast radius. The unbounded serializer was in the **development
web UI** snapshot branch. Native desktop already requested `DesktopContract.snapshot()`, whose history
page is metadata-only and has existing regressions proving no `segments` field.

The web path serialized up to 100 sessions with full transcripts, full provisional notes, speaker turns,
grounded summaries and rolling notes. It had no response-size cap because both `desktop.check_size()` and
the Swift frame cap are native-only.

**Fix:** web snapshots are now `paged: true` and metadata-only. A validated `conversation_page` action
returns one session page: at most 500 segments, 100,000 characters of notes, bounded turns plus summary,
rolling draft, language lock, usage and audio state. The web client lazily loads the selected conversation,
supports server-total pagination and follows a summary citation to its page.

Remaining watch item: mutation events such as `transcript_replaced` and `people_changed` can still include
full affected transcript rows. This was outside the reconnect defect; paginate those events only if
real-world profiling shows a need.

### S-L1 — cleanup warning could include model text (low)

A total cleanup failure could stringify a Pydantic `ValidationError`, whose input value may include model
response/transcript text. The warning now emits the exception class and optional HTTP status only. Durable
cleanup failures remain sanitized. A canary regression proves transcript/model text is absent from the UI
warning and event.

### L6 — routine builds reset TCC permissions (low)

`build_desktop.py` no longer resets Screen Recording or Calendar permissions by default. Rebuilding the
canonical app resets those grants only when the operator sets `SCRIBELOOP_RESET_TCC=1`. Fixture and
noncanonical destinations still never reset. Packaging regressions cover default-off, opt-in and scope.

### L3 — full roster query (low)

The prior covering index helped the first speaker-membership check, but the full query still used
correlated scalar subqueries for each speaker's sample and first timestamp. `ROSTER_QUERY` now materializes
the session's utterances once and computes each speaker's first sample and timestamp in one grouped pass.
A regression checks the actual query plan has no correlated scalar subquery and validates sampled text.

### P-L2 — reservation usage read amplification (low)

`cloud_request` rows remain durable pre-dispatch billing/reservation evidence and are not compacted away.
Instead, `SessionStore` keeps a small per-session aggregate in memory, initialized from durable rows and
updated on each reservation. This avoids reparsing the whole reservation tail every 10 seconds per stream
while preserving restart-safe audit rows. Deletion clears the aggregate. A trace regression proves cached
reservations do not issue another payload scan.

## Security verification

- Local API remains loopback-bound with per-launch token, strict first-message compare, host/origin checks,
  16 KB incoming WS limit and TrustedHost middleware.
- Native protocol remains private-pipe bootstrap plus desktop read receipts; snapshot/detail paths remain
  bounded and authenticated.
- The new web read uses strict Pydantic validation (`extra='forbid'`, bounded offset/anchor), fixed limits
  and current-revision segment reads. Unknown sessions and bad citations fail closed.
- SQL continues to use bound parameters; the remaining interpolated table names are hardcoded internal
  tuples only.
- SQLCipher 4, hex PRAGMA without tracing, cipher memory security, WAL, secure delete and no plaintext
  fallback remain unchanged.
- Keychain work and other blocking I/O remain off the event loop; SQLite remains on the event-loop thread.
- Development UI DOM updates remain `textContent`/`createElement`/attribute based; no HTML insertion was
  added.
- Subprocess paths remain fixed-argv/no-shell with bounded stdio and timeouts; deserialization remains
  conservative.

## Performance verification

- Native desktop reconnect and detail reads were already paginated and remain unchanged.
- Web reconnect now serializes bounded session metadata only; transcript mass is one selected page.
- Recording-gate hot path remains indexed and bounded.
- Cloud usage no longer reparses all reservations on every 10-second block.
- Roster construction no longer multiplies segment scans by speaker count.
- Ollama lifecycle remains loopback-only, credential-free and bounded at quit.

## Validation

- Python: **797 passed** with `.venv/bin/pytest -q`.
- Frontend replay: `node tests/frontend_replay.mjs` passed, including the new paged snapshot/page case.
- Native desktop audit: documented `swiftc` build of `DesktopAuditTests` passed.
- Ruff: all checks passed.
- `git diff --check`: clean.
- Production bundle was rebuilt as `0.1.45 (46)` with the TCC reset opt-in left disabled. The bundle passed
  the packaging validator, ad-hoc signature verification and `verify_desktop.py`.
