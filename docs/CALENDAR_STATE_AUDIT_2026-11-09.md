# Calendar and relaunch-state audit — 2026-11-09

Plan before implementation:
- Separate Calendar integration intent from current EventKit authorization.
  Show Connect when disconnected, Allow when a request is needed, and Fix access
  when denied. Only show Disconnect for an actually authorized integration.
- Preserve enabled intent and calendar selection through quit; recheck access on
  initialization and app activation. Reuse an existing Calendar grant without
  requesting it again. Prevent a pending access response re-enabling a disabled
  integration.
- Verify the same build across quit/reopen. A rebuilt ad-hoc binary can be treated
  differently by macOS; do not describe that as a normal-quit permission reset.
- Persist call-detection preference: currently DiscoveryService starts enabled
  and set_detection updates only memory. Restore the saved flag before starting
  observation.
- Audit capture permissions, provider keys, account, agreement and retention
  lifecycle. Build resets remain limited to ScreenCapture and Microphone as
  requested; Calendar and Keychain must never be reset by build or quit.
- Add native state/restart regressions and Python detection-persistence coverage,
  rebuild and inspect the installed app. No private recordings or API calls needed.

Findings and fixes:
- Calendar enabled intent and current authorization were conflated. The settings
  row showed Disconnect whenever enabled was saved, including denied/reset states.
  Added explicit connected/blocked/request-needed state, initialized before display,
  refreshed on app activation, with connection-specific actions and no duplicate
  settings action in the disconnected Calendar panel.
- Calendar enable now reuses an existing grant. A permission response cannot
  re-enable an integration disabled while the request was pending. Activation
  refreshes do not invalidate an in-flight permission request.
- Call detection pause was session-only. Saved detection_enabled now restores
  before observer startup. Paused startup still enumerates apps/devices for manual
  recording without starting the call observer.
- Capture-check messages are cleared on core restart instead of showing a stale
  permission result. OS authorization remains the source of truth.
- Provider keys/settings remain in Keychain; account shutdown cancels pending
  sign-in without signing out. Calendar enabled flag/selection live in UserDefaults.
  Agreement and recordings live in encrypted storage. No shutdown permission-reset
  or credential-clear path was found.

Validation:
- Same installed 0.1.48 binary was quit and reopened without rebuilding/resetting:
  microphone remained allowed and Calendar remained readable with all four selected
  calendars. The reported Calendar grant loss was not reproduced in this check.
- Native Calendar regressions passed: connected/denied/reset states, persisted
  selection and enabled intent, disconnect with retained macOS authorization,
  URL safety, recurrence and serialized reads.
- 829 Python tests passed, including detection pause across a new core instance.
- Rebuild permission reset remains ScreenCapture + Microphone only, as explicitly
  requested earlier. Calendar and Keychain are excluded. This intentional build
  reset must not be confused with normal quit/reopen behavior.

Installed development build: 0.1.49 (50). Bundle verification passed. The install
log confirms only ScreenCapture and Microphone resets; Calendar was not reset.

Installed UI check: the new binary reports Calendar request-needed and shows only
Allow Calendar access, with no Disconnect action. This differs from the unchanged
binary's successful quit/reopen check. The build/install did not reset Calendar,
so the observed difference is associated with replacing the ad-hoc signed binary,
not normal quit. The app cannot grant Calendar access itself: the permission must
be approved again for this build. Saved enabled intent and selections are retained.
A stable release signing identity remains outside this development build change.

## Meeting-audio permission button follow-up

Show Allowed and disable the permission-request button when macOS already grants
Screen Recording access. Keep OS authorization separate from the capture helper's
operational check (for example, a missing display is not revoked permission).
Refresh authorization without prompting on view appearance and app activation;
retain a separate Check access action. Apply consistently in setup, Settings and
recording review. Rebuild for native inspection.

Allowed-button implementation: OS permission is tracked separately from capture
health and checked without prompting at launch, view appearance and activation.
All three request buttons show Allowed and are disabled when granted. The capture
check remains available. Native DesktopAuditTests passed, including preservation
of the allowed state after a capture-health failure. Build 0.1.50 (51) installed
and bundle verification passed; requested capture reset applied during install.
