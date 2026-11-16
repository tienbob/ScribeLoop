# ScribeLoop Ubuntu Desktop Release Plan

**Status:** implementation roadmap from the existing Ubuntu preview to a supported Ubuntu desktop release. This document describes planned work; it does not claim release acceptance.

**Repository baseline:** `ARCHITECTURE.md` is the product and system architecture reference. Its current implementation notes describe macOS as the primary desktop app and Linux as a GTK preview. The Linux preview is substantial and already shares the Python core, encrypted storage, protocol, and meeting workflows. This plan is therefore a hardening and product-scope plan, not a port-from-zero proposal.

## 1. Release Goal

Ship an installable, maintainable Ubuntu desktop application that:

- Uses the existing shared Python core and a native GTK shell.
- Records only after explicit user action and consent, with a visible recording state.
- Keeps workspace data encrypted and credentials in the desktop Secret Service.
- Captures microphone and selected system-output audio reliably on supported hosts.
- Installs, upgrades, launches, and uninstalls predictably without silently deleting user data.
- Has an explicit Ubuntu, architecture, and desktop-session support matrix.
- Makes intentional Linux feature boundaries clear instead of implying exact macOS parity.

The first release can remain narrower than macOS. Per-application capture, automatic call detection, Calendar, and email handoff are product decisions and should not block an Ubuntu release unless they are declared release requirements.

## 2. Threat Model and Data-at-Rest Inventory

### Security claims and limits

Application encryption protects encrypted workspace files and audio if copied while their keys are unavailable. It is not a substitute for full-disk encryption (FDE). It does not protect an active logged-in session from malware running as the same user, root, a compromised kernel, or an attacker who can use an unlocked Secret Service. Provider requests intentionally disclose audio or transcript text to the configured provider according to the visible agreement and action. The release must say this plainly; users handling sensitive meetings should enable FDE and secure their account/session.

Protect against: another unprivileged local account reading ScribeLoop's private workspace or transient files; offline access to copied SQLCipher/SLPA files when their keys are unavailable; accidental credential disclosure through logs, UI snapshots, or package contents.

Do not claim protection against: same-user malware while the desktop/keyring is unlocked; root/kernel/physical attacks on a running or unlocked system; data already sent to a configured provider; user-created plaintext exports; or forensic recovery from storage/snapshots outside the app's encrypted-file and deletion controls.

### Data inventory to audit

| Surface                       | Current design/evidence                                                                                                                              | Release requirement                                                                                                                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Live capture and queues       | Capture queue is bounded; recording writes authenticated encrypted SLPA records. The storage writer documents that plaintext is not written to disk. | Trace every capture/transcode/error path for temporary PCM/WAV spill; inject failure and disk-full conditions; prove plaintext is not staged.                                                    |
| Playback                      | Fixed shared `/tmp` staging was replaced in this branch with a unique file inside a private per-playback directory; cleanup runs on replacement, selection change, EOS/error, and window close. | Playback-specific regression tests cover mode, uniqueness, symlink rejection, and lifecycle cleanup; publish the preview update after full package validation. |
| Database, WAL, search         | SQLCipher workspace; `secure_delete=ON`; WAL mode; purge truncates the WAL. FTS5 search table is in the encrypted database.                          | Verify every deletion path removes rows/index entries, checkpoints/truncates WAL, and leaves no plaintext sidecar/index/cache. Test database/WAL bytes with distinctive fixtures.                |
| Process memory and crash data | Transcript/audio keys and decrypted windows necessarily exist in process memory. Ubuntu 26.04 documents crash-dump support enabled by default.       | Define core-dump, Apport/Whoopsie, and diagnostics policy; ensure transcript/audio/key payloads are not collected by default. Explain RAM/core dumps are outside application-at-rest encryption. |
| Swap and hibernation          | OS-controlled; application encryption does not cover plaintext in process memory.                                                                    | Recommend FDE covering swap/hibernation; do not promise secure erasure of memory pages.                                                                                                          |
| Logs and diagnostics          | Backend suppresses most native/provider details and avoids access logs.                                                                              | Test exceptions and debug paths for secrets, transcript text, audio bytes, local paths, and provider response bodies; document opt-in diagnostic collection.                                     |
| Export and GTK recent files   | Markdown export is intentionally plaintext at a user-selected path; file chooser may retain recent-path metadata.                                    | Show plaintext warning before export, never claim deletion covers exports, and assess/limit recent-file metadata exposure.                                                                       |
| Provider egress               | Cloud ASR sends audio; cloud notes/cleanup sends text to configured services.                                                                        | Disclose destination and payload before first use/action; test HTTP(S) proxy support and system CA trust, including corporate roots.                                                             |

