# Preferences refresh

Desktop 0.1.21 (22), 3 November 2026.

Preferences now has four categories instead of one long form:

- Recording: duration, searchable language selection, permissions, call detection
  and login behavior.
- Models: transcription engine/key, vocabulary and conversation-notes provider,
  model/key and editable prompt. Deepgram-only recognition controls are hidden
  when Ollama is selected.
- Microphone: sensitivity and noise filter, with threshold controls under Advanced.
- Account: existing account and plan information, keeping the deferred setup out
  of the initial recording-settings view.

Detailed original/processed ASR input and refinement controls sit under Advanced
recognition. The save button reads Save changes for an edited draft and Done
otherwise. Category switching preserves the draft; Cancel discards it. Shorter
copy retains the relevant provider-upload, billing and microphone-filter caveats.
Local duration options omit the Deepgram spending-cap labels.

The Cloud processing section and its confirmation dialog were removed at the
user's request. First-use copy no longer points to that removed control. Existing
backend consent checks and recording Stop behavior were not changed.

SwiftUI typecheck uses the macOS 13 deployment target. The canonical app is rebuilt
in place; Screen Recording and Calendar permission caches are reset by the local
build workflow as requested. No provider requests or audio playback are part of
the UI checks.

Validation: production build and bundle audit passed, along with all five
packaging checks. Actual Preferences shows the four category controls and the
Recording content; the Cloud processing section is absent. No saved preference
values were changed by the verification actions.
