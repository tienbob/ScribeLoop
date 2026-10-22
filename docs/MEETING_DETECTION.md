# Meeting discovery — observer and authenticated source UI

Implemented **2026-10-22**: metadata-only macOS observer, installed-app registry,
typed discovery/source contracts, pure candidate state machine, diagnostic CLI,
and shared host-clock mapper. The second increment adds the authenticated
discovery service, candidate/dismissal UI, and manual source selection. These
implement M2's service/UI from the [plan](PHASE_1_IMPLEMENTATION_PLAN.md), but
do not establish real-call Teams/Slack support. No recording path is enabled.

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
feature.

The normal server CLI now starts metadata discovery alongside replay. Build the
observer before launching it; a missing helper leaves discovery unavailable and
manual selection usable. `--no-discovery` disables the process observer while
retaining installed-app choices. Programmatic `create_app()` defaults to disabled
discovery; tests explicitly inject a deterministic service when needed.

## Authenticated UI and source selection (second increment)

- Only authenticated WebSocket snapshots/events contain installed app choices
  and candidates. Static pages and `/healthz` expose no process inventory. The
  service sends state changes rather than flooding clients with timestamps.
- The notebook displays activity evidence, possible-call/browser ambiguity,
  unavailable/off states, Use source, and Dismiss. Dismissal is shared across
  local clients for the ongoing episode; reconnect restores current candidates.
- Candidate IDs include a random service incarnation. Selection checks the
  current installed-app registry, matching app/candidate identity, observer
  availability, and a five-second freshness window. Removed installations or
  changed identity mappings invalidate old candidates. Arbitrary bundle/device
  values and client-supplied raw bundle lists are rejected.
- Manual selection works without a healthy detector: select an installed app
  or explicitly choose **All system audio · includes other apps**. No app or
  whole-system scope is implicitly selected. Apps need not be in a live call to
  save this source preference; actual capture must revalidate running sources.
- Microphone choice currently offers only **System default microphone · not
  checked**. This is a source preference, not device enumeration, permission
  approval, or confirmed microphone availability. Those remain M3 work.
- A selection acknowledgement is private to the requesting connection. Its
  request ID prevents an old reply from replacing a newer preference. Preferences
  and dismissal state stay in memory; reconnect requires source confirmation
  again. No session/audio/transcript row is created by these controls.
- Record is always disabled, and no recording command exists. Neither candidate
  detection, selecting a source, nor dismissing it opens PCM or grants consent.
  Replay controls and existing session history continue to work independently.

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
can be supplied through the registry's explicit roots parameter. The UI enumerates
installed app choices; actual running capture/device enumeration remains pending.

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
  IDs are local to the detector instance; the implemented API adds an incarnation
  prefix and rejects stale selections.

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
- Verification: **162 tests passed** in the full local suite, frontend replay and
  meeting-discovery regressions passed, 44 application modules passed import smoke, and changed
  Python code passed Ruff. Native metadata helper compilation passed separately.
- API/service checks cover auth, stale/foreign IDs, private acknowledgements,
  shared dismissal, observer/registry failures, source validation, and reconnect.
  Frontend checks cover literal rendering, stale replies, selection invalidation,
  disconnected controls, manual fallback, and permanently disabled Record.
- Brave interaction/visual check on the encrypted local server: discover installed
  choices, manually select Teams classic, receive the private confirmation, retain
  encrypted history, and verify Record remains disabled. No real call, new audio,
  microphone permission, candidate-positive browser run, or mobile layout check
  is claimed. Candidate interaction is covered with deterministic API/JS tests.

Next: establish input-activity visibility and real Teams/Slack metadata traces.
Implement microphone capture, common-clock native adapters, and device/source
checks (M3), followed by encrypted recorder integration (M4). Signing and original
Phase 0 release-validation gates remain deferred as documented.