### Deletion semantics

“Delete conversation” means the application removes workspace records, search-index rows, wrapped/session keys, and encrypted audio files under its control, then applies secure-delete/WAL checkpoint behavior. It cannot promise removal from exports, backups, filesystem snapshots, crash dumps, swap/hibernation, provider systems, or copies outside the workspace. Test and document this boundary. Deleting a key does not crypto-shred plaintext or artifacts that were never encrypted.

## 3. Existing Implementation

| Area                 | Current state                                                                                                                                                                          | Main source                                                                                              |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Desktop shell        | GTK 3 notebook with conversations, Notes, Transcript, Audio, settings, recording dialog, and Markdown save dialog                                                                      | `core/scribeloop_core/api/linux_native.py`                                                               |
| Owned backend        | GTK host starts and stops an authenticated, loopback-only core process; data is stored under the XDG user data directory                                                               | `core/scribeloop_core/api/linux.py`                                                                      |
| Audio capture        | GStreamer pulls microphone and default output-monitor sources through `pulsesrc`; works with PulseAudio and PipeWire's PulseAudio compatibility server. Application scope is rejected. | `core/scribeloop_core/capture/linux_pipewire.py`                                                         |
| Shared behavior      | Existing core owns recording state, provider integration, encrypted storage, summaries, retention, and protocol handling                                                               | `core/scribeloop_core/`                                                                                  |
| Packaging            | Ubuntu-specific `.deb` vendors Python wheels while using Ubuntu GTK/GStreamer/PyGObject packages                                                                                       | `packaging/linux/build_deb.py`, `packaging/linux/Dockerfile`                                             |
| Automated validation | CI builds Ubuntu 22.04 and 24.04 packages for amd64 and arm64 and runs installed-package checks in disposable desktop/audio services                                                   | `.github/workflows/linux-desktop.yml`, `packaging/linux/check_installed.sh`, `packaging/linux/verify.py` |

The preview documentation records 22.04/24.04 builds and generated-audio smoke checks. It also explicitly leaves physical capture, alignment, Wayland, long recordings, device changes, and suspend/resume for host acceptance. See `packaging/linux/README.md` and `docs/LINUX_UBUNTU_PREVIEW.md`.

## 4. Decisions Before Release Work

Resolve these in a short release-scope milestone and record the answers in the release checklist.

1. **Audience and support:** current date is 2026-11-10. Ubuntu 26.04 LTS shipped 2026-04-23. Its release notes say support through April 2031, while Canonical's lifecycle tracker currently lists standard maintenance through May 2031; use the lifecycle tracker for the final support statement and re-check before release. The tracker lists Ubuntu 22.04 standard maintenance through May 2027 and 24.04 through May 2029. Treat 26.04 as the primary GA target, 24.04 as a bridge if its matrix passes, and 22.04 as preview/maintenance-only for a new GA launch unless customer demand justifies its short remaining standard-support runway. Re-check Canonical's lifecycle page at release time.
2. **Desktop sessions:** Wayland is the primary target. Ubuntu 25.10 removed the GNOME-on-Xorg session; Ubuntu 26.04 Desktop is Wayland-only (XWayland remains for X11 applications). X11 is secondary compatibility coverage only on releases that still offer a supported Xorg session. GNOME 50 is the current upstream desktop shipped by Ubuntu 26.04. Validate portals, audio routing, keyring activation, file dialogs, and lifecycle on actual hosts.
3. **Feature scope:** which macOS capabilities are required on Linux? Publish explicit supported/deferred lists for call discovery, app-scoped capture, transcript cleanup, people/speaker editing, meeting rename/delete/recovery, Calendar, and email.
4. **Distribution:** evaluate a signed first-party APT repository or Launchpad PPA as the primary update channel; release-specific standalone `.deb` downloads may remain a fallback. Use a dedicated keyring with `signed-by=` in the source entry, never deprecated `apt-key`. Any Flatpak/Snap/AppImage proposal needs separate portal, keyring, audio-permission, and update-policy evaluation.
5. **Updates and support:** define release signing, repository publication, update cadence, rollback, support contact, and incident response. CI artifact upload is not a user update channel.
6. **Version source:** one manifest must set core, Debian package, macOS metadata, docs, and release artifact versions. The current checked-in values are re-verified in Source Review Notes; CI must reject drift.
7. **Recovery and keyring:** require a user-held recovery mechanism for GA (recovery key/passphrase-wrapped workspace key or encrypted backup/export) and define GNOME Keyring, auto-login/locked keyring, blank-password keyring, KDE Wallet, and unsupported headless behavior before choosing exact UX.

