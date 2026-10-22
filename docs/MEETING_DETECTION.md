# Meeting discovery — first implementation increment

Implemented **2026-10-22**: metadata-only macOS observer, installed-app registry,
typed discovery/source contracts, pure candidate state machine, diagnostic CLI,
and shared host-clock mapper. These are M1/M2 foundations from the
[implementation plan](PHASE_1_IMPLEMENTATION_PLAN.md), not a complete M2 UI or
validated Teams/Slack integration. No recording path is enabled.

## Run the metadata diagnostic

```bash
.venv/bin/python scripts/build_capture.py --kind observer
.venv/bin/python scripts/check_meeting_detection.py --duration-s 10
```

The helper emits one versioned JSON snapshot and exits; the diagnostic polls it
once per second (up to five minutes). Output includes installed candidate app
identities and changes to suggested candidates/availability. It never opens
audio streams, starts an app, inspects browser pages/URLs, or stores an activity
history. Redirecting its output is an operator choice, not an app persistence
feature. The browser replay server is unchanged and does not run this observer.

## Signals and boundaries

The native helper reads `kAudioHardwarePropertyProcessObjectList` and each
process's PID, bundle ID, `kAudioProcessPropertyIsRunningInput`, and
`kAudioProcessPropertyIsRunningOutput`. Local Apple SDK `AudioHardware.h`
documents these as active I/O/stream indicators, not speech or meeting signals.
Microphone mute may not stop I/O; media playback and microphone tests may look
like calls. Consequently the detector emits suggestions only.

The installed-app registry reads specific application bundles under
`/Applications`, including nested helper plists in ordinary Frameworks and
versioned Chromium Helpers layouts. It matches exact bundle IDs, never string
prefixes or names of arbitrary running processes. This is not publisher-signature
verification and does not prove per-app output routing. Apps in other directories
can be supplied through the registry's explicit roots parameter; device/source
enumeration in the UI is still pending.

The registry recognizes installation paths for Teams, Teams classic, Slack,
Zoom, Brave, Chrome, Safari, and Firefox. A missing installation contributes no
identity. Recognition is not a support claim. Multiple browser tabs cannot be
distinguished; every browser candidate remains ambiguous.

Native property failures and process-list races produce incomplete/unavailable
snapshots. Such snapshots never count as idle time or satisfy debounce. Missing
or unknown properties are not false activity values. Empty successful process
lists are permitted, but cannot establish that live-call signals work.
Successfully queried unbundled clients are ignored because they cannot match an
installed-app bundle identity; they do not invalidate unrelated app observations.

## Candidate contract

- Defaults: three-second debounce, fifteen-second end grace, five-second maximum
  observation gap. These are configurable starting values, not tuned targets.
- Aggregate positive input/output evidence across an app's exact known helpers.
  Both directions in a meeting app yield `possible`; one direction or any browser
  yields `ambiguous`. An open idle app yields no candidate.
- Keep one candidate ID per app/activity episode. Dismissal suppresses that
  episode, including activity changes and observer failures. A new ID appears
  after healthy idle observations satisfy end grace and activity returns.
- Silence is not examined. Loss of activity only removes a suggestion after the
  grace period; the detector has no access to recording Stop.
- Unknown/incomplete observations and long gaps reset unconfirmed debounce and
  end-grace accumulation. Existing suggestions/dismissals remain in memory;
  consumers must honor `available` and `last_seen`, and revalidate before use.
  IDs are local to the detector instance; future API integration must scope them
  to a server incarnation and reject stale selections.

## Recording and clock contracts

`CaptureSelection` requires an explicit `application` or `system` scope and a
microphone device identity. Application scope requires nonempty bundle IDs;
system scope forbids them. This object is not consent or permission to record.
The future coordinator must validate live sources and session consent server-side
before opening PCM, and reserve one slot across recording and replay.

`RecordingState` defines preparing, recording, stopping, completed, failed, and
interrupted; existing replay storage has not been changed to use these states.
Planned transitions are preparing → recording/failed, recording → stopping/failed,
and stopping → completed/failed. Restart turns any durable nonterminal recording
state into interrupted; resuming transcription never resumes microphone capture.

`SessionClock` maps integer **common monotonic host nanoseconds** onto session
milliseconds, retaining offsets and gaps without float precision loss. Native
adapters must convert device timestamps into that clock and account for drift.
Python receipt time and independently zeroed source clocks cannot substitute.
The existing SLP1 capture adapter remains unchanged; wiring/versioning the native
capture timestamps and implementing microphone capture belong to M3.

## Verification and next work

- Native helper compiled locally with the installed command-line SDK and emitted
  protocol version 1. A five-second metadata diagnostic completed successfully.
- The sandboxed diagnostic returned zero process objects. An authorized host
  check outside the sandbox returned **25 bundled processes, one output-active,
  none input-active**, available/complete. This establishes positive output
  activity visibility on this host, not real-call detection or input visibility.
  Empty sandbox results must not be advertised as evidence that no call exists.
  No microphone or capture stream was opened for either check.
- Installed Teams classic `com.microsoft.teams` and its
  `com.microsoft.teams.helper` identities were verified from local plists. Brave
  and its nested helper identities were discovered locally. Slack was absent at
  the expected installation path; its real identities/call behavior remain untested.
- Deterministic tests cover activity debounce, helper aggregation, idle apps,
  dismissal/new episodes, mute/output-only ambiguity, browsers, multiple
  candidates, observer gaps/failures, malformed metadata, explicit capture scope,
  and shared-clock offsets.
- Verification: **152 tests passed** in the full local suite, frontend replay
  regressions passed, 43 application modules passed import smoke, and changed
  Python code passed Ruff. Native metadata helper compilation passed separately.

Next: establish input-activity visibility and real Teams/Slack metadata
traces; finish authenticated candidate/manual-selection UI (M2). Real encrypted
recording, permissions UX, microphone capture, and alignment checks remain M3/M4.
