# PR integration review — 16 November 2026

Scope: the Ask/Search/summary/changelog stack (#19–24) and the Ubuntu preview
branch (#25, originally `54d7f8d`, by ngocquy2610). Review and integration used an
isolated checkout; the owner's three uncommitted working-tree edits were not
included or modified.

## Findings corrected before merge

- The Linux change returned early from shared `DesktopSettings.load`, bypassing
  legacy embedding-key/model migration on both platforms. Move platform detection
  defaults to the final validation after every migration. Added regressions for
  both Linux's opt-in default and macOS's existing default; saved choices retain
  precedence.
- The Linux toggle claimed to suggest meetings, although candidate capture and
  user-facing suggestions remain unavailable. Label it metadata monitoring and
  explain manual recording/app-isolation limits.
- The architecture edits overlapped the newer Ask/Search/storage documentation.
  Preserve the current schema-23 architecture and add a separate Ubuntu runtime
  flow linking the junior's detailed release plan.

## Reviewed behavior

- Linux app discovery parses known desktop-entry/audio-stream metadata without
  launching desktop commands, recording PCM or inspecting browser content.
- Application capture is explicitly disabled for Linux; manual system-output
  recording discloses that it includes all selected-output sound.
- Playback uses unique private directories/files and cleans them up across normal
  playback, replacement/error, selection changes and shutdown. Runtime-directory
  ownership/mode/symlink checks remain enforced.
- Settings retain provider credentials when blank fields are left unchanged,
  acknowledge saves/errors, and use Secret Service on Linux. No environment-key
  import or permission expansion is introduced.
- The Ask stack keeps guarded uploads, bounded evidence, isolated vector shapes,
  migration and deletion integrity, and explicit Hybrid configuration. Saved-note
  display-name changes remain separate from transcript-content freshness.
- Workflow changes were not introduced by the junior's PR. Its previous runs were
  `action_required` after Copilot-authored commits; those are not passing checks.

## Local validation

- Combined Python suite: **937 passed, 1 skipped**. The skipped native GTK history
  test needs GTK/display support unavailable on this macOS checkout.
- Focused Linux/settings suite: **76 passed, 1 skipped**.
- Ruff over `core`, `scripts`, `tests`: passed.
- Frontend replay groups: passed.
- Whitespace/conflict-marker and architecture-link checks: passed.

Hosted Linux package/installed GTK checks must validate the updated PR head before
merging #25. Existing passing checks for #19–24 were inspected individually; merge
those in dependency order and retarget each child to main after its parent lands.
Real Linux call capture and full desktop parity remain preview acceptance work,
not a claim of this code review. No private workspace, live provider request or
physical microphone/playback was used during this review.