## 5. Work Plan

### Priority and sequencing

| Priority  | Work                                                                                                                    | Suggested owner                             | Estimate                                       | Dependency                                                            |
| --------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------- |
| Immediate | Fix decrypted playback staging and publish a preview update independently of release-scope decisions                    | Linux shell maintainer                      | 1-2 engineering days                           | None; do before any further preview distribution                      |
| P0        | Threat/data review, crypto design review, recovery mechanism, local-backend boundary tests, and keyring/data continuity | Security + core/storage + Linux maintainers | 1-2 engineering weeks                          | Immediate playback fix; recovery design before implementing migration |
| P1        | Support matrix/version manifest, UX scope, required notebook actions, accessibility, and Wayland host validation        | Product + Linux shell + QA                  | 2-4 engineering weeks plus host scheduling     | P0 design decisions; 26.04 build environment                          |
| P1        | Signed APT channel, dependency provenance/CVE process, clean install/upgrade/recovery acceptance                        | Release engineering + security              | 1-2 engineering weeks                          | Version manifest and package matrix                                   |
| P2        | GTK 4/libadwaita migration assessment/prototype and deferred parity features                                            | Linux shell + product                       | 1-3 engineering weeks for assessment/prototype | P1 release scope; may be post-GA                                      |

Estimates are initial engineering effort, not calendar commitments; physical host testing and review availability add elapsed time. Release-blocking P0 work is listed once here; milestone sections below define implementation and test details.

### Immediate: Preview Playback Fix

This is independent of GA scope and must ship as a preview update before additional preview builds are shared. The implementation now uses an exclusive 0600 WAV inside a unique 0700 directory under a validated `XDG_RUNTIME_DIR` (or the system temporary directory when unset), with cleanup after EOS, replacement, session change, playback failure, and window close. It rejects unsafe or symlinked runtime directories and does not remove arbitrary paths. The old fixed `/tmp/scribeloop-native-playback.wav` path is no longer used.

**Status:** implementation and focused regression tests are present in the current branch. The preview release gate remains open until the complete Linux test suite and package/installed smoke checks pass and a preview package is rebuilt.

**Exit:** tests prove mode 0600-equivalent access, symlink resistance, collision resistance, and cleanup on normal/error lifecycle paths. Publish a preview update with a concise privacy note.

### Consolidated P0 Release Blockers

- Complete a cryptographic design review before GA: key generation/derivation (KDF/HKDF roles), AES-GCM nonce uniqueness and authenticated framing, key wrapping/recovery, key rotation, deletion semantics, and schema/key migration. Record reviewer, findings, mitigations, and regression tests.
- Define and test the threat model/data inventory in §2, including capture spill, SQLCipher/WAL/FTS, memory/core dumps, swap/hibernation, logs, exports/recent-file metadata, provider egress, and deletion boundaries.
- Require a user-held workspace recovery mechanism before GA. Support a versioned recovery key or passphrase-wrapped workspace key and/or encrypted backup/export; test restore on a fresh machine and keyring. Clearly warn that loss of both keyring key and recovery material means unrecoverable data loss. No silent empty-workspace fallback.
- Verify local backend security: bootstrap token remains off argv/environment and travels only over the private control pipe; Host/Origin checks reject rebinding/foreign origins; EOF or parent death stops the backend. Current code already uses stdin and Host/Origin validation and listens for control-pipe EOF; preserve these controls with regression tests. Evaluate a 0600 Unix-domain socket as a hardening option, not an assumed requirement.
- Test package upgrades and purge semantics. Maintainer scripts must never inspect or modify `$HOME`; package `remove` and `purge` leave user data and keyring untouched. Conversation deletion is only an explicit in-app operation with confirmed, documented scope.
- Resolve Secret Service behavior for locked/auto-login keyrings, blank-password keyrings, GNOME Keyring availability, KDE Wallet/non-GNOME sessions, and headless/SSH. Either implement supported adapters with explicit UX or clearly reject unsupported environments without losing data.

