# Failed call retry and capture authorization — October 29, 2026

Internal build **0.1.4 (5)**, Apple Silicon/macOS 13+.

## Report and findings

The first accepted browser call opened the recording review but capture failed.
The user reported all macOS permissions enabled, and a subsequent call did not
open another review prompt.

The prior helper reduced multiple thrown native startup errors to one generic
permission/device message. That message could not establish which permission or
source failed. In addition, accepting a call dismissed its candidate before
capture startup completed. A quick redial could retain that dismissed episode
and candidate ID, preventing the native UI from prompting again.

## Changes

- Native startup failures now carry a fixed stage/domain and numeric OS error
  code. Only allowlisted fields/messages cross into app diagnostics; arbitrary
  localized error text, paths and content are not displayed.
- Preferences has **Check capture access without recording**. The app invokes
  native shareable-source enumeration and microphone authorization status through
  its own helper process. No audio stream, microphone session, provider request
  or recording is created. macOS may enforce its normal authorization UI.
- A failed auto-workflow recording arms its dismissed episode for the next
  healthy microphone off→on edge. Sustained activity in the same failed call does
  not repeatedly prompt. A new start after two seconds of known full inactivity
  also re-confirms a dismissed episode before the normal 15-second grace expires.
- Regular Not now remains suppressed across mute/unmute when output continues.
  Output-only notifications do not start a call. Unknown observations do not
  establish a new input edge. All browser suggestions remain heuristic.
- Failed sessions have **Review and retry recording**, prefilled with the prior
  source/microphone but requiring a fresh explicit Record choice. Rejected start
  commands keep their review visible instead of immediately closing it. There is
  no automatic capture, paid retry or permission reset.

## Host observations

The read-only check, invoked through the rebuilt app, returned native diagnostic
`output_enumeration/screen/-3801`: ScreenCaptureKit access declined. At the same
time, macOS System Settings displayed **ScribeLoop.app on** under Screen & System
Audio Recording, and the app reported microphone authorization allowed. Restarting
the same build alone did not resolve the `-3801` response.

This establishes a mismatch between the visible setting and authorization for
the launched helper. It does not establish whether stale ad-hoc signing trust,
responsible-process identity, or another macOS authorization condition caused it.
Developer ID signing remains absent and is needed for the planned release path.
Do not claim that the checkbox being on proves successful stream startup.

The user explicitly approved refreshing only ScribeLoop's Screen & System Audio
Recording authorization and restarting. After local macOS authentication, the
off state and restored on state were each observed; reopening the same build
still returned `-3801`. The old ScribeLoop entry was then removed and the exact
current `dist/macos/ScribeLoop.app` bundle re-added through System Settings.
After relaunch, the in-app check reported **Meeting-audio access: allowed.
Microphone: authorized. Display: available. No audio was recorded.**

This resolves the observed enumeration-authorization blocker for this build and
supports a stale permission-entry/signature explanation. No other app permission
or speaker setting was changed. There was no direct TCC database edit, global
permission reset or disabled security protection. The bundle was not rebuilt
after this successful check; its ad-hoc designated requirement is code-hash based,
so future rebuilds may require replacing the permission entry again. Stable
Developer ID signing remains necessary for release/update continuity. Successful
enumeration does not establish physical capture or a complete recording.

## Validation

547 development tests passed; the portable shipping runtime passed 544 tests
with the optional local-diarization module skipped. Coverage includes failed-browser quick redial with other
tab output, same-call suppression, ordinary Not now/mute behavior, and sanitized
access-check diagnostics. Production rebuild, native macOS 13 compilation, Ruff,
whitespace, bundle audit and internal-DMG verification passed. The updated app
connected and its new retry/control UI was inspected. No call was joined, audio
captured or played, or paid provider request made during this work. Speaker output
was not enabled. A successful physical recording and a successful real second-call
prompt still require acceptance after authorization is working.
