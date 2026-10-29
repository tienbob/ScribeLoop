# Call end reasons: definitive OS signal + reason taxonomy (2026-10-29)

Research question: how do comparable meeting recorders handle call end, and what
can ScribeLoop adopt without violating its fail-closed posture?

## Scope honesty boundary

`selected_app_exited` fires only when the meeting app's process quits. Most hang-ups
leave Zoom/Teams/Slack running, and browser calls cannot trigger it at all. The
60 s healthy-inactivity monitor remains the primary end heuristic; this change adds
a **definitive** end signal for the app-quit case and makes every end reason
observable. Window-list monitoring (meeting window disappears) is a phase-2
candidate only, behind measurement.

## Research summary (official sources checked 2026-10-29)

- **Otter desktop:** automatic meeting-end detection with a Keep recording
  override. Its help also documents a no-conversation warning after 10 minutes,
  followed by a 15-second stop countdown. The exact meeting-end detector is not
  published. [Desktop help](https://help.otter.ai/hc/en-us/articles/35973988280215-Otter-Desktop-App-Mac-Windows).
- **Recall.ai bots:** configurable everyone-left, silence, and bot-only timeouts.
  Participant membership is available to a bot; ScribeLoop cannot infer it from
  the lifetime of a browser process. [Automatic leave behavior](https://docs.recall.ai/docs/automatic-leaving-behavior).
- **Jamie:** its August 16, 2026 changelog describes AutoStop when a meeting goes
  silent. It does not establish the microphone-release algorithm claimed in the
  earlier draft. [Official changelog](https://www.meetjamie.ai/changelog).

Design inference: distinguish definitive process exit from activity heuristics,
keep an explicit stop reason, and preserve the countdown/Keep recording control.
Earlier unsupported product thresholds and algorithm claims were removed.

## Probe evidence (2026-10-29, macOS 27.2, disposable silent target)

The previous TextEdit/blocking-sleep findings were not accepted as evidence.
`.spike/probe_liveness.swift` now uses async main and `Task.sleep(100 ms)`, like
the helper, without explicitly servicing a run loop. It launches only the silent
fixture in `.spike/target.swift`; SIGTERM invokes AppKit's terminate action,
SIGKILL forces exit, and a third scenario immediately launches a replacement.
Raw observations (local-only, excluded from Git): `.spike/liveness-trace.txt`. SCK enumeration adds ~30–50 ms
per tick. Results are observation bounds, not benchmarks of every supported OS.

| Scenario | proc_pidinfo | NSRunningApplication | NSWorkspace | SCK |
|---|---|---|---|---|
| Graceful terminate | gone ≤48 ms | stale at first sample; nil ≤188 ms | absent ≤188 ms | target absent ≤48 ms |
| Force quit | gone ≤50 ms | stale at first sample; nil ≤190 ms | absent ≤50 ms | target absent ≤50 ms |
| Force quit + fast relaunch | old PID gone ≤66 ms | old PID nil ≤194 ms | replacement present ≤194 ms | replacement present ≤194 ms |

- No zombie state observed (reaped within one poll tick); three-state `pidGone`
  still guards `SZOMB` explicitly.
- Composed start time is stable; replacement PID and start differ. Actual PID
  recycling was not observed and must be covered by deterministic fixtures.
- SCK returned one entry for the fixture but **two** for
  `com.apple.SafariPlatformSupport.Helper`. One-per-bundle is not a valid global
  assumption; Teams/Electron multiplicity remains unmeasured.
- Gate decision: retain proc-only identity checks and support arbitrary captured
  sets. Never infer a main process from workspace visibility. Every captured
  identity must be definitively gone; an unknown identity prevents this signal.
  A new process does not restore the original SCK filter's captured identities.
  Workspace staleness was brief here, not the 12 s claimed in the old draft.

## Reason taxonomy (single source of truth)

| `end_reason` | Producer | Semantics |
|---|---|---|
| `manual_stop` | User Stop (native menu/window, web) | Explicit user end |
| `auto_app_inactive` | `monitor_calls` | 60 s healthy inactivity |
| `auto_microphone_released` | `monitor_calls` | Opt-in mic-release signal |
| `selected_app_exited` | Native helper (new) | Definitive OS signal, clean end |
| `duration_limit` | Runner `max_s` self-stop | Preset cap reached |
| `capture_failed:<code>` | Failure path | Diagnostic code appended |
| `interrupted` | `recover()` on restart | Core restart while nonterminal |
| `unknown` | Nothing set a reason | Honest fallback (never `manual_stop`) |

## Implementation contract

### Native (SLE2 helper)

- Poll path: capture pids + start times at startup from `SCRunningApplication`;
  require 20 consecutive all-gone polls (~2 s) before `sourceEnded`; uses
  `proc_pidinfo` only. A relaunch does not silently retarget the original filter.
- `didStopWithError`: set `streamDead` **first**, then run the ~1.5 s bounded pid
  wait on a detached task; if the captured pids are gone → `sourceEnded(
  "selected_app_exited")`, else `fail()` (real failure, unchanged).
- `sourceEnded` writes one allowlisted stderr marker line `Source ended: <reason>`
  then requests the same drain path as controller STOP (resampler drain → SLE2
  completion packet → exit 0). Once-only guarded.
- `stopCapture()` skipped when `streamDead` is set; otherwise `try?`.
- selfTest fixtures: recycled pid (non-GUI owner), helper death, main gone with
  `streamDead` true/false, relaunch with new pid, composed start equality.

### Python contracts

- Marker scan: per-line match, **last matching line wins**, allowlist-validated
  (`SOURCE_END_REASONS = {'selected_app_exited'}`); unknown/junk stderr never
  masquerades as a reason and never fails a completed drain.
- `resolve_end_reason(stop_reason, capture_end_reason)`:
  `STOP_REASON_WINS = {'duration_limit','auto_app_inactive','auto_microphone_released','app_shutdown'}`
  then `capture_end_reason` (beats `manual_stop` only), then `stop_reason or 'unknown'`.
- `stop()` accepts only `{'manual_stop','auto_app_inactive','auto_microphone_released'}`
  (duration_limit stays internal); first-writer-wins under a lock;
  `_capture_end_reason` reset in `start()`.
- `record_end` persists type `recording_end` with
  `INSERT ... ON CONFLICT(session_id) WHERE type='recording_end' DO NOTHING`
  (SQLite 3.24+), rowcount-checked and logged; `get()` reads the **first** row.
- Migration v13: dedupe pre-existing `recording_end` rows then
  `CREATE UNIQUE INDEX idx_events_recording_end ON events(session_id) WHERE type='recording_end'`
  in one transaction with the version bump; mid-migration failure rolls back both.
- `monitor_calls` maps `AUTO_REASONS = {'app_inactive':'auto_app_inactive',
  'microphone_released':'auto_microphone_released'}`; missing reason →
  `auto_app_inactive` fallback (never `auto_inactivity`).
- Short-recording guard at the `automatic_notes` scheduling hook: require ≥1
  confirmed segment or ≥30 s confirmed audio; otherwise publish a session-note
  label ("Call ended early — nothing to summarize") and skip the summary attempt.
  Every skip is logged with session id and duration (measurement data for tuning).

## Implementation and validation completed

- Three-state PID read checks exact bytes, ESRCH, unknown errors, and zombie
  status by equality (BSD status is an enum, not a bit mask). Self-test covers
  short reads, EPERM, unknown errors, composed start, recycled identity,
  multi-owner unknown state, and stream-dead ordering.
- Helper confirmation, without capture: graceful quit 2.010 s, force quit
  2.012 s, fast relaunch 2.003 s, stream-dead owner exit 0.130 s. Each emitted
  exactly one allowlisted marker, SLC2 + SLE2, zero PCM, and exit 0.
  Trace (local-only, excluded from Git): `.spike/helper-confirmation.txt`. This does **not** validate an actual
  ScreenCaptureKit delegate callback on a live meeting; it exercises the same
  liveness/wait/drain code without opening devices.
- Python contracts first ran red (21 failures), then green. Full suite:
  **575 passed**; frontend replay/meeting regressions passed; changed Python
  files pass Ruff. Coverage includes marker cleanup and incomplete drains,
  reason priority, concurrent first writer, duplicate logging, real constraint
  failures, migration dedupe/rollback, per-recording reset, and automatic-notes
  skip with no provider attempt plus reconnect restoration.
- Rebuilt **0.1.6 (7)** at `build/macos/ScribeLoop-v6.app`, arm64, macOS 13+.
  Bundle audit passes with no external native dependencies, bundled recordings,
  or operator transcription credentials. Ad-hoc signed, not notarized.
- Bundled helper generated and drained exactly 480,000 samples on each track
  (30 s at 16 kHz); parsed by `MultiplexedPCMSource`, no playback.
- The running app and its permission entry were not replaced. No user recording,
  paid transcription, call join, or speaker output was initiated.

Remaining measurements: actual SCK stop callback during a disposable real capture,
Teams/Electron same-bundle multiplicity, muted/receive-only calls, and hang-up
while unrelated browser audio continues. The app-quit fix must not be presented
as proof that every hang-up stops recording. macOS 13 runtime testing remains
separate from deployment-floor/bundle validation on macOS 27.2.
