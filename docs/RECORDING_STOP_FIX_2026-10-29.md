# Recording Stop and call-end status — October 29, 2026

Internal build **0.1.3 (4)**, Apple Silicon, macOS 13+.

## Report and findings

The user reported that a recording appeared to continue after a browser Teams
call ended and that Stop produced “Request rejected. Check authorization, fixture,
and active replay.”

- The old Stop route fell through to the replay runner when the supplied session
  was no longer the recorder's active session. Late/repeated Stop requests could
  therefore produce a misleading replay error instead of reconciling state.
- Native UI listened for `auto_call_ending`; the backend actually publishes
  `auto_call_status`. The ending countdown and unknown-state feedback were hidden.
- Capture failure was published before releasing the recorder's active ID,
  permitting a snapshot during that interval to retain an already-failed capture.
- On inspection, the saved Brave session was Failed with zero segments and no
  active recording. Its stored diagnostic said capture failed before transcription;
  the generic diagnostic does not establish the exact underlying native error.
  Preferences currently reported microphone access needed. Permission was not
  granted or changed during inspection.

## Changes

- Native Stop sends an authenticated `stop_recording` command targeting the
  current capture rather than a possibly stale selected session. It bypasses the
  uncertain-action guard, as Stop already should. Repeated/idle requests succeed
  without starting work and return authoritative recording/processing state.
- Legacy session-specific Stop accepts a known meeting that has already stopped;
  it never routes that saved meeting to replay. Genuine replay Stop retains its
  existing route. Malformed/unknown requests still fail validation.
- Failed capture releases active ownership before publishing failure. Post-capture
  processing has its own ID in reconnect snapshots.
- Native UI handles the real call-status event, displays the inactivity countdown
  or a manual-Stop explanation when detection is unknown, and shows Stopping
  capture until capture finishes. Saved failures display the allowlisted diagnostic.

## Boundaries

Auto-stop is **60 seconds of continuous healthy inactivity**, not an official
Teams call-ended event. Browser capture/detection covers the whole browser;
another tab's audio or browser-held input can keep activity active. Unknown or
unavailable observations do not assert that a call ended. Manual Stop remains
independent of detection and the recording preset still bounds capture duration.

## Validation

- 539 Python tests passed. Integration coverage stops a generated microphone
  stream through the new command, checks native source closure, then repeats new
  and legacy Stop after completion without replay rejection or another upload.
- Failure regressions assert that capture ownership is cleared when failure is
  published. Native macOS 13-targeted smoke validates ending/unknown/resumed status
  and ignores events for another session.
- Production rebuild, Ruff, whitespace and bundle audits passed: 38 native
  binaries, no external native dependencies, no bundled recordings/operator key.
- Reopened native app connected, showed the saved failed session and its diagnostic.
  Preferences was inspected and cancelled without saving. No recording, playback,
  permission change, speaker output or provider request was made.
- Version 0.1.3 internal DMG was generated and passed image verification. It is
  still ad-hoc signed/unnotarized. Physical successful capture/Stop on a consented
  call remains to be validated; the failed user session does not establish it.
