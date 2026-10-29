# ScribeLoop native desktop app

The full local development app is `dist/macos/ScribeLoop.app`. It runs the
meeting core as its own bundled child; no terminal server, browser,
local speech model, installed Python or Homebrew library is needed to **run** it.
Building still requires Xcode and the separate packaging environment.

## Launch and configure

```sh
open 'dist/macos/ScribeLoop.app'
```

1. Open Preferences using the gear button. Configure your summary provider and
   its key. OpenAI / `gpt-5.4-mini` is the default. Enter your own Deepgram
   transcription key in its separate section. Provider keys are saved in macOS
   Keychain; the app contains no operator key or included AI credits. Runtime never
   imports development `.env`. Leave a key field unchanged to retain the saved key.
   Press **Done** to save edits; the sheet closes after the save is confirmed.
   Saved summary keys show only their first four characters plus a fixed mask.
   Enter a new key to replace it; unchanged fields keep the saved key.
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
authenticated encrypted chunks. The workspace key uses the existing storage Keychain namespace; audio keys use workspace-encrypted envelopes.
Unarchived audio expires after 30 days while running/idle and the agreement is
accepted. Archive keeps audio until unarchived or deleted. Notes/transcripts remain.

Preferences does not expose workspace management. Existing configured workspaces
remain in use; this UI change does not relocate or decrypt data. The workspace
reference is persisted in UserDefaults; transcript/audio/provider keys are not.
Database schema downgrade is not supported.

## Build and validate

Prepare the isolated environment as described in [M0 build instructions](README.md),
then run (no provider credential is needed to build):

```sh
build/macos/venv13/bin/python packaging/macos/build_desktop.py
.venv/bin/python packaging/macos/verify_desktop.py
.venv/bin/pytest -q
node tests/frontend_replay.mjs
```

