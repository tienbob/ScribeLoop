# First-use UX audit — 2026-11-09

Verdict: the notebook works for a configured user, but onboarding is not yet ready
for an unfamiliar user. Setup is spread across settings rather than presented as
a path to a successful first recording and summary.

Scope: native UI inspection of Recording settings and recording review; source
inspection of default settings, agreement, empty workspace, provider setup,
permissions and automatic notes. This was not a clean-profile usability study:
the existing workspace and macOS permissions were preserved. No recordings,
provider requests, credentials or permissions were changed for this audit.

## Findings, ranked

1. **High — Recording promises notes without checking notes readiness.**
   `DesktopApp.swift:488,509,514–525` displays a notes model, promises automatic
   notes and enables Record based on transcription configuration, without requiring
   a configured summary provider. Defaults contain no summary API key
   (`desktop_settings.py:30`); automatic notes reject an unconfigured provider
   (`server.py:289`). A fresh user who only connects transcription can finish a
   recording without the promised outcome. Add separate Transcription ready /
   Notes ready states. Offer Configure notes or an explicitly named transcript-only
   recording mode; do not require notes when the user chooses transcript-only.

2. **High — Missing transcription setup leads to a disabled action, not recovery.**
   `DesktopApp.swift:505–525` tells the user to add a key in Preferences, but offers
   no button from the recording review to the relevant settings. The first-use
   empty workspace (`DesktopApp.swift:337–344`) describes call detection rather
   than what remains to be configured. Add a persistent setup checklist with direct
   actions, and an Open transcription settings action in recording review.

3. **High — Local mode makes an incorrect privacy promise.**
   `DesktopApp.swift:491–493` says transcription AND notes run on this Mac and
   nothing is uploaded whenever transcription uses Ollama. Notes and cleanup are
   independently configurable cloud providers. Derive disclosure from all three
   stages; e.g. Audio: on this Mac; cleanup: Gemini; notes: OpenAI. This is a
   concrete trust problem for a new user choosing Local.

4. **Medium — Permission readiness is buried and incomplete.**
   Recording settings place the long language grid before permissions
   (`Preferences.swift:425 onward`). The microphone has a readiness state, but
   meeting audio shows a generic Open settings button and a separate capture check.
   A user must discover both rather than see one clear readiness result. Put
   readiness first, distinguish microphone-only from meeting capture requirements,
   and provide Check again plus an explicit ready/needed/error state. Ask for
   permissions when their chosen capture mode needs them.

5. **Medium — The agreement reads like implementation documentation.**
   `DesktopApp.swift:364–369` mixes essential consent/privacy information with
   request counts, MB limits, token limits, encryption, exports, retention and
   voice-recognition semantics. The empty notebook remains below the agreement;
   there is no focused introduction or progress. Show short essential statements
   with expandable details, followed by Continue setup. Keep the actual first-use
   agreement and Record / Not now per call.

6. **Medium — Call watching looks ready even when recording is not.**
   `DesktopClient.swift:218–224` returns Watching for calls from discovery status
   alone, independent of transcription and notes setup. Distinguish Detection on
   from Ready to record; surface missing setup without implying detection failed.

7. **Medium — Cleanup labeling and state are misleading.**
   `Preferences.swift:328` says Clean up transcript locally while allowing Gemini,
   OpenAI and other cloud providers directly below it. Default cleanup is on, but
   an empty cleanup model causes `Settings.cleanup()` to return None
   (`desktop_settings.py:155–165`). Rename to Improve transcript wording, show its
   actual destination and configuration state, and keep optional cleanup out of
   the primary onboarding path.

8. **Low — First recording review is overloaded and not capture-specific.**
   Observed native review shows the all-browser-tabs warning even for Microphone
   only, followed by provider/model IDs, audio cap, languages, processed/raw source
   and vocabulary count. `DesktopApp.swift:480–509`. Show source, destination and
   Record / Not now prominently; reveal source-specific warnings only when
   relevant, with technical options in details.

