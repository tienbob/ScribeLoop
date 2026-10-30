# macOS desktop plan audit — 2026-10-26

Scope: review the proposed desktop plan against the audited merged core and Apple
platform/distribution constraints. This is a documentation audit; no app, backend,
permissions, credentials or data have been changed.

## Findings and resolutions

| Priority | Finding | Resolution in the revised plan |
| --- | --- | --- |
| P1 | Copying an encrypted DB did not specify WAL handling or shared audio-key identity. `database_account()` hashes the DB path; audio keys use global session UUIDs. Copied active stores could lose committed data or invalidate each other's audio. | MVP adopts the store in place. Relocation is a separate gated migration with WAL-consistent copying, journaling, collision/rollback checks and an explicit shared-key/coexistence design. |
| P1 | M0 promised production lifecycle/clean-machine behavior that M1 and signing had not yet delivered; permissions were implied by generated/unsigned tests. | Isolated, unquarantined fixture spike first. Distinguish minimal bootstrap from production host contracts; do not claim TCC/Gatekeeper proof. Repeat the small artifact with hardened signing when prerequisites exist, before release. |
| P1 | Provider setup and native UI relied on settings/paging that were scheduled too late. Current provider objects read `.env`/environment once; existing actions forbid arbitrary envelope fields. | Move settings injection, versioned envelopes/acknowledgments, ambiguity handling and paged/revisioned reads into M1 before real call workflow/UI. Pin settings per workflow; no implicit credential import or paid connection test. |
| P1 | Quit/crash language guaranteed finalization; pipe draining, parent liveness, safe process-group ownership and forced interruption were underspecified. | Explicit app-core/helper ownership, EOF reuse, bounded log drains, graceful-versus-forced outcomes, no orphan capture and no mutation replay after restart. Forced termination may leave a recoverable prefix, never a guaranteed footer. |
| P2 | Reconnect/paging lacked sequence, revision and citation consistency; native restoration could persist plaintext inadvertently. | Stable cursors, revision-pinned reads, boot/event IDs, snapshot/subscription race tests, invalidation and bounded memory-only caches. No transcript/audio/key scene-restoration or defaults persistence. |
| P2 | “Pause detection” and login behavior were presented as existing behavior, but the backend has no pause command. Pausing while recording could disable the inactivity end monitor. | Add backend-owned detection state; MVP pause only while idle. Login is user-enabled menu-only monitoring, never recording, and handles OS-disabled/approval states. |
| P2 | macOS 14+, dependency/signing compatibility and URLSession loopback behavior were assumptions rather than tested support. | Provisional baseline; explicit native-library/rpath/deployment inspection, loopback/ATS proof, minimum-OS host/VM test and separate signed release gates. Never fix a packaging issue by globally weakening transport/library security. |

## Checked against the repository

- `storage/encrypted.py`: path-hashed database accounts, SQLCipher/WAL and exclusive lock.
- `storage/audio.py`: global per-session audio key accounts and adjacent recording tree.
- `llm/summary_provider.py`, `pipeline/deepgram_meeting.py`, `pipeline/model_selection.py`:
  environment/file configuration and immutable per-run cloud settings assumptions.
- `api/server.py`, `api/auth.py`: strict action models, token-first native WebSocket
  support, fixed/keyless demo CLI, full snapshots and absent desktop settings/pause APIs.
- `capture/native/macos_meeting/main.swift`: STOP command and parent-stdin EOF handling
  already exist; extend/reuse that contract rather than replace the capture pipeline.
- `meetings/service.py`: candidate freshness and discovery lifecycle; detection is a
  heuristic, not an official call-state integration.
- Baseline PR #5 is merged in `0003b87`; implementation is no longer waiting on that merge.

Apple's [notarization issue guidance](https://developer.apple.com/documentation/security/resolving-common-notarization-issues)
supports the explicit nested-signature/hardened-runtime gate and warns against
blanket library-validation exceptions. Check
[URLSession/ATS local networking](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking)
on the actual target rather than assuming a native WebSocket behaves like the web demo.

## Disposition

The revised plan is suitable to begin **M0 only**: a synthetic, isolated packaging
proof. M1–M6 are sequenced acceptance work, not already validated implementation.
Signing remains deferred under the user's existing instruction; safe relocation,
minimum-OS compatibility and release permissions remain explicit gates.

Validation: Markdown links and whitespace checked. Runtime tests were not rerun for
this docs-only change. See [the revised implementation plan](MACOS_APP_IMPLEMENTATION_PLAN.md).
