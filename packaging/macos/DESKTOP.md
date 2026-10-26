# ScribeLoop native desktop app

The full local development app is `dist/macos/ScribeLoop.app`. It runs the
cloud-only meeting core as its own bundled child; no terminal server, browser,
local speech model, installed Python or Homebrew library is needed to **run** it.
Building still requires Xcode and the separate packaging environment.

## Launch and configure

```sh
open 'dist/macos/ScribeLoop.app'
```

1. Open Preferences using the gear button. Add your Deepgram key and summary
   provider key. OpenAI / `gpt-5.4-mini` is the default; compatible providers can
   use their own base URL and model. Keys are saved in macOS Keychain. The app
   deliberately does not import your development `.env` automatically.
2. Choose Auto or a fixed conversation language and a 5-minute, 1-hour or 3-hour
   recording preset. Vietnamese requires its fixed choice. Saving settings makes
   no provider request; changes require idle capture/text processing.
3. Accept the first-use agreement. Agreeing never starts recording. Request
   microphone access from Preferences when ready. App/system output also needs
   macOS Screen Recording permission; open its settings from Preferences.
4. A likely Teams/Slack/Zoom/browser call opens a source-aware Record / Not now
   panel. Detection uses audio-activity heuristics, not official meeting APIs.
   Browser capture includes **every tab** in the selected browser. Revalidate and
   choose Record only when everyone captured has agreed. Manual New recording
   supports microphone-only, a meeting app, or system audio plus microphone.
5. Confirmed words and anonymous speaker labels arrive while recording. Stop is
   available in the window and menu bar. Detected calls stop after 60 seconds of
   known inactivity; unknown activity does not guess that the call ended. Manual
   recordings use Stop or the selected duration limit. Stop finishes existing
   streams, without an automatic full-audio second pass.
6. Notes and a topic title follow when configured. Cited notes open and highlight
   their transcript page. Saved meetings support Markdown export, speaker labels,
   people links, recovery/resume, bounded in-memory playback and audio archive.
   Manual rerun explicitly uploads the retained audio again and may incur charges.

Closing the window leaves the app and menu bar running. Quit stops capture and
cancels text processing, with bounded shutdown; interrupted work is never resumed
silently. Pause detection stops the backend observer while idle. Reconnect reads
snapshots and request status; it never replays state-changing or paid commands.
If a receipt is unavailable after a crash, review the current state and acknowledge
uncertain actions before making another change.

## Data

New workspaces use `~/Library/Application Support/ScribeLoop/meetings.sqlcipher`
and adjacent encrypted `recordings/`. Transcripts/notes use SQLCipher; audio uses
authenticated encrypted chunks. Keys use the existing storage Keychain namespace.
Unarchived audio expires after 30 days while running/idle and the agreement is
accepted. Archive keeps audio until unarchived or deleted. Notes/transcripts remain.

To use development meetings, stop the development server and choose **Use an
existing database** in Preferences. Select its `.sqlcipher` file in place. No
copy/relocation or key remapping is performed. The original adjacent recordings
and path-bound Keychain key remain authoritative. The selected database path is
the only workspace reference persisted in UserDefaults; transcript/audio/provider
keys are not stored there. Database schema downgrade is not supported.

## Build and validate

Prepare the isolated environment as described in [M0 build instructions](README.md),
then run:

```sh
build/macos/venv/bin/python packaging/macos/build_desktop.py
.venv/bin/python packaging/macos/verify_desktop.py
.venv/bin/pytest -q
node tests/frontend_replay.mjs
```

Build scripts select the installed Xcode per command and never change global
xcode-select. Dependencies are resolved in the separate cloud-only environment;
`build/macos/requirements-resolved.txt`, `desktop-manifest.json` and
`desktop-audit.json` describe the actual local build. The build is ad-hoc signed,
with a conservatively host-only minimum OS. `verify_desktop.py` scans native
libraries/rpaths, signatures and prohibited bundled recordings/ML weights.

The separate preview app exercises the **same** SwiftUI client and production
protocol using generated encrypted data, fake Keychain and disabled external
network/capture. It never opens your real workspace:

```sh
build/macos/venv/bin/python packaging/macos/build_preview.py
'build/macos/ScribeLoop Preview.app/Contents/MacOS/ScribeLoop' --desktop-smoke
```

The preview test entry point is not included in the production build. The smoke
switch refuses to run in the production bundle so tests cannot accidentally open
real data. Native smoke verifies paging 230 segments, agreement, secure settings
adapter, reconnect, backend pause and owned shutdown. Python integration tests
also verify actual cloud-stream processing with generated PCM/fake WebSockets:
speaker-labelled words before Stop, duplicate dispatch suppression, settings freeze,
reconnect preserving capture, and no full-audio second pass.

## Acceptance status

Local development implementation is built. Readability was verified by screenshots
of notes, transcript and preferences after replacing fixed paper backgrounds with
semantic macOS surfaces and giving the sidebar a 260-point minimum width.

Real microphone/system-audio permission attribution, physical call timing, sleep/
wake and device changes, clean-Mac/older-macOS runtime compatibility, login-item OS
approval, signed upgrades, notarization and installer distribution remain acceptance
checks. Developer ID signing remains deferred. Do not treat an ad-hoc development
app as a notarized release or the synthetic preview as real-call validation.

### Summary provider presets

Preferences offers OpenAI and Gemini (Cloud), Ollama and LM Studio (Local), and
Custom. Presets set the endpoint automatically and hide the URL editor. Custom
exposes it for other compatible servers or nonstandard local ports. Choose a model
ID from the selected provider, or an installed model ID for local servers. The app
does not download or launch local models. Changing destinations clears the previous
saved summary key unless a new key is supplied. Local server authentication is
optional. Deepgram transcription remains unchanged.

Endpoint references: [Gemini compatibility](https://ai.google.dev/gemini-api/docs/openai/),
[Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api/introduction.mdx),
[LM Studio compatibility](https://lmstudio.ai/docs/developer/openai-compat).
