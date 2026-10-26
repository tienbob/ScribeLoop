# macOS desktop app — proposed implementation plan

Drafted 2026-10-26. **Planning only; native desktop implementation has not started.**
Baseline: audited Phase 2b core at `314dcbc`, [PR #5](https://github.com/tienbob/ScribeLoop/pull/5).
Merge that core separately before beginning the implementation sequence below.

## Product outcome

Install `ScribeLoop.app`, open it from Applications, and leave it in the menu bar.
No terminal, developer Python installation, repository checkout or browser is needed.
A likely call opens a compact source-aware **Record / Not now** prompt. Recording
creates a dated meeting, streams words with anonymous speaker labels, then stops
when the user presses Stop or the existing inactivity heuristic ends the call.
Notes and an automatic topic title follow. Closing the main window keeps the app
and its recording indicator available; quitting stops capture and the core.

Preserve cloud-only Deepgram ASR, OpenAI summary default/compatible overrides,
first-use agreement, explicit per-call recording choice, encrypted storage,
30-day audio expiry/archive, bounded playback/manual reruns, person links and
spending limits. Browser capture still means all tabs in the selected browser.
Voice recognition stays disabled until its separate backend/calibration gate is met.

## Recommended architecture

**Native SwiftUI UI + the existing Python core as an app-managed child process.**
Keep the current Swift capture/observer helpers. Build a real macOS interface rather
than making the development website the production window. The browser UI remains
a development/test client. Do not rewrite ASR, summary, encryption or retention
logic in Swift or create a second meeting database.

| Component | Responsibility |
| --- | --- |
| SwiftUI app | Windows, menu bar, call prompt, onboarding, preferences, playback and export |
| Core client | Codable command/event types, authenticated connection, snapshots and reconnect |
| Core supervisor | Launch one bundled backend, readiness, shutdown and bounded crash recovery |
| Python core | Existing capture coordination, provider calls, encrypted storage, notes and people |
| Swift helpers | Existing native audio transport and metadata-only activity observation |
| Keychain | Database/audio keys and release provider credentials; no packaged `.env` |

Proposed first target: **Apple Silicon, macOS 14+**. The minimum OS is provisional
until the frozen runtime and every native dependency are verified on it. Intel and
Linux desktop shells are separate later builds; shared backend behavior remains
portable. Use an Xcode app target with testable Swift modules. Prefer an embedded
one-directory frozen Python runtime; evaluate PyInstaller in the first spike.
No bundled local speech/embedding/LLM weights, Torch or development environment.

The app is a regular user process, not a root daemon. Initial distribution is a
Developer ID `.app`/DMG outside the App Store; App Store sandbox distribution is
separate scope. Optional login launch must be user-enabled, not installed silently.

## First deliverable: packaging and identity proof

Build the smallest working `.app` **before** porting all screens:

- Launch the bundled core without system Python or Homebrew paths.
- Show connection/storage state and synthetic meeting history in a native window.
- Resolve helper binaries from the app bundle, never `data/native` or the working
  directory. Include SQLCipher, sqlite-vec, soundfile, crypto and other required
  native libraries with correct architecture/deployment targets.
- Verify SQLCipher opens, sqlite-vec loads and Keychain access works in the bundle.
- Exercise a generated audio session and orderly shutdown; no private audio needed.
- Establish permission attribution for the app/helper topology with local builds.
  Signed update-stable attribution is a later signed acceptance check, not something
  the unsigned spike can prove.
- Measure bundle/installer size, cold startup and idle/capturing RSS. Record actual
  numbers before setting release budgets; do not copy the current development
  environment's size or promise an unmeasured small installer.

**Exit:** a double-clickable local `.app` lists encrypted test meetings, controls
its own backend and leaves no orphan processes. If freezing/signing/library loading
fails, resolve that packaging constraint before building the complete SwiftUI UI.

## Milestones and acceptance

| Milestone | Work | Exit check |
| --- | --- | --- |
| M0: contracts and bundle spike | Fix app identity/minimum OS, runtime/helper manifests, core protocol version and synthetic fixtures; complete the first deliverable above | Clean-machine/path-independent launch; encryption/vector/helper checks pass |
| M1: managed core lifecycle | Desktop bootstrap, authenticated connection, snapshots, single-instance/lock behavior, child ownership and shutdown | No port conflicts, startup key paste, silent duplicate core or orphan capture after crashes |
| M2: first-use and call workflow | Native agreement/permissions, provider setup, menu status and Record / Not now panel | No capture before Record; denial/Not now opens no audio source; Stop works with the main window closed |
| M3: native meeting workspace | History, live transcript, speakers, rolling/final notes, sources, playback, archive, rerun and Markdown export | Native feature parity with the audited core; long lists/windows stay bounded and reconnect restores saved state |
| M4: preferences and continuity | Keychain provider settings, presets/budgets, people/forget, data adoption, optional login launch, sleep/wake and network errors | Upgrades preserve keys/data; revoked access and interrupted work stay visible without automatic paid retries |
| M5: packaged local acceptance | Build automation, UI/protocol tests, failure scenarios, memory/startup measurements and consented physical tests | Launchable local `.app` and reproducible report; unsigned/signed results clearly distinguished |
| M6: signed distribution | Developer ID signing, hardened runtime, notarization/stapling, DMG and upgrade validation | Gatekeeper acceptance and permissions verified on a clean Mac; no developer tools required |

M0–M5 local development can proceed under the existing signing deferral. M6 needs
the certificate/account prerequisites when distribution is requested; do not ask
for them again merely to draft this plan or start unsigned local development.

## Backend changes required for a desktop host

1. **Dedicated desktop bootstrap.** The current CLI defaults to keyless demo access,
   fixed port 8757, stderr launch URLs and repository `.env` configuration. It is
   not the shipping launcher. Add a desktop entry point with encrypted storage and
   mandatory per-launch authentication. Bind loopback on an OS-assigned port and
   publish the actual ready port/protocol only after initialization succeeds.
2. **Private readiness/control channel.** Use inherited pipes/handles for the child
   handshake and shutdown. Keep the random token in process memory; never put it
   in argv, URLs, user logs or saved preferences. Do not infer readiness from an
   unauthenticated health response. The existing first-WebSocket-frame auth can be
   reused; native no-Origin connections already require token authentication.
3. **Versioned commands/events.** Use the existing Pydantic contracts, snapshots and
   event types through Swift `URLSessionWebSocketTask`. Add schema/version fixtures,
   correlation IDs and actionable errors where needed. Reject incompatible core
   versions before mutations; reconnect restores state without replaying commands.
4. **Typed release preferences.** Add redacted provider-settings reads and secure
   writes, persisted through Keychain. Retain `.env` only for development and
   explicit imports. OpenAI remains the default summary API. Secret changes never
   appear in snapshots, diagnostic exports, process environment dumps or arguments.
5. **Resource paths and release mode.** Inject bundle helper/runtime paths and data
   directories instead of source-tree-relative paths. Disable development replay,
   keyless mode and browser asset serving in the packaged desktop entry point.
6. **Process ownership.** App death closes a parent-liveness channel; backend death
   closes capture-helper control pipes. Helpers must exit on parent/control EOF.
   Stop only owned children/process groups, never kill by generic process name.
   A crash may restart the core with a bounded retry policy, but must mark work
   interrupted and never resume capture/provider requests automatically.
7. **Bounded native UI delivery.** The current WebSocket snapshot returns full
   histories/transcripts although the web view renders pages. Add paged session/
   transcript reads and lightweight snapshots before claiming bounded native load.
   Keep source-citation navigation able to fetch older pages. Bound playback to one
   selected window; export still contains the full current transcript.

## Native UX

- **Main window:** meeting list, current transcript and cited notes; clear app,
  microphone/output source, anonymous speaker names and processing state.
- **Menu bar:** watching, possible call, recording with elapsed time, preparing
  notes, or interrupted/error. Include prominent Stop, open meeting and pause
  detection controls. A state indicator must remain while the window is closed.
- **Detected-call panel:** source/app, microphone, browser-all-tabs warning,
  provider/summary destinations, duration/cost limits, Record and Not now. Revalidate
  the candidate at acceptance. Do not steal full-screen meeting focus repeatedly.
- **Notifications:** optional; ask notification permission only when enabling them.
  A notification opens the current call review; a stale notification cannot start
  recording. If notifications are denied, use menu status/in-app prompts.
- **Preferences:** provider credentials, recording/language presets, limits,
  permissions, data location, first-use agreement/revocation and optional login
  launch. OS permissions remain OS-controlled; no repeated processing checkboxes.
- **Close versus Quit:** closing the window keeps the menu app running. Quitting
  during recording offers Stop & Quit / Cancel, then bounds shutdown, finalizes
  encrypted writers and cancels pending text work. Never wait indefinitely for an
  LLM or leave capture running after Quit. Explain interrupted notes/resume actions.
- **Accessibility:** keyboard navigation, VoiceOver labels, dynamic type/contrast,
  visible focus, selectable transcript text and accessible recording announcements.

Use SwiftUI scene/menu APIs, with AppKit only for specific window/activation and
system interactions. Apple's [multi-window/MenuBarExtra guidance](https://developer.apple.com/videos/play/wwdc2022/10061/)
supports the menu/window composition. Evaluate
[SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)
for explicit user-enabled login launch; it must not grant recording authorization.

## Existing data and credentials

Use the existing Application Support store in place when possible. Keep the
current storage Keychain service/account identifiers stable across app updates.
**Database key accounts currently hash the resolved database path**; moving a
`.sqlcipher` file alone makes its original key lookup fail.

For project-local development stores, provide an explicit adoption/import flow:
stop/lock the old core, verify its existing Keychain key, copy the encrypted DB and
recording tree to staging, register the destination key reference, verify the copy
and commit the migration with a recovery journal. Preserve originals until success.
Rewrite stored paths only for verified owned recordings. Cover interruption, missing
keys, locked Keychain, collisions and rollback. Never silently create a replacement
key for an unreadable existing database or export plaintext during migration.

Do not copy a developer `.env` automatically or ship it. Provider credential import
is a separate explicit user action. Release credentials belong in Keychain.
Changing bundle/signing identity may affect access prompts; validate upgrade access
with the actual signed app rather than assuming Python's prior permission carries.

## Build, distribution and tests

Proposed directories:

```text
shell/macos/                    Xcode app, Swift modules and UI tests
packaging/macos/                Frozen-core manifest, signing and DMG scripts
core/scribeloop_core/api/       Desktop bootstrap/contract adapter
core/.../capture/native/        Existing capture/observer source
```

Package owned executables/libraries in appropriate app code locations with an
explicit manifest. Validate nested code signatures inside-out; do not depend on
Homebrew dylibs, the developer virtualenv or writable bundle resources. Start with
manual versioned DMG releases; signed automatic updates are later scope.

CI adds Swift build/unit tests, schema fixtures, synthetic backend/UI lifecycle,
app-bundle dependency inspection and generated capture checks to existing core
Python/frontend/Linux/macOS coverage. Test wrong-token/external-origin rejection,
locked/missing keys, already-running development core, permission denial/revocation,
helper/backend/UI crash, sleep/wake, device changes, quota/network failure,
first-use/reconnect, deletion/migration and upgrade behavior.

For Developer ID distribution, Apple documents signing distributed executables,
hardened runtime, notarization and stapling in
[Notarizing macOS software](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
Audit minimal entitlements for bundled Python/extensions; do not solve failures by
blanket-disabling library validation or weakening local API authentication.
Certificates/notary credentials remain outside the repository. Ad-hoc developer
builds do not establish release signing, TCC attribution or Gatekeeper acceptance.

## First implementation task

Create the Xcode SwiftUI target and desktop protocol/supervisor spike. Bundle the
minimal frozen core plus existing helpers, display a synthetic encrypted meeting
snapshot, then prove launch, authenticated reconnect and clean shutdown from Finder.
Do not start with a full UI rewrite or a Swift reimplementation of the core.