### P1: Freeze Scope and Support Matrix

**Deliverables**

- A versioned support statement listing Ubuntu release, CPU architecture, Python ABI, desktop environment/session, and audio server combinations. Target Ubuntu 26.04 LTS (GNOME 50, Wayland-only Ubuntu Desktop) for GA; include 24.04 only if validated; treat 22.04 as preview/maintenance-only for a new GA launch unless product leadership accepts its remaining support runway.
- Validate Ubuntu 26.04's Python 3.14 ABI, including sqlcipher3, cryptography, and all other binary wheels. Current documented CI evidence covers 22.04/Python 3.10 and 24.04/Python 3.12.
- A Linux feature matrix marking each macOS workflow as supported, deferred, or not applicable.
- A signed APT repository/PPA decision and a single version manifest enforced by CI.
- A GTK 4/libadwaita migration assessment. GTK 3 remains usable for the preview but is a strategic maintenance and platform-integration risk as current GNOME applications move to GTK 4/libadwaita. Confirm upstream support policy before setting a migration deadline.
- Updated user-facing language that calls the product a preview until the gates below pass.

**Exit checks**

- The builder rejects unsupported Ubuntu releases and architectures rather than accepting any version at or above 22.04. Add 26.04 to CI; retain 24.04 only if its Python/runtime tuple passes; decide whether 22.04 receives preview-only maintenance.
- Ubuntu 26.04 Python 3.14 package build and installed smoke pass on every claimed architecture; do not infer ABI support from the 22.04/24.04 matrix.
- Wayland on Ubuntu Desktop 26.04 is the required primary acceptance target. Test X11 only as secondary compatibility on releases that provide a supported Xorg session.
- CI covers every declared Ubuntu/architecture tuple, or the release statement clearly identifies manually tested tuples.
- A single manifest supplies version/build values; CI verifies package, app, docs, and artifact labels agree.
- APT source uses a dedicated keyring and `signed-by=`; no `apt-key` use.

### P0: Crypto, Recovery, Backend, and Data Lifecycle

This milestone implements the consolidated P0 blockers above; playback itself is already tracked under Immediate.

**Crypto and data lifecycle.** Review key hierarchy, key derivation, encryption format/nonce uniqueness/authenticated framing, key wrapping/recovery, key rotation, migrations, deletion, SQLCipher WAL/FTS, and all plaintext spill paths. The current design has secure-delete, WAL truncation, encrypted audio, and bounded queues; acceptance must verify end-to-end effects rather than rely on individual settings.

**Recovery.** Ship a user-held recovery key or passphrase-wrapped workspace key, plus a versioned encrypted backup/export and restore flow. Recovery material must not be stored only beside the workspace or only in the same keyring. Explain recovery loss and key loss consequences before the user relies on the app.

**Secret Service and upgrades.** Handle locked keyring, auto-login, blank-password keyring, missing service, KDE Wallet/non-GNOME sessions, and headless SSH explicitly. A failed unlock must identify the existing workspace and offer retry/recovery; it must never initialize an empty replacement. Preserve user data on `remove` and `purge`; deletion is in-app only. Validate schema migration, interrupted migration, supported upgrade, and downgrade policy.

**Local backend.** The current random bootstrap token is passed in a private stdin control message, not argv; server code checks Host and Origin; EOF on the parent-control pipe requests shutdown. Add tests for these invariants and for UI SIGKILL/orphan cleanup. Keep authentication secrets out of environment and process command lines. Evaluate a per-user 0600 Unix-domain socket if it materially reduces exposure without breaking GTK/WebSocket integration.

**Exit checks**