Build scripts select the installed Xcode per command and never change global
xcode-select. Dependencies are resolved in the separate cloud-only environment;
`build/macos/requirements-resolved.txt`, `desktop-manifest.json` and
`desktop-audit.json` describe the actual local build. The build is ad-hoc signed,
with an explicit arm64/macOS 13 deployment target. See the
[portable runtime/dependency setup](../../docs/INTERNAL_MAC_DISTRIBUTION.md#build-and-inspect)
before building. `verify_desktop.py` scans native
libraries/rpaths, signatures and prohibited bundled recordings/ML weights.

The separate preview app exercises the **same** SwiftUI client and production
protocol using generated encrypted data, fake Keychain and disabled external
network/capture. It never opens your real workspace:

```sh
build/macos/venv13/bin/python packaging/macos/build_preview.py
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


### Customer-managed transcription

Version 0.1.1 removes `Contents/Resources/transcription.json` and all build-time
provider-key loading. Each user enters their own Deepgram key in Preferences.
The key is stored in macOS Keychain, excluded from public settings/diagnostics,
and can be replaced or removed. Leaving the field unchanged preserves it.
Saving requires idle processing and makes no API request. The new key applies to
subsequent recording, resume and explicit transcription reruns.

An upgrade does not import the old bundle's shared operator key. Existing notes,
recordings and summary-provider settings remain available; transcription needs a
customer key before new work. Valid older customer-owned `deepgram_key` settings
are retained. Fully self-hosted ASR remains future work; Ollama and LM Studio notes
endpoints are already configurable.

See [internal Mac distribution](../../docs/INTERNAL_MAC_DISTRIBUTION.md) for the
DMG, signing/notarization commands and tester instructions.

### Audio player and naming transcript people

The Audio tab has a full-recording timeline with elapsed/total time, play/pause,
drag-to-seek, 15-second skips and 0.5×–2× speed. Conversation mixes available tracks;
Microphone and Meeting audio can also be selected individually. The backend fully
authenticates each encrypted track once to build sparse, memory-only seek checkpoints.
Playback then reauthenticates requested records and checks file identity/change
metadata, fetching at most a 60-second window at a time. The native player keeps
only its current and next windows, prefetches near the boundary and advances through
the full recording. Each request rechecks audio availability/keys. Stale responses
from old seeks/tracks/meetings are ignored. Closing the Audio tab or disconnecting
releases playback buffers; expired/deleted audio stops playback. No plaintext audio
file or whole-recording PCM cache is created. The first open may show Loading while
long tracks are indexed.

Choose **Name people** beside the meeting title, or **Name this person** on a
transcript row. Select Person 1 / Person 2 / Person 3, inspect the sample utterance,
then type a name and Save. Alternatively select an existing saved person. The
roster spans the complete current transcript, not only the visible page, and its
numbers match transcript labels. Creation and linking form one transaction; a stale
revision or failed notes update cannot leave an orphan person. Manual naming does
not enable voice learning. Merged speakers retain their original target number.

Validation: 426 Python tests pass, including complete-track authentication,
random-access boundaries, mixed tracks, changed-file invalidation, revoked keys,
meeting-wide numbering, merges and atomic naming rollback. Preview native smoke
also checks full-duration metadata, 90-second seeking, person naming and generated
silent playback through multiple windows, pause/resume, speed, superseded seeks,
track changes and cleanup. Native UI inspection verified timeline seeking/play/pause,
speed choices and selecting Person 3 → entering a name → saving to the transcript.

### Avoiding chat-notification prompts

Native discovery tracks microphone starts rather than waiting a fixed six seconds.
A known input off→on transition needs two successive duplex observations; startup
or recovery activity and browsers need three. Output-only notifications and output
starting on an already-held microphone do not establish new calls. This remains an
audio-process heuristic; use New recording for receive-only calls or missed starts.
Existing call IDs survive muting and short silence. The independent call-end monitor
continues using any-direction activity.

Not now and the close button suppress the current episode. After fifteen seconds of
healthy inactivity in both directions, a new observed microphone start can prompt
again immediately after confirmation. Uncertain reappearances remain suppressed
for five minutes. Registry/helper changes preserve dismissals, and the native client
remembers shown candidate IDs rather than just the last one.

### Browsing without audio-key prompts

History and record details inspect retention metadata and encrypted-file presence,
without fetching audio keys. The Audio tab loads and authenticates audio only after
Play is pressed; the duration and seek bar populate then. A listed recording may
still fail playback if its key is missing or access is denied. Metadata visibility
never grants decryption access. Playback/rerun and deletion still use the native
keystore. Ad-hoc rebuilds can separately require approval for database/provider
credentials at startup; stable release signing remains deferred.

### Retranscribing saved audio

Transcribe again uses Deepgram's prerecorded HTTP endpoint: one whole-conversation
WAV upload, with the recorded sources as separate channels and speaker separation
enabled. The upload is generated in bounded memory without plaintext disk staging
or realtime replay. Live calls and interrupted-live resume retain their streaming
path. Existing transcript revision stays selected until the batch result succeeds;
failures do not silently retry. The UI reports saved processing/upload/wait states,
and retained audio remains playable during a saved rerun. Capture and audio recovery
continue to exclude playback because they can modify the underlying files.

### Visual design and icon

The notebook keeps its sidebar and Notes / Transcript / Audio layout. Its native
palette follows macOS appearance: warm neutral light surfaces, charcoal-green dark
surfaces, a green accent, and explicit status badges. Notes use a single overview
card and compact source buttons; transcript labels distinguish people from audio
sources. Playback still loads only on Play. Recording review shows provider/model
information without an unnecessary endpoint URL.

The bundle's speech-loop/waveform icon is packaged as `ScribeLoop.icns`; editable
SVG, PNG and regeneration instructions live in `shell/macos/Assets/`. No provider
calls are needed to build or inspect the UI.

### Simplified Preferences

Done saves changed notes-provider, model, key, language and duration settings, then
closes after the core accepts the save. An unchanged Done closes without writing to
Keychain. Failed or uncertain saves keep the form and its edits visible. There is
no separate Save button, key-removal switch or workspace-management section.
Cancel, Escape, or clicking outside the Preferences sheet discards unsaved edits.
Call-detection and login switches are staged until Done too. Cancellation is disabled
while a save is being confirmed; OS permission buttons act immediately.

The saved-key placeholder shows four leading characters followed by a fixed mask
(short keys remain entirely masked). This is display metadata, never a replacement
credential. A blank replacement field keeps the existing key for the same endpoint.
Microphone and meeting-audio permissions have separate trailing actions; automatic
call detection uses a switch. Permission grants still require the operating system.

### Rename or delete a conversation

Use the visible pencil and trash buttons on each sidebar conversation card, or
right-click a conversation for **Rename conversation…** and
**Delete conversation…**. Both actions are also in the selected record's Meeting
menu. Renaming saves a custom title without transcribing or generating notes; later
summaries keep that title. Empty, multiline and overlong names are rejected.

Delete asks for confirmation and names the target conversation. Confirmation uses
the existing key-first permanent deletion flow for its audio, transcript and notes.
Deleting the selected record moves selection to an adjacent record. Cancel leaves
all saved data intact. Actions are unavailable while recording/processing is active.

On macOS, conversation deletion checks Keychain item identity without reading the
audio secret, removes the key, verifies absence, then removes encrypted files.
Deleting does not need to decrypt the recording. A locked Keychain or changed
unsigned development identity can still require macOS authorization.

Preferences → **Stop cloud processing…** revokes the first-use agreement and stops
recording/pending processing after confirmation. Saved conversations remain; unsaved
settings drafts are discarded. Already uploaded data cannot be recalled. This action
is immediate and separate from Done.

The preview builder now creates its fixture bundle directly, without rebuilding the
production app or loading its operator key. It writes a separate preview manifest.

### One workspace unlock

The database key is retrieved from Keychain once when the core opens the workspace.
A separate wrapping key is derived with HKDF-SHA256; each recording still uses a
random 256-bit key. Those recording keys are stored as authenticated AES-256-GCM
envelopes in `<database filename>.audio-keys/` beside the database, not as separate
new Keychain items. Opening another recording or restarting the core after the
workspace unlock needs no individual key authorization for migrated/new recordings.
Raw keys are never written to disk. Include the envelope directory in encrypted
workspace backups; copying the database alone does not preserve audio access.

Existing recordings migrate on their first explicit audio access. macOS may ask
once for each legacy recording key; successful migration persists and verifies an
envelope without rewriting audio/transcripts or making provider requests. Failed
migration preserves the original key and recording. Legacy Keychain originals stay
until authorized recording deletion; deletion removes both copies before audio.
The process cache is evicted on deletion and cleared on shutdown. Backups and
forensic recovery of deleted envelope files are outside the deletion guarantee.

The first-use agreement is separate from Keychain authorization. Startup can still
need OS approval for workspace/provider credentials, especially after ad-hoc builds;
stable release signing remains open. This change removes separate recording-key
prompts after migration, rather than bypassing OS authorization.

### Summary instructions

Preferences → Conversation notes → **Summary instructions** accepts up to 2,000
characters of optional guidance, such as “Prioritize decisions, commercial risks,
owners and explicit deadlines.” Done saves it securely with the provider settings;
Cancel/Escape/outside dismissal discards edits. Blank uses built-in guidance.
Instructions apply to new section notes and final summaries, and changing them
invalidates matching summary-job cache entries for the next generation. Existing
saved notes are unchanged until Generate summary is requested. Required citations,
JSON structure and factual-grounding rules remain enforced.

The development API can also read `SUMMARY_INSTRUCTIONS` from its local configuration;
the bundled desktop runtime uses saved Settings and does not read checkout `.env`.

A reproducible fictional business demonstration can be generated with local macOS
voices, then sent once to the configured cloud providers:

```sh
.venv/bin/python scripts/demo_business_meeting.py --allow-synthetic-upload \
  --output data/demos/business-pilot-new
```

It writes audio, the original script, raw transcript, cited notes, measurements and
an HTML report in an isolated workspace; it never opens the user's meeting DB or
changes saved provider/settings. It uses SciPy and macOS voices in the development
environment, not the shipped app. See `data/demos/business-pilot-20261027/demo.html`
for the completed 4:16 example and `REVIEW.md` for its observed name/wording errors.

When no custom instructions are saved, the editor displays the default editorial
guidance as editable text. Existing custom instructions take precedence. Opening
Preferences and pressing unchanged Done does not save or touch Keychain.

### Microphone controls

Preferences → **Microphone** adds input sensitivity (-12 to +24 dB), Off/Standard/Strong
background filtering, an adjustable noise rejection threshold and Reset. Defaults
are 0 dB, Standard and -40 dBFS. Changes apply to new recordings and their ASR; an
encrypted original microphone track is retained when processing is enabled.
See [behavior, limits and latest-recording analysis](../../docs/MICROPHONE_SETTINGS.md).

### Streaming performance and status

The sender reads bounded audio durations independently of native packet size, and
provider writes have a cancellation-aware five-second deadline. The main window
shows local catch-up versus pending confirmed text during streaming. Note/page
refreshes reuse change-aware caches; source edits still invalidate cited notes.
Schema 12 adds source-change tracking without rewriting recordings/transcripts.
See the [fixes and re-audit](../../docs/STREAMING_PERFORMANCE_CLOSEOUT_2026-10-28.md)
for measured improvements and cold-cache/real-network limitations.

### Memory cache

The core uses a shared **256 MiB** LRU cache for recent authenticated audio blocks,
indexes, transcript pages, speaker rosters and validated notes/hash data. It fills
on demand rather than reserving the budget at startup. File/source changes,
deletion, forget-person and shutdown invalidate appropriate entries; stored audio
stays encrypted. See [cache behavior and silent measurements](../../docs/MEMORY_CACHE_256MB.md).
