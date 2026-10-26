# Native built-app UI test — 2026-10-26

Tested the production `dist/macos/ScribeLoop.app`, using its normal controls and
real provider configuration. No alternate test bundle/source injection was used.
The user explicitly authorized real API calls and microphone use.

| Check | Observed result |
| --- | --- |
| Microphone permission | Requested in app Preferences; authorized before capture. Rebuilt ad-hoc helper identity later reports not_determined again; signed permission continuity remains open. |
| Real recording | Started and stopped with app controls. Approximately 33 seconds saved. Existing public AMI 20-second speech clip played in QuickTime as controlled microphone input. Room audio may also be captured; no transcript/audio is copied into this report. |
| Deepgram transcription | Five confirmed segments and two anonymous speakers returned. |
| Live update latency | Confirmed text was observed after Stop. This run does **not** establish satisfactory live-update latency. |
| Saved playback | Real saved recording loaded with a 33-second duration; seek to 18 seconds, play/pause and 1.5× speed worked in UI. |
| Person naming before repair | Selecting Person 2 and saving a test name was rejected twice. |
| Naming root cause | Isolated streaming-path reproduction: `commit_window` omitted `transcript_revisions`, so person-link FK insertion failed. Preview seeding used `append`, which inserted provenance. |
| Repair | Streaming writes now add revision provenance atomically. Schema 11 repairs missing revision metadata in existing streams without changing transcript/audio or rerunning providers. |
| Person naming after repair | Restarted same app/store, reopened same recording, selected Person 2 and saved “UI Test Speaker”; success visible in UI. |
| OpenAI summary | User saved key in app Preferences. Generate summary was invoked once on saved transcript. App displayed generic summary failure; no automatic retry. Cause not yet established, notes/title/citations not accepted as working. |
| Model editing/focus | AX-directed typing worked, but user reports physical typing targets the app behind. Clearer textbox and explicit user-window activation implemented. User tested physical keyboard input in the rebuilt app and confirmed “Works now”. |

Regression checks: 429 Python tests pass, including SQLCipher streamed-provenance,
existing-store repair, person linking, cited-summary persistence and rollback.
Native rebuild and dependency/signature audit pass. The summary failure and live
latency remain open follow-ups; synthetic smoke evidence does not override these
physical UI findings. The older empty recording was preserved.