- Independent crypto review has no unresolved critical/high findings; all accepted findings have tracked fixes or explicit risk acceptance before GA.
- Recovery restores an encrypted workspace, recordings, settings, and keys on a fresh machine/session without access to the original keyring.
- Tests cover capture/encode failure and disk-full; distinctive transcript/audio fixtures are absent from temp files, main DB, WAL, FTS, logs, and diagnostics after authorized deletion, within the documented OS/storage limits.
- Tests cover first launch, relaunch, locked/auto-login/blank keyring, missing key, recovery, upgrade, interrupted migration, remove/purge, and reinstall.
- Backend tests prove token absent from argv/environment, Host/Origin checks reject invalid authorities, EOF and forced parent death stop the child within 5 seconds, and normal quit reaps it.
- No maintainer script reads or mutates a user's home/workspace/keyring; removal and purge preserve user data.

### P1: Finish the Linux Notebook's Required Workflows

First inventory the UI against the shared core API. Keep functionality in the core where it is platform-independent; implement Linux-specific presentation and OS integration in the GTK shell.

**Recommended first-release scope**

- Stable history selection, notes, paged transcript, audio playback/archive, provider setup, Markdown export, recording consent, and explicit Stop.
- Meeting lifecycle actions needed for data ownership: rename, confirmed permanent delete, and explicit recovery/reprocess where supported by the core.
- Clear errors and disabled/busy states when the backend disconnects, a provider is unavailable, the keyring is locked, or audio has expired.
- Keyboard navigation, screen-reader labels, usable focus order, a documented minimum window size of 900x600 logical pixels, and dialogs that fit supported display sizes at 200% text scaling.
- GTK-native file selection and desktop integration consistent with GNOME conventions.

**Product decisions, not assumed parity requirements**

- Automatic call detection must only suggest a candidate. It must never start recording without explicit user confirmation. Linux desktop portals and application audio routing differ from macOS; validate feasible per-app scope before promising it.
- The current system-output source captures **all audio sent to the selected output**, including notifications, music, and other calls; it does not isolate a meeting or application. The GTK source picker, first-use agreement, and active recording status now disclose this. Per-application capture is not implemented.
- Calendar and email can remain deferred. If deferred, say so in the Linux release notes and UI where relevant.
- Speaker naming/linking, transcript cleanup, advanced recording controls, and macOS menu-bar behaviors need a deliberate priority rather than an implicit parity commitment.

**Exit checks**

- Automated GTK tests cover supported workflows and failure/cancel/busy/reconnect states, including rename, confirmed delete, recovery/reprocess, and Markdown export.
- Delete acceptance verifies session metadata, transcript, search index/FTS, summaries, audio keys and encrypted tracks are removed under the documented deletion boundary; caches and WAL are checked too. Exported files, backups, provider copies, memory, swap, and OS snapshots are explicitly outside that guarantee.
- Recovery and paid/provider actions remain explicit; reconnect never replays uncertain mutations.
- A keyboard-only task run completes launch, configure, consent, record/stop, navigate transcript, export, rename, delete, and recovery without a pointer. Orca review reports no blocking label/focus issues on supported GNOME Wayland.
- Window and dialog acceptance passes at 900x600 logical pixels and 200% text scale without clipped required controls.

### P1: Accept Audio on Real Ubuntu Hosts

Keep the current `pulsesrc` implementation as the supported baseline unless testing demonstrates a concrete limitation. “PipeWire support” currently means using PipeWire's PulseAudio compatibility server; it does not mean native PipeWire API integration or per-application capture.

**Required host scenarios**

