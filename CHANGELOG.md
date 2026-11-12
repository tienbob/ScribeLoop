# Changelog

## Latest local preview — 0.1.50 (51)

**Compared with the shared macOS beta 0.1.45 (46). Prepared 12 November 2026.**

These notes describe the local app through code commit `4337f2c`. The Google Drive
installer is still 0.1.45 (46); this changelog does not replace or publish it.
Several development updates share the 0.1.50 version number, so use the commit
reference to identify the exact feature set.

### New

- **Search your conversations.** Find passages in saved titles, transcripts and
  current notes. Narrow results by meeting, date or person and open the matching
  source directly.
- **Find ideas across wording and languages.** Optional Hybrid Search combines
  keywords and semantic embeddings. Choose Gemini, OpenAI, bundled local Granite,
  Ollama, LM Studio or a compatible custom service. Gemini is the default embedding
  choice; Keyword Search remains the default search mode.
- **Ask your meetings.** Get answers grounded in retrieved excerpts, with source
  links. Use Ask this meeting inside a conversation or Ask across meetings in the
  sidebar. Ask requires configured Hybrid Search and a notes provider.
- **Chat naturally.** Follow up in a message timeline, use starter questions and
  multiline input, and reopen complete saved chats from History. The compact
  context bar shows the included meetings; Change opens a searchable picker.
- **Manage chat history.** Delete individual chats or clear all history with
  confirmation. Meeting recordings, transcripts and notes are preserved.
- **Choose AssemblyAI transcription.** AssemblyAI joins Deepgram as a cloud option
  for live speaker-labelled transcription and saved-audio batch reruns, with its
  own saved API key. Deepgram remains the default.
- **Guided first setup.** A resumable setup checklist explains transcription,
  optional notes, microphone access and meeting-app/browser audio permissions.
  Transcript-only setup is available when automatic notes are disabled.

### Improved

- **Search as you type.** Results update 0.5 seconds after your last query or
  filter edit. The Search button is removed. Pending edits are coalesced behind
  one in-flight request, and obsolete responses cannot replace newer results.
- **Clearer meeting context.** Changing a chat's meeting selection starts a new
  chat and preserves the previous saved thread. Cancelling the picker leaves the
  current chat intact. All-meeting chats keep the meeting set from their first
  question rather than silently including meetings added later.
- **Cleaner answers.** Removed Answer details. Sources, uncertainty, changed-source
  warnings and evidence-limit notices remain available.
- **Better retrieval and citations.** Matching excerpts from the end of long
  segments reach the answer model. Meeting/date/person filters apply before
  semantic selection. Source links navigate to the cited transcript passage.
- **More reliable Calendar state.** Connection controls reflect actual access.
  Existing grants are reused, enabled/calendar-selection preferences survive
  relaunch, and returning to the app refreshes authorization state.
- **Less permission confusion.** Already-allowed capture controls are disabled,
  while microphone and meeting-app/browser audio access remain separate. Pausing
  call detection now persists across relaunch.
- **Clearer embedding-key feedback.** Settings shows a saved checkmark without
  extra redacted-key or Keychain explanation lines.
- **Updated documentation.** A clearer README and current architecture guide
  explain setup, provider choices, privacy and the major system flows.

### Fixed

- Renaming a person or speaker no longer marks current saved summaries outdated.
  Transcript changes and speaker merges still invalidate notes. Generated note
  wording is preserved; names inside prose are not silently rewritten.
- Fixed Ask failures involving valid cited responses and retrieval caveats.
- Fixed clipped evidence appearing outdated immediately after saving and reopened
  history losing its matching excerpt.
- Fixed artificial request-size failures for Vietnamese/Unicode questions by
  accounting for actual UTF-8 provider payloads.
- Revoking the processing agreement stops pending Search/Ask jobs from initiating
  subsequent embedding or answer uploads. Reaccepting does not revive old jobs;
  data already sent cannot be recalled.
- Fixed incomplete processing metrics: stored usage includes embedding/indexing
  work as well as answer generation. Ask hides these details in its simplified UI.
- Deleted chats cannot be restored by stale history responses or recreated by
  an in-flight follow-up. Deletion waits for registered Ask jobs to finish.

### Upgrade notes

- Apple Silicon and macOS 13+ remain the targets; automatic call detection still
  requires macOS 14.2+. Builds remain internal, ad-hoc signed and not notarized.
- The workspace schema advances from **15 to 23**. Quit the app fully before
  replacing it and preserve a recoverable backup before testing an upgrade.
  Database downgrades are unsupported.
- Existing fresh legacy summaries are preserved during name edits. Legacy notes
  that were already outdated may need one regeneration to establish the new
  freshness baseline.
- Keyword Search stays local. Enabling cloud Hybrid Search permits automatic
  embedding requests after typing pauses; cloud Ask sends retrieved text to the
  notes provider. Your provider account's charges and policies apply.
- The app is larger because it now bundles the optional local embedding model
  and its runtime. This is not a measured increase in recording-time RAM use.
- Existing playback, speaker naming, encrypted storage, Markdown/email sharing,
  30-day audio retention/archive, and optional Ollama paths remain available.
  There is no new cloud sync or automatic provider fallback.

### Validation

The latest implementation passes **914 automated Python tests** and macOS bundle
verification. Native regression checks cover Search serialization and history
deletion. Full visual acceptance of the latest context-picker flow is still open;
the desktop-control connection was unavailable during that walkthrough.

See the [artifact comparison and evidence](docs/MACOS_DRIVE_COMPARISON_2026-11-12.md)
for the baseline, package sizes, checksums and remaining validation limits.
