# Call stop and automatic notes repair — October 29, 2026

## Observed failure

A read-only review of the latest completed meeting found microphone-release
stopping disabled, a manual recording end, one failed automatic summary job on
revision 1, and a revision 2 job that completed after explicit Generate retried it.
The old build did not persist the failure classification, so the precise original
provider error cannot be reconstructed. No provider calls were made during this
workflow investigation.

## Changes in 0.1.9 (10)

- Input activity no longer inherits output-direction uncertainty. Known released
  microphone input can remain usable even when the output status is unknown.
  Unknown microphone input and stale/unhealthy observations still prevent the
  microphone-release stop signal.
- Microphone-release stopping is the default for new desktop settings. Existing
  saved choices are preserved. The user's existing choice was explicitly enabled
  through Preferences and verified after reopening the sheet.
- A known release after the selected meeting app has used its mic starts the
  existing **60 s countdown**, even if unrelated browser output stays active.
  Keep recording overrides it; input resumption rearms it. Muted/receive-only calls
  can also release input, so the override and opt-out remain important. This does
  not claim an exact browser hang-up signal.
- Automatic final notes wait for any busy summary request rather than dropping
  the one automatic scheduling opportunity. The scheduling hook binds the
  completed recorder task, rather than later waiting for a different recording.
- Automatic final notes can make **one additional provider request** if a complete
  response fails JSON/schema/citation validation. This repair is bounded across
  the entire map/reduce run, uses the same destination and consent, counts against
  the existing durable budget, and is logged as `automatic_summary_repair`.
  Timeouts, unknown delivery, HTTP errors, cancellations, and prior uncertain
  dispatches are not automatically retried. Manual Generate remains available.
- Summary job failures retain sanitized diagnostic codes. The final outcome is
  persisted and shown on a later meeting read/reconnect. A successful automatic
  or explicit retry clears the failure notice without removing the saved history.

## Validation

- Regression tests ran red for direction-specific microphone activity and known
  invalid-summary repair before implementation.
- **593 tests passed**. End-to-end fake-provider cases cover manual Stop followed
  by automatic notes, one invalid response followed by successful repair, a
  permanently invalid response that stops after two dispatches and restores its
  failure on reconnect, explicit recovery, and waiting for a busy prior summary.
- Unknown provider delivery is dispatched once and not repaired. Existing quota,
  authorization, transcript-edit, citation, cancellation, and durable-cache tests
  remain passing.
- Frontend regressions, Ruff and whitespace checks pass. The in-place app bundle
  audit passes: arm64, macOS 13 deployment floor, 38 native binaries, no external
  native dependencies, no bundled recordings or operator credentials.
- Only `dist/macos/ScribeLoop.app` is retained. Rebuilt app is ad-hoc signed;
  the existing Screen & System Audio Recording grant must be refreshed for its
  changed identity. The user approved the required local authentication.
- After the refresh and restart, the in-app metadata-only permission check reports
  **meeting audio allowed, microphone authorized, display available**. The saved
  microphone-release stop toggle remains on. No audio session was opened by this
  check.

No live meeting, new recording, audio playback or paid provider validation was
initiated. Real browser microphone release/mute behavior remains a live acceptance
check; deterministic tests establish policy and scheduling, not browser behavior.
This update does not resolve the separately measured Deepgram word-accuracy issue.