- Physical microphone-only capture and microphone plus output-monitor capture on PulseAudio and PipeWire-Pulse.
- Alignment and drift over short and multi-hour sessions; verify timestamps, no unexplained gaps/overlaps, and correct track identity.
- Device selection, output-device changes, microphone unplug/replug, Bluetooth transitions, sound-server restart, and capture failure reporting.
- Headphones and speaker playback scenarios to characterize echo and microphone bleed; do not claim acoustic echo cancellation unless implemented and measured.
- Stop/EOS drain, app close, logout, suspend/resume, and recovery after process termination.
- GNOME Wayland on Ubuntu 26.04 is the mandatory primary target; X11 is secondary on Ubuntu 24.04 or another explicitly supported Xorg session. Validate dialogs, focus/lifecycle, scaling, and accessibility in each claimed session.
- Provider setup and real transcription/notes only in an explicitly authorized test account, separately from generated-signal tests.
- Custom `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, and `XDG_RUNTIME_DIR`; non-ASCII workspace/export paths; encrypted home; and local ext4 plus an explicitly supported/unsupported NFS scenario. SQLite WAL on network filesystems must be verified or rejected with a clear message.
- Disk exhaustion during a three-hour preset, forced process termination, logout, and suspend/resume. Preserve a valid encrypted prefix and report that the recording was interrupted; never leave plaintext capture spill.

**Exit checks**

- Record a host/OS/device test matrix with results and known limitations. Test one continuous **3-hour** recording, matching the current longest preset.
- Generated-signal CI remains the deterministic regression layer; physical tests are separately signed off and never presented as equivalent.
- For a 3-hour session, measured mic/output start offset is at most 100 ms and relative drift at most 50 ms/hour (150 ms total); generated common-clock tests have no unexplained dropped samples. Any physical gap over 100 ms is surfaced in diagnostics and investigated rather than silently attributed to user silence.
- Process-group RSS stays below 1.5 GiB during a 3-hour two-track capture and grows by no more than 50 MiB/hour after the first 15 minutes. Capture queues remain at configured bounds.
- Required free-space reservation is computed from selected streams, raw-track option, duration, and 50 MB reserve; disk-full injection stops capture cleanly, preserves authenticated encrypted prefix, and leaves no new plaintext temporary files.
- A default-device change never silently broadens capture scope or starts a new recording.

### P1: Package and Release Engineering

Continue release-specific `.deb` builds initially because GTK, GStreamer, PyGObject, Secret Service, and binary wheels are tied to the target Ubuntu runtime. Keep builds isolated per Ubuntu release and architecture. Provide a signed APT repository or PPA as the primary release/update channel; a static download and checksum is not an update mechanism. Use `signed-by=` and a repository-specific keyring, not `apt-key`.

**Build/package tasks**

- Pin and record Python dependency versions with hashes, build image digests, Ubuntu package versions, and native dependency inventory for every artifact. Generate an SBOM (CycloneDX or SPDX) and attach it to every release.
- Run automated vulnerability scans against the SBOM and vendored wheels on every release and at least daily against supported branches. Assign a security owner and publish a proposed response objective: acknowledge/triage critical or actively exploited CVEs within 1 business day, issue mitigation or a fixed release within 72 hours where feasible; high severity within 5 business days. Record exceptions and user mitigations when an upstream fix or compatible wheel is unavailable.
- Validate package metadata, desktop entry, icon, categories, launch command, permissions, and installed documentation. Replace placeholder maintainer/support metadata before distribution.
- Ensure package scripts do not access user workspaces or keyrings during install/remove/purge. Keep configuration and user data outside package-owned directories; never delete from `$HOME` in maintainer scripts. Purge leaves all per-user data intact; in-app deletion is the only supported destructive workflow.
- Define signed repository metadata/package publication, staged rollout, release notes, supported upgrade path, and rollback policy. Keep signing keys out of CI logs and repository files. Test key rotation and repository key replacement.
- Verify outbound provider requests honor documented proxy variables and use the system CA store or a documented override compatible with corporate roots; do not silently pin a `certifi` CA bundle that prevents enterprise TLS interception/trust configuration.
- Build and test the actual release artifacts on clean Ubuntu VMs, not only inside the build container.
- Test install, launch, quit, upgrade, reinstall, and remove on every supported tuple. Test both a fresh user and an existing encrypted workspace.
- Verify the packaged app contains no credentials, `.env`, recordings, test fixtures, model weights, or unintended repository files.

**CI and release checks**

- PR CI: core tests, Linux capture/settings tests, GTK shell tests, Ruff, package build, package metadata validation, installed smoke, generated audio, version-manifest validation, dependency hash/SBOM generation, and vulnerability scan on each supported tuple.
- Release CI: clean install/upgrade tests, artifact audit, repository signing, staged publication, SBOM and scan report, and release manifest generation.
- Manual release gate: physical audio acceptance, Wayland-first checks, keyring/recovery continuity, accessibility, provider proxy/CA behavior, and reviewer approval of release notes.
- Run CI from a trusted checkout; pull-request jobs should not receive release credentials.

**Exit checks**

- A clean-machine install succeeds without a source checkout, developer venv, or network access beyond normal package dependencies.
- The package launches from the desktop menu and command line, creates a private XDG workspace, connects only to its owned authenticated backend, and shuts that backend down.
- A signed APT repository/PPA installs and updates the app on clean supported systems, verifies signatures using the documented `signed-by=` keyring, and provides tested rollback. Checksums and a matching support statement/SBOM accompany releases.

### P2: Post-Release Operations and Deferred Work

- Triage crash/startup/capture reports without collecting transcript, audio, provider key, or workspace content by default.
- Maintain a compatibility table for Ubuntu updates, Python ABI changes, GTK/GStreamer changes, and keyring behavior.
- Re-run host acceptance when audio stack, capture source, Python ABI, encryption dependency, protocol, or storage migration changes.
- Track deferred features explicitly and avoid silently changing the supported feature matrix.

## 6. Acceptance Checklist

### Security and Privacy

- [ ] No predictable or world-readable plaintext audio staging; cleanup is verified.
- [ ] Data-at-rest review covers capture spills, DB/WAL/FTS, memory/core dumps, swap/hibernation, logs, exports/recent files, provider egress, and deletion limits.
- [ ] Crypto design review and user-held recovery/restore flow are complete; key loss consequences are disclosed and restore is tested on a fresh machine.
- [ ] Provider credentials are stored through Secret Service and never included in logs, diagnostics, exports, or frontend snapshots.
- [ ] Capture always requires accepted agreement, explicit source selection, and explicit Record action.
- [ ] System-output selection and consent clearly state that all selected-output audio, including notifications/music/other calls, is captured.
- [ ] Package has no operator credentials, personal data, recording fixtures, or model weights.
- [ ] Backend token is not in argv/environment; Host/Origin checks are regression-tested; parent EOF/SIGKILL stops/reaps the child within 5 seconds.
- [ ] Workspace permissions and symlink/path handling are tested for the supported filesystem/session.
- [ ] Locked/auto-login/blank keyring, Secret Service missing, KDE Wallet, and unsupported SSH/headless behavior are explicitly tested or rejected without data loss.
- [ ] Deletion tests inspect encrypted database, WAL, FTS, cache, logs, and owned audio/key files; documented exclusions are not represented as deleted.

### Product and Reliability

- [ ] Supported notebook workflows and failure states are covered by GTK/core tests.
- [ ] Rename, confirmed permanent delete, and explicit recovery/reprocess are tested end to end.
- [ ] Mic/output selection, consent, alignment, EOS draining, device loss, disk-full, suspend/resume, and one 3-hour session pass host acceptance.
- [ ] Three-hour mic/output start offset is <=100 ms and drift <=50 ms/hour (<=150 ms total); generated common-clock tests have no unexplained dropped samples; physical gaps >100 ms are reported and investigated.
- [ ] Process-group RSS stays below 1.5 GiB during a 3-hour two-track capture and grows <=50 MiB/hour after the first 15 minutes; capture queues remain bounded.
- [ ] Disk-full handling preserves an authenticated encrypted prefix, stops cleanly, and creates no plaintext spill. Required disk reservation includes streams, raw-track option, duration, and 50 MB reserve.
- [ ] Wayland on Ubuntu 26.04 is primary and verified; X11 is only claimed on tested supported sessions.
- [ ] Keyboard-only workflow completes; Orca review finds no blocking focus/label issues; minimum 900x600 logical pixels and 200% text scaling have no clipped required controls.
- [ ] macOS-only and deferred workflows are clearly called out.
- [ ] Custom XDG paths, non-ASCII names, encrypted home, local filesystem, and supported/unsupported NFS behavior are tested.

### Packaging and Support

- [ ] Ubuntu 26.04 is the GA baseline; 24.04 is included only if validated; 22.04 is preview/maintenance-only unless explicitly re-approved. Exact architecture, Python ABI, and session matrix is published.
- [ ] Signed APT repository/PPA supports install/update/rollback through `signed-by=` and repository key rotation; package purge never touches `$HOME`.
- [ ] Install, upgrade, reinstall, rollback, recovery, remove, and purge behavior are tested on clean systems; user data survives package operations.
- [ ] One version manifest is enforced in CI across core, Linux package, macOS app metadata, docs, and release artifacts.
- [ ] Hash-pinned dependencies, SBOM, daily vulnerability scan, and CVE response objectives are published and exercised.
- [ ] Proxy and system CA trust behavior pass tests, including a custom corporate CA.
- [ ] Artifacts are audited, checksummed, signed, and accompanied by release notes and support instructions.

## 7. Risks and Mitigations

| Risk                                                                  | Mitigation                                                                                                                                 |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Decrypted audio is left in a predictable temporary file               | Immediate preview fix; lifecycle and permission tests before any further preview distribution                                              |
| Ubuntu/Python ABI changes break vendored wheels                       | Build per supported Ubuntu release; pin dependency resolution; run installed-package tests on each tuple, including 26.04/Python 3.14      |
| PipeWire/PulseAudio host variation causes missing or misaligned audio | State the actual compatibility-server contract; validate physical hosts and retain clear device/error states                               |
| Users assume Linux has macOS's call detection or capture scope        | Publish a Linux feature matrix; disclose that system-output capture includes all selected-output audio; never broaden scope silently       |
| Package upgrade strands encrypted data or keys                        | Require user-held recovery material and test migration with isolated keyring services; never reset to an empty workspace on unlock failure |
| Version and build labels diverge                                      | Establish one release manifest and enforce it in CI                                                                                        |
| UI behavior passes Xvfb but fails under a real desktop session        | Treat Xvfb as smoke coverage only; require Ubuntu 26.04 GNOME Wayland host acceptance and test Xorg only where supported                   |
| Release artifacts become available without support/update process     | Make signed APT channel, ownership, signing, support contact, and rollback policy P1 release gates                                         |

## 8. Recommended Execution Order

1. Fix and test plaintext playback lifecycle and ship an independent preview update.
2. Freeze release audience, exact support matrix, feature scope, signed update channel, recovery design, and version scheme.
3. Complete the P0 crypto/data/backend review and workspace recovery/keyring continuity work.
4. Complete and accessibility-check the required GTK notebook workflows.
5. Run physical audio and Wayland-first desktop acceptance on every supported Ubuntu release/architecture family.
6. Harden package provenance, signed repository, dependency CVE process, and clean-machine install/upgrade validation; publish only after every P0 item and declared support-matrix gate passes.

## 9. Source Review Notes

- `ARCHITECTURE.md` is the architectural baseline, but it contains historical designs and macOS-specific flows; use its current implementation/status notes rather than treating every historical flow as a Linux requirement.
- `packaging/linux/README.md` and `docs/LINUX_UBUNTU_PREVIEW.md` correctly describe the preview boundary and much of the host-validation gap. Update them when the support matrix or release status changes.
- **Version values verified 2026-11-10:** `pyproject.toml` is 0.1.45; `packaging/linux/build_deb.py` uses build 46; `packaging/macos/build_desktop.py` sets 0.1.50/build 51. These values diverge now. CI must enforce one version manifest across the core, package metadata, app bundle, docs, and release artifacts.
- Canonical's [Ubuntu lifecycle](https://ubuntu.com/about/release-cycle) lists 22.04 standard maintenance through May 2027 and 24.04 through May 2029. Ubuntu's [26.04 release notes](https://documentation.ubuntu.com/release-notes/26.04/) state support through April 2031; the [26.04 desktop summary](https://documentation.ubuntu.com/release-notes/26.04/summary-for-lts-users/) says Ubuntu Desktop has been Wayland-only since 25.10, with XWayland for X11 applications. [GNOME 50 release notes](https://release.gnome.org/50/) identify the current upstream GNOME version. Recheck lifecycle details before release.
- Ubuntu/GNOME's transition of core applications to GTK 4/libadwaita is a strategic compatibility signal, not evidence in this plan of a specific GTK 3 upstream end-of-support date. Confirm the toolkit support policy before setting a GTK 4 migration deadline.
- Automated preview tests do not establish physical capture quality, privacy behavior of playback staging, upgrade continuity, Wayland support, or production release readiness.