9. **Low — Account and experimental setup compete with essential setup.**
   `Preferences.swift:117–135` gives Account and Experience the same top-level
   prominence as Recording and Models. Account can show unavailable service setup
   and a disabled sign-in. Keep optional account/calendar/experimental choices
   outside the initial required path; make their optional status explicit.

## Recommended first-use sequence

1. Welcome: automatic call detection, Record / Not now, what is retained and where
   audio/text go. Accept the app agreement once.
2. Connect transcription: provider + key + clearly reported configuration check.
   Explain any paid validation before sending a request.
3. Connect notes, or explicitly choose transcript-only. Optional cleanup later.
4. Choose microphone-only or meeting capture; request only needed permissions and
   show a single readiness checklist. Language defaults remain easy to change.
5. Offer an optional short test recording with explicit Record; show live activity,
   interim text and Stop. Allow Skip test.
6. Finish at a Ready to record empty state explaining how detected calls prompt
   and how to start manually. Do not record automatically.

## Acceptance checks for implementation

- Fresh install with no keys has a visible next action, not a disabled dead end.
- Missing notes key never produces an unconditional promise of notes.
- Local transcription + cloud notes has accurate per-stage disclosure.
- Permission denial has a targeted recovery path; no unrelated permission request.
- Setup can be resumed after quit, with credentials retained and no auto recording.
- Detection, recording readiness and notes readiness are shown separately.
- Optional cleanup, calendar and sign-in can be skipped without blocking recording.
- Existing configured users retain their settings and are not forced through setup.

No application code changes were made as part of this audit.

## Approved implementation plan

Implement a resumable first-use checklist (provider, notes or transcript-only,
microphone, optional meeting-audio check and test), concise agreement with
expandable details, targeted setup actions from recording review, per-stage data
destinations, persistent automatic-notes preference, and permission-first
Recording settings. Existing populated workspaces bypass the checklist. No API
validation runs automatically; configuration checks do not prove a key is valid.
Keep calendar/account/experimental setup optional. Rebuild and inspect native UI.

## Implemented

- Fresh empty notebooks show a resumable checklist after the concise agreement;
  Help → Review setup opens the same flow for existing users.
- Checklist links directly to providers and permissions; notes can explicitly be
  disabled for transcript-only use. Optional test recording is never automatic.
- Automatic-notes preference persists in Keychain settings and is honored for
  new recordings, accepted calls and transcription reruns. Manual Generate notes
  remains available when a notes provider is configured.
- Recording review shows independent audio, cleanup and notes destinations;
  missing notes configuration produces a transcript-only action and a setup link.
- Permission controls precede the language list; meeting-audio checking is optional
  for microphone-only setup. Detection status distinguishes setup from readiness.
- Cleanup wording no longer claims cloud processing is local. Account setup is
  described as optional. Agreement and review details use progressive disclosure.

Validation: 828 tests passed; Ruff and whitespace checks passed. Development build
0.1.47 (48) packaged and installed; bundle verification found no external native
dependencies, bundled recordings or bundled operator transcription credentials.
Configuration readiness is deliberately distinguished from verified provider
credentials; no paid validation request runs during onboarding. Fresh macOS
permission prompts remain user-controlled.

## Capture-permission follow-up (requested)

Add an explicit Allow meeting apps & browsers button in setup, Recording settings
and meeting recording review. Request macOS Screen Recording access directly from
the native app, then check actual capture access; if macOS has already denied it,
open the matching System Settings pane. One ScribeLoop permission covers native
meeting apps and browsers; browser capture still includes all tabs.

Canonical local builds now reset ScribeLoop's ScreenCapture and Microphone TCC
entries after successful bundle validation, without an environment opt-in. Preview
and staging builds do not alter permissions. Installing a staged build invokes the
same reset explicitly. Never reset other apps, Keychain credentials or all TCC.
The user must approve permissions again; resetting does not grant access.

Validated follow-up: 828 tests passed. Build 0.1.48 (49) installed; the build
reset function successfully reset ScreenCapture and Microphone for
`ai.scribeloop.desktop`. Keys, calendars and recorded data were preserved.
