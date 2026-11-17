# F3 slices 1–2: rebuilt-app check

Date: 17 November 2026. Branch: `feature/f3-meeting-templates`.
This validates the implemented generation flow, not complete F3 acceptance.
The native template picker, preview and editor remain pending.

## Builds and automated checks

- Rebuilt production `dist/macos/ScribeLoop.app` and the separate synthetic
  `build/macos/ScribeLoop Preview.app` from the current working tree.
- Preserved newer local rolling-failure handling edits. Their focused Python
  checks passed: 71 tests. These edits were not overwritten by the UI test.
- Native desktop audit regressions passed (ledger, Stop, history and transcript
  navigation). Production bundle verification passed: 40 native binaries, no
  external dependencies, bundled recordings or operator transcription credential.
- Builds use local ad-hoc signing; release signing/notarization is not claimed.

## In-app observations

The preview uses a disposable encrypted workspace containing a synthetic
230-segment conversation. Its test-only Planning choice is attached in the fixture;
no unimplemented template picker was used. A deterministic request hook requires
that the actual section/reduction payload contains that Planning template.
Although the provider label is LM Studio, no real LM Studio or cloud request runs.

1. Opened the Meeting menu and generated notes. The seeded legacy summary changed
   to the generated recording-priorities summary and desktop-workflow key point.
2. Followed Source 3 to a highlighted transcript passage at 6:04, then returned to
   Notes and confirmed the generated notes remained available after the page fetch.
3. Invoked Generate again without an error. Distinct version/cache identities are
   covered by the API regressions; the current UI does not display generation IDs.
4. An export attempt exposed the synchronous `NSSavePanel.runModal()` call inside
   the WebSocket reader. The preview disconnected during this interaction.
5. Changed export to an asynchronous sheet, activated the app window and guarded
   against duplicate panels. Rebuilt both apps and repeated generation/export.
   The save sheet appeared, a temporary synthetic Markdown file was saved, and
   the preview remained connected with its controls available afterward.
6. Read the saved Markdown: 230 source anchors, every summary link resolves, the
   generated summary/key point are present, and the final 7:38 passage is included.
   Markdown punctuation is escaped intentionally. Export formatting is still the
   existing canonical layout; saved-template presentation belongs to slice 4.
7. Opened the production encrypted workspace after local Keychain approval.
   All eight saved conversations loaded. Existing Vietnamese notes and their
   3:12 citation worked. The final rebuilt production process reopened successfully.
   No production notes were regenerated, recordings captured or real provider
   requests made during this check.

Synthetic export: `/private/tmp/scribeloop-f3-ui-export-20261117-5c79e4ad.md`.
Machine-readable artifact (ignored): `build/macos/f3-ui-check-2026-11-17.json`.
This is workflow verification with synthetic output, not model-quality evidence,
live API validation or F3 closeout.
