# macOS desktop app — proposed implementation plan

Drafted and audited 2026-10-26. **M0 fixture implemented; production desktop implementation in progress.**
Baseline: audited Phase 2b core `314dcbc`, merged through `0003b87`
([PR #5](https://github.com/tienbob/ScribeLoop/pull/5)).
See the [pre-implementation plan audit](MACOS_APP_PLAN_AUDIT.md).

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
  Use an isolated encrypted fixture store/test Keychain namespace, fake discovery
  and disabled provider transports. Never open, migrate or expire the real user
  store, read provider secrets, or request capture permissions in this fixture spike.
- Resolve helper binaries from the app bundle, never `data/native` or the working
  directory. Include SQLCipher, sqlite-vec, soundfile, crypto and other required
  native libraries with correct architecture/deployment targets.
- Verify SQLCipher opens, sqlite-vec loads and Keychain access works in the bundle.
- Exercise a generated audio session and orderly shutdown; no private audio needed.
- Inventory purpose strings, helper identities, Keychain access and required
  entitlements. An unsigned/generated fixture cannot prove capture permission
  attribution. Verify real permissions later with explicit user participation;
  signed update-stable attribution remains a signed acceptance check.
- Measure bundle/installer size, cold startup and idle/capturing RSS. Record actual
  numbers before setting release budgets; do not copy the current development
  environment's size or promise an unmeasured small installer.

**Exit:** an unquarantined local developer `.app` lists encrypted test meetings,
controls a minimal authenticated backend and exits cleanly without developer Python
or Homebrew. This is not a quarantined-distribution/Gatekeeper claim. The spike
proves a minimal lifecycle; M1 hardens it for production. Resolve runtime/library
loading failures before building the complete UI. When signing prerequisites are
available, repeat this same small artifact with hardened runtime/Developer ID
before expanding signed distribution work. The existing signing deferral remains
in effect; unresolved signed compatibility blocks release, not local UI work.

## Milestones and acceptance

| Milestone | Work | Exit check |
| --- | --- | --- |
| M0: isolated bundle spike | Prove minimal frozen runtime, helpers, synthetic encrypted fixture, private bootstrap and one native window | Local Finder launch, correct architecture/deployment metadata, no external Python/dylibs; no real data, credentials, capture or network calls |
| M1: production host contracts | Harden lifecycle/auth, bounded paged/revisioned protocol, request acknowledgments and Keychain settings adapter | No duplicate/orphan core; bounded reconnect with no mutation replay; secrets stay out of logs/env/argv |
| M2: first-use and call workflow | Native agreement/permissions, secure provider setup, menu status and Record / Not now panel | No capture before Record; deny/Not now opens no source; Stop works with window closed; real tests remain explicitly consented |
| M3: native meeting workspace | History, live transcript, speakers, section/final notes, citations, playback, archive, rerun and export on M1 contracts | Feature parity; only requested pages/window cached; source links and revision invalidation work after reconnect |
| M4: continuity and settings | In-place legacy-store adoption, presets/people/forget, optional login launch, sleep/wake and network errors | Existing path/key identities preserved; ambiguous dispatches never auto-retry; paused detection state is real backend state |
| M5: packaged local acceptance | CI, failure matrix, performance measurements, privacy checks and separate consented physical tests | Reproducible local `.app` report with synthetic, physical and signed evidence separated |
| M6: signed distribution | Hardened signed spike, notarization/stapling, DMG, clean-Mac and upgrade validation | Gatekeeper and real TCC/Keychain upgrade access verified; minimum-OS claim tested; no developer tools required |

Dependencies are M0 → M1 → M2/M3 → M4 → M5 → M6. Secure provider settings and
paging are M1 prerequisites, not late M4 additions. Login launch is optional after
manual launch works. Relocating/copying an existing encrypted store is a separately
gated follow-up; MVP adoption uses its existing path.

M0–M5 local development can proceed under the existing signing deferral. M6 needs
the certificate/account prerequisites when distribution is requested; do not ask
for them again merely to draft this plan or start unsigned local development.

## Backend changes required for a desktop host

1. **Dedicated desktop bootstrap.** The current CLI defaults to keyless demo access,
   fixed port 8757, stderr launch URLs and repository `.env` configuration. It is
   not the shipping launcher. Add a desktop entry point with encrypted storage and
   mandatory per-launch authentication. Bind loopback on an OS-assigned port and
   publish the actual ready port/protocol only after initialization succeeds. The
   Swift supervisor creates the random 256-bit token and passes it only through
   an inherited private control channel; the child fails closed if it is absent.
   Validate returned PID/launch identity, protocol, loopback host and actual port.
   Use startup states/timeouts with Cancel/Retry and no automatic Keychain-prompt
   restart loop; drain bounded diagnostic output continuously to avoid pipe deadlocks.
2. **Private readiness/control channel.** Use inherited pipes/handles for the child
   handshake and shutdown. Keep the random token in process memory; never put it
   in argv, URLs, user logs or saved preferences. Do not infer readiness from an
   unauthenticated health response. The existing first-WebSocket-frame auth can be
   reused; native no-Origin connections already require token authentication.
3. **Versioned commands/events.** Use the existing Pydantic contracts, snapshots and
   event types through Swift `URLSessionWebSocketTask`. Add schema/version fixtures,
   correlation IDs and actionable errors where needed. Reject incompatible core
   versions before mutations. Define a desktop envelope carrying protocol version,
   request ID and action payload; do not add arbitrary fields to existing strict
   Pydantic actions. Provide accepted/rejected acknowledgments and authoritative
   status lookup for ambiguous state-changing/paid requests. Reconnect queries
   status/snapshots instead of resending Record, Generate, Rerun or Delete.
   Prove URLSession loopback WebSocket behavior/ATS on the minimum OS; never use a
   global arbitrary-load/TLS-bypass exception to make the connection work.
4. **Typed release preferences.** Add redacted provider-settings reads and secure
   writes, persisted through Keychain. Retain `.env` only for development and
   explicit imports. OpenAI remains the default summary API. Secret changes never
   appear in snapshots, diagnostic exports, process environment dumps or arguments.
   In desktop mode the backend reads provider keys from Keychain through an injected
   settings source; disable automatic repository `.env` discovery/environment-key
   fallback. Validate/save settings locally without a provider request. Freeze
   provider/model/key/endpoint settings for each active workflow; changes apply to
   new work while idle. Do not silently reconfigure active capture or retry uploads.
   This requires adapting DeepgramConfig/CloudRecordingRouter and SummaryProvider;
   their current environment/path-based initialization is not a release settings API.
5. **Resource paths and release mode.** Inject bundle helper/runtime paths and data
   directories instead of source-tree-relative paths. Disable development replay,
   keyless mode and browser asset serving in the packaged desktop entry point.
6. **Process ownership.** App death closes a parent-liveness channel; backend death
   closes capture-helper control pipes. Helpers must exit on parent/control EOF.
   Stop only owned children/process groups, never kill by generic process name.
   A crash may restart the core with a bounded retry policy, but must mark work
   interrupted and never resume capture/provider requests automatically. The meeting
   helper already accepts STOP/EOF on stdin; reuse and test that behavior. The new
   app-to-core liveness channel is still required. Isolate owned process groups from
   the UI/Terminal group before any group termination and handle PID reuse safely.
   On normal Quit, await encrypted writer close within a measured deadline. On a
   hung process or forced kill, retain an authenticated prefix and mark interrupted;
   a valid footer cannot be guaranteed. Never describe forced termination as a
   successfully finalized recording or continue LLM work invisibly after Quit.
7. **Bounded native UI delivery.** The current WebSocket snapshot returns full
   histories/transcripts although the web view renders pages. Add paged session/
   transcript reads and lightweight snapshots before claiming bounded native load.
   Keep source-citation navigation able to fetch older pages. Bound playback to one
   selected window; export still contains the full current transcript. Use stable
   session cursors and revision-pinned transcript pages, sequence/boot IDs and
   invalidation events. Close the snapshot/subscription race, deduplicate confirmed
   segments and fetch citations by session/revision/segment ID. A short replacement
   must clear old pages and draft notes. Keep only bounded in-memory native caches;
   do not persist transcript/audio/keys in UserDefaults, scene restoration or logs.

## Native UX

- **Main window:** meeting list, current transcript and cited notes; clear app,
  microphone/output source, anonymous speaker names and processing state.
- **Menu bar:** watching, possible call, recording with elapsed time, preparing
  notes, or interrupted/error. Include prominent Stop, open meeting and pause
  detection controls. A state indicator must remain while the window is closed.
  Add an actual backend detection-enabled command/state: hiding UI suggestions is
  not pausing observation. For MVP, disable Pause while recording and require Stop
  first so the existing inactivity monitor is not silently disabled mid-call.
- **Detected-call panel:** source/app, microphone, browser-all-tabs warning,
  provider/summary destinations, duration/cost limits, Record and Not now. Revalidate
  the candidate at acceptance. Do not steal full-screen meeting focus repeatedly.
- **Notifications:** optional; ask notification permission only when enabling them.
  A notification opens the current call review; a stale notification cannot start
  recording. If notifications are denied, use menu status/in-app prompts.
- **Preferences:** provider credentials, recording/language presets, limits,
  permissions, data location, first-use agreement/revocation and optional login
  launch. OS permissions remain OS-controlled; no repeated processing checkboxes.
  Login launch opens menu-only monitoring after setup, never starts recording, and
  must handle OS-disabled/requires-approval states. Manual launch opens the notebook.
- **Close versus Quit:** closing the window keeps the menu app running. Quitting
  during recording offers Stop & Quit / Cancel, then attempts bounded graceful
  writer closure and cancels pending text work. Forced shutdown is interrupted,
  not guaranteed finalized. Never wait indefinitely for an
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

**MVP: explicitly adopt an existing store in place.** Let the user select the old
DB location; stop the development server, acquire its exclusive lock, verify its
existing Keychain key and open the same DB/recording directory. Show that location
in Preferences. Do not infer a new database path and silently create a blank store.

**Relocation/copy is not an MVP requirement and must not use a file-copy shortcut.**
It needs its own reviewed migration service and fault-injection acceptance:

- Quiesce all source readers/writers and both app instances. Preserve/checkpoint
  committed WAL data using a verified SQLCipher backup/checkpoint strategy; copying
  only the `.sqlcipher` file may omit committed changes. Never make a plaintext
  intermediate. Do not replace an existing destination or its keys silently.
- Stage encrypted DB/audio, register and verify the final path-bound database key
  reference, fix verified owned paths, and journal phases without recording keys.
- Audio accounts currently use global `audio:<session UUID>` names, not per-store
  namespaces. Two copied active stores therefore share audio keys: deleting audio
  in one can make the other unreadable. Resolve namespaced destination key mapping/
  re-encryption or a verified single-authoritative-store retirement design **before**
  exposing copy/import. A preserved source copy is not automatically an independent
  recoverable backup. Do not silently delete shared keys or claim safe coexistence.
- Exercise missing/locked keys, failed copies, insufficient space, interrupted
  Keychain/DB commits, destination collisions and rollback before deleting or
  retiring anything. Recover through an explicit journal workflow, not key
  regeneration. Keep new-schema rollback limits visible; app downgrade does not
  imply schema downgrade is supported.

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

Maintain a measured dependency/build manifest (Python, SQLCipher, sqlite-vec,
NumPy, crypto and runtime architecture/minimum OS) from a clean cloud-only build
environment. Native wheel success on the developer Mac is not a deployability
check. Scan Mach-O dependencies/rpaths and bundle contents for external dylibs,
secrets, databases, recordings, local ML weights and development tools.

CI adds Swift build/unit tests, schema fixtures, synthetic backend/UI lifecycle,
app-bundle dependency inspection and generated capture checks to existing core
Python/frontend/Linux/macOS coverage. Test wrong-token/external-origin rejection,
locked/missing keys, already-running development core, permission denial/revocation,
helper/backend/UI crash, sleep/wake, device changes, quota/network failure,
first-use/reconnect, deletion and in-place adoption. Test WAL/key migration and
downgrade/copy semantics separately before implementing relocation. Include full
screen/Focus/notification-denied cases, offline behavior (no local ASR fallback),
unknown/stale call activity, normal and forced Quit, and three-hour IPC/UI bounds.
The minimum supported OS requires a real compatible host/VM check before release;
a newer CI runner compiling with a lower deployment target is not that check.

For Developer ID distribution, Apple documents signing distributed executables,
hardened runtime, notarization and stapling in
[Notarizing macOS software](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
Audit minimal entitlements for bundled Python/extensions; do not solve failures by
blanket-disabling library validation or weakening local API authentication.
Certificates/notary credentials remain outside the repository. Ad-hoc developer
builds do not establish release signing, TCC attribution or Gatekeeper acceptance.

## First implementation task

Create the isolated M0 fixture app with a minimal private bootstrap, frozen core
and bundled helpers. Display only synthetic encrypted history, prove local Finder
launch and clean exit, and record dependency/size/RSS/startup evidence. This spike
must not touch real credentials/data, request microphone permission or call cloud
providers. Complete the M0 decision record before the M1 production adapter and
the full native UI. Signing and relocation remain explicit later gates.

## M0 implementation started — 2026-10-26

Use the installed `/Applications/Xcode.app` through per-command `DEVELOPER_DIR`;
do not change the machine's global Xcode selection. Implement only the isolated
fixture app, private authenticated bootstrap, bundled cloud-only Python runtime,
read-only encrypted synthetic history and owned-child shutdown. The fixture backend
must expose no recording, permission or provider commands and read no `.env` or
production Keychain/data. Freeze from a separate environment, not the ML-heavy dev
virtualenv. Validate bootstrap failures, auth, Finder-like launch, dependency paths,
Swift build and explicit smoke/reconnect/shutdown. Real permissions/credentials and
production UI remain M1+ work.

### M0 fixture evidence — 2026-10-26

Implemented on `feature/macos-m0-fixture`: native SwiftUI notebook/menu bar,
owned bundled sidecar, private bounded stdin bootstrap and random-token WebSocket,
read-only synthetic encrypted history and native-library probes. Separate packaging
environment excludes local ML extras. Xcode 27.1 builds an approximately 80 MB
ad-hoc signed host-only app. See [build and verification instructions](../packaging/macos/README.md).

Validation: 412 Python tests pass; native startup/reconnect/same-session/shutdown
smoke passes (~0.64 s); explicit STOP and parent EOF pass; 91 Mach-O binaries have
no unbundled non-system dependency paths. Native window inspection shows Connected,
the generated transcript and four verified storage/library checks. No production
Keychain, capture permissions, provider keys or real recordings were used.

M0 fixture implementation is available. Broader packaging failure injection,
older-OS compatibility and release-signing/TCC proof remain open gates; do not
interpret this spike as a production-ready desktop app. M1 production host/data
integration is next, following the audited plan.


## Full desktop implementation authorized — 2026-10-26

Build the local production app on `feature/macos-desktop-app`, preserving the M0
fixture as a separate build. Implement M1–M4 together in dependency order:

1. Private desktop bootstrap with a selected encrypted database path, bundled
   helpers and environment-independent provider settings stored in Keychain.
   No automatic developer `.env` import or existing-store migration.
2. Protocol v2 envelopes with bounded request receipts/status lookup, boot/event
   identity, bounded history/transcript pages and revision invalidation. Reconnect
   reads state and never replays mutations. Settings changes require idle state.
3. Native first-use agreement, secure settings, manual microphone/application
   recording and source-aware detected-call Record / Not now. Menu bar Stop remains
   available without the main window. Pausing stops backend observation while idle.
4. Native saved-meeting workspace: paged transcript and speaker corrections,
   cited summaries/section notes, bounded audio playback, archive, explicit rerun,
   export, delete/recovery, people and recording presets. Existing stores can be
   explicitly selected in place; new installs use Application Support.
5. Package an independently named ScribeLoop.app and validate with injected fake
   transports and synthetic data, compile/build, full regression tests and bundled
   lifecycle checks. Never make paid provider requests or start physical capture as
   an implementation test. Physical calls and signed distribution remain separate
   acceptance; Developer ID/notarization remain deferred.

Use local ad-hoc signing and conservatively host-only minimum OS until older-host
validation. Do not claim M5 physical or M6 release acceptance from synthetic tests.


### Native contrast correction — 2026-10-26

Screenshot inspection found a fixed pale background combined with dark-appearance
white text, making the workspace unreadable. Replace fixed paper colors with
semantic macOS surfaces and primary/secondary foregrounds, use the system accent
for controls, and constrain sidebar width. Rebuild both desktop and preview app,
then inspect screenshots of notes, transcript and preferences before acceptance.

### Full desktop local implementation evidence — 2026-10-26

The production development artifact is `dist/macos/ScribeLoop.app`; build/setup
instructions are in [DESKTOP.md](../packaging/macos/DESKTOP.md). The M0 app remains
separate. Production bootstrap uses protocol 2, a private parent pipe, random-token
WebSocket and explicit selected encrypted store. Provider configuration is injected
from Keychain, never repository `.env` or inherited provider-key environment values.

Implemented native agreement/settings, metadata discovery and idle pause, compact
source-aware call review, manual microphone/app/system recording, live words and
speaker labels, menu Stop, dated meetings/topic titles, paged history/transcripts,
citation navigation, notes, bounded playback, archive/rerun, export, recovery/resume,
manual people links and speaker corrections, and in-place store selection. Requests
have bounded boot-scoped receipts; reconnect reads state/status without replay.
Unknown mutations require explicit state review. Provider changes are frozen while
capture or text work is active. Native UI caches at most 120 history entries and
one 100-segment transcript page, with at most one five-minute decoded audio window.

Validation: 420 Python tests pass, 15 frontend regression groups pass, module-tree
import smoke passes. Production Swift sources compile and the approximately 81 MB
app is ad-hoc signed. Bundle audit inspects 91 Mach-O binaries: no external
non-system dependency/rpath, credentials, recordings or local model weights found.
Separate preview native smoke passes through 230-row paging, agreement, settings,
reconnect, backend pause and owned shutdown. A generated-PCM/fake-WebSocket test
proves live speaker text before Stop, duplicate request suppression, settings freeze,
reconnect preserving capture and one existing stream per selected track without a
full rerun. Screenshots verified readable notes, transcript and preferences in dark
appearance after the contrast correction. No physical capture, production Keychain
or paid provider request was used for validation.

M1–M4 local implementation is available, with physical/OS acceptance still open.
This does not complete M5 physical validation or M6 distribution. Pending checks:
real permission attribution and call timing, sleep/wake/device changes, login-item
OS approval, minimum-OS/clean-Mac runtime support, signed upgrade access and
notarization/installer release. The build conservatively requires the current host
macOS version until compatibility is tested. Signing deferral remains in effect.

### Summary provider picker — 2026-10-26

Replace the native provider text field with OpenAI, Gemini, Ollama, LM Studio and
Custom choices. Known providers automatically select their documented API endpoint
and hide its editor; Custom exposes the URL field. The backend owns the preset
mapping. Preserve existing nonstandard endpoints as Custom. Changing destinations
clears the old summary credential unless a new key is explicitly supplied, so a
provider switch cannot send another provider's saved key. Local summary servers
use their installed model ID and optional authentication; transcription stays on
Deepgram. Validate settings and rebuild without calling any provider.

Provider-picker validation: 9 desktop tests pass, production/preview native builds
succeed, and native inspection confirms OpenAI/Ollama hide the URL while Custom
exposes it. Provider endpoint enforcement, local no-key configuration and clearing
credentials across destinations are covered without provider requests.
