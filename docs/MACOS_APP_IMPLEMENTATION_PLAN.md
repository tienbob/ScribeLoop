# macOS desktop app — implementation plan and history

Drafted and audited 2026-10-26. **Updated 2026-10-28: native desktop workflows and local packaging are implemented; physical/release acceptance remains open.**
See the [current checklist](CURRENT_STATUS.md) for active work. The dated entries below preserve implementation history; older pending findings may be resolved by later entries.
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

### Operator-managed transcription — 2026-10-26

Remove the Deepgram credential section from native preferences and reject user
transcription-key changes at the desktop API. Desktop transcription uses only an
operator credential supplied during packaging (`SCRIBELOOP_BUILD_DEEPGRAM_API_KEY`,
or the build checkout's ignored `.env` DEEPGRAM_API_KEY). Runtime environment and
legacy user Keychain Deepgram values cannot override it. Keep user summary-provider
settings and recording language/duration choices. The preview bundle strips the
operator credential and uses an empty injected configuration. No provider calls
are made by validation. Bundling enforces app configuration, not key secrecy;
a distribution backend is needed to protect a shared service credential.

Operator-transcription validation: 423 Python tests and native preview smoke pass.
Production bundle audit passes and reports the operator credential's presence;
the preview explicitly has no transcription credential resource. Native screenshot
inspection confirms Preferences starts with summary settings and contains no
Deepgram credential editor. Tests prove user patches are rejected, legacy Keychain
keys/runtime environment cannot override the operator key, and summary edits leave
transcription configuration unchanged. No provider request was made.

### Playback and speaker naming UX — 2026-10-26

Replace minute-step playback with a full-duration timeline, play/pause, scrub to
any position, skip controls and playback speed. Decode authenticated windows on
demand and advance through the whole recording without loading it all into RAM.
Authenticate/index encrypted tracks before random access; invalidate on file change,
recheck keys for each read, and correlate responses so late seeks/track/session
changes cannot play stale audio. Keep decryption local, cancellation safe and memory
bounded. Provide conversation mix plus individual tracks.

Replace standalone Add person with a meeting-wide numbered speaker roster (including
speakers outside the current transcript page), sample utterance and one-step
select-Person-1/name/save. Support linking existing people. The same numbered labels
appear in the transcript and picker, scoped to the current transcript revision.
Creating and linking a person must be atomic; stale revisions produce no orphan
person. Names remain manual labels and do not enable voice learning. Validate with
synthetic audio/speakers, native playback/controller tests and screenshots.

Playback/naming evidence: implemented conversation/individual-track player with
whole-duration scrubbing, play/pause, 15-second skips and 0.5×–2× speed. At most two
60-second windows are retained in the UI. Sparse track checkpoints are created only
after footer authentication; changed tracks are reindexed and requested records are
reauthenticated. This supersedes the prior five-minute playback-window UI.

The Name people button opens a visible single-step form: select the transcript's
Person number, type a name or choose an existing person, Save. Stable meeting-wide
numbering includes speakers on other pages. Naming/linking and derived notes are
atomic and revision-checked. Screenshot inspection caught and fixed a clipped form;
the complete picker/name/save controls now fit without scrolling.

426 Python tests pass. Native preview smoke verifies speaker naming, full duration,
seek, continuous generated-silence playback, pause/resume, speed, stale-seek rejection,
track changes and cleanup. Actual native UI inspection verified Person 3 naming,
seeking to 1:30 and playing from there, pause and speed options. Synthetic test data
only; no capture or provider calls. Build and dependency/signature audit are repeated
for the final app artifact.

Final smoke-run correction: the synthetic preview now begins with its test agreement
accepted. The runner must still await the accept-agreement receipt before sending
the next mutation, rather than treating an already-true state as acknowledgment.
Updated agreement/settings/naming/pause steps to await their own accepted request
receipts. The rebuilt native smoke passes with playback controls, person naming,
seeking, reconnect, pause and shutdown verified; final bundle audit passes.

### Explicit real-provider UI trial — 2026-10-26

User authorized real API calls and requested the already-built production app,
including microphone use. Use its normal UI and saved settings. No alternate API
test app or injected recording source is used; the unused harness was discarded.
The controlled speech source is the existing public AMI clip played through
QuickTime into the microphone. Keep recordings local/encrypted and avoid copying
private room audio/transcript into reports. Validate saved playback, naming and one
explicit OpenAI summary on the same recording.

### Real built-app UI findings and repair — 2026-10-26

User redirected testing to the already-built production app and explicitly allowed
microphone use. Requested mic access in its UI, recorded about 33 seconds while
playing the existing public AMI clip through QuickTime, and stopped via UI. The
real Deepgram result contains five segments and two anonymous speakers. Playback,
seeking to 18 seconds and 1.5× speed work on this actual encrypted recording.
Confirmed transcript text was observed after Stop in this run; live latency is not
accepted from it. The preexisting empty recording was not deleted.

Naming Person 2 failed twice in the real app. An isolated streaming-path reproduction
shows commit_window creates segments without transcript_revisions metadata, causing
person_links' foreign-key insert to fail. Preview seeding used append, which creates
that metadata, hiding the defect. Fix commit_window to create provenance within the
same transaction and add schema 11's additive/idempotent missing-revision repair so
existing saved streams can be named without rerunning/uploading audio. Cover naming
and derived summaries using actual streaming commits and SQLCipher. User has now
saved their summary key in Preferences; test one summary on the same saved recording
after repair. No alternate public-API test bundle is used; discard its unused harness.

### Model ID editing affordance — 2026-10-26

User reported the model field was not editable. In the running production UI,
click/type changed its value; restored the original without saving. The grouped
Form's borderless right-aligned field looks read-only and has a small hit area.
Replace it with a labelled full-width rounded textbox, explicit keyboard focus,
no autocorrection and automatic model-field focus after provider selection. Keep
provider/key/current model unchanged during validation. Rebuild the same app and
verify typing and persistence through its UI. The real OpenAI summary attempt failed
with only a generic error and was not retried; do not mark summary acceptance done.

### Keyboard routed to background app — 2026-10-26

User clarified that the apparent focused field sends physical typing to the app
behind ScribeLoop. This is a window-activation defect, not merely an input border.
Keep the clearer Model ID textbox, explicitly establish regular-app activation on
launch/reopen, and make user-opened notebook/preferences/people windows key when
they attach. Do not activate automatic call-review panels or steal focus on backend
updates. Verify actual active/key-window state and native keyboard editing after
rebuild; AX-directed text insertion alone is insufficient evidence of focus.


Keyboard-focus acceptance: rebuilt the same production app with regular activation
policy and key-window activation on user-opened notebook/preferences/people views,
plus the outlined Model ID field. The user tested physical typing and confirmed
“Works now”. No provider/key change or API request was made for this focus check.
The real-UI test report is [MACOS_REAL_UI_TEST.md](MACOS_REAL_UI_TEST.md); it preserves
remaining summary-failure/live-latency findings instead of treating synthetic tests
as end-to-end acceptance.

### Teams chat notification false prompts — 2026-10-26

User reports Teams text chats/new-message alerts repeatedly suggest transcription.
Current detector allows output-only activity, which includes notification sounds.
Require sustained app microphone **and** output activity before suggesting a new
call (6 seconds in native discovery); keep call-end monitoring's any-direction
activity independent so muting does not prematurely stop a recording. Not now and
closing the prompt suppress the episode and apply a five-minute per-app quiet
period. Preserve episode/dismissal identities across unrelated installed-app/helper
registry changes; deduplicate shown prompt IDs in the native client. Manual recording
remains available for receive-only/muted calls that do not satisfy the stricter
heuristic. This is heuristic filtering, not an official Teams call-state API.
Test repeated chat sounds, direction changes, helper aggregation, dismissal/re-arm,
registry churn and native panel-close handling before rebuilding.

Teams prompt-filter validation: 436 Python tests and 15 frontend regression groups
pass. Native build/signature/dependency audit passes. Restarted production app
reports Watching for calls with no prompt at idle. Read-only HAL metadata check was
available/complete with no active Teams audio processes at this observation; actual
new-message arrival and a new real Teams call were not triggered by this test.
Synthetic regressions cover repeated output-only notifications, continuous duplex
debounce, mute retaining a call episode, five-minute dismissal quiet time and
registry/helper churn preserving IDs and dismissals. No chat was sent or read, no
recording/provider request occurred for this fix.

### Adaptive call-episode confirmation — 2026-10-26

User authorized replacing the fixed six-second gate. Track per-app healthy input/
output baselines and microphone-start edges. A newly observed microphone start,
with output present, requires two consecutive qualifying observations; already-
duplex activity seen at startup/recovery requires three. Browsers retain ambiguous
confidence and use three observations. Output-only alerts never qualify, and an
already-held microphone followed only by an output notification is not a new call.
Keep microphone-first starts armed while waiting for output, reset unconfirmed
samples on gaps/unknown observations, and preserve established IDs through mute.

A clear healthy idle interval ends the episode. A genuinely observed new microphone
start after that end can prompt promptly even inside the fallback quiet period;
unknown/startup reappearances remain suppressed. Not now/X suppress the whole current
episode. Keep installed-helper churn stable and retain native shown-ID deduplication.
No time-based start debounce or six-second gate remains. Idle end grace is separate
hysteresis, not an automatic Stop change. Cover fast starts, startup detection,
pre-call mic checks plus notification sounds, late output, gaps, mute, dismissal,
re-arm, registry churn, and source validation before rebuilding the native app.

Implemented adaptive confirmation and superseded the six-second start gate above.
Validation: 440 Python tests pass; 15 frontend replay groups pass; Ruff and diff
checks pass. Rebuilt `dist/macos/ScribeLoop.app`; bundle/signature/dependency audit
passes (91 native binaries, no external dependencies or bundled recordings).
Opened the rebuilt production app: Watching for calls, recording idle, no call
prompt. No Teams message was sent and no recording/provider call was made. A
physical Teams notification followed by a real call remains an acceptance test;
HAL activity is a heuristic, so these deterministic results do not prove every
Teams/browser combination detects correctly. App signing remains deferred.

### Gemini real-API compatibility repair — 2026-10-27

User selected and saved `gemini-3.5-flash-lite` after the previous configured model
returned 404. A real synthetic request with the chosen model returns Markdown-fenced
JSON and currently fails strict JSON parsing. Accept exactly one whole-response
`json` or unlabeled code fence before schema validation; retain size bounds, reject
surrounding prose/multiple blocks and preserve citation validation. Add regressions,
rebuild the production app, and retry notes via its UI on the existing test session.

Gemini repair validated: 24 summary tests and the production bundle audit pass.
After local Keychain approval, real Gemini notes were saved for the existing test
recording, revision 1; source link opens transcript and sidebar topic title updates.
Remaining observations: detail heading can retain the placeholder title, and ad-hoc
rebuilds plus repeated audio-status Keychain reads cause disruptive authorization.
Address stable signing/key access UX without disabling encrypted storage or widening
credential access. References: Apple TN3127 explains ad-hoc designated requirements
are specific to that code version.

### Record-switch Keychain prompts — 2026-10-27

Confirmed two implicit secret reads: RetainedAudio.status retrieves each session
key when loading history/details, and the native Audio tab automatically opens and
indexes encrypted audio. Change browsing status to policy/file metadata only;
`available` means retained ciphertext is present, not that its key is unlocked.
Actual playback/rerun continues to require and authenticate the key. Make native
playback load on explicit Play rather than tab appearance or record selection.
Preserve seek/speed controls after loading, fail closed for missing/revoked keys,
and test that browsing never calls the key backend. No plaintext fallback or
Keychain ACL change. Stable signing remains a separate development-build issue.

### Saved-recording batch retranscription — 2026-10-27

User requires whole-conversation processing for Transcribe again. Replace Deepgram's
saved `_batch` WebSocket replay with one prerecorded POST `/v1/listen` containing an
aligned multichannel PCM16 WAV. Preserve separate source channels, diarization,
Auto/fixed language, existing cost caps, single-dispatch/no automatic retry, and
atomic revision replacement. Authenticate complete ciphertext before dispatch;
construct upload incrementally in bounded memory with no plaintext disk file and
no real-time pacing. Live capture/resume retains its existing streaming path.

Expose saved processing state in the native client so controls do not pretend idle.
Allow read-only retained playback during a rerun of immutable audio, but continue
blocking capture/recovery/deletion conflicts. Playback loads only on Play (previous
record-switch fix). Report upload/wait stages, keep the previous transcript visible
until successful replacement, and recover normal audio controls after completion.
Validate batch request/channel layout, gaps, speaker/source attribution, errors,
request accounting, atomic rollback, and read-only playback during rerun. Then
rebuild and use the existing short test recording for one real batch API run.
Reference: https://developers.deepgram.com/docs/pre-recorded-audio and
https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded

Implemented record-switch and batch-rerun changes. Validation: 458 Python tests,
15 frontend groups, Ruff/diff checks, native playback smoke, production build and
bundle audit pass. Real UI batch rerun of the existing 33-second recording completed
as revision 3 and generated cited Gemini notes. Record switching no longer fetched
keys or prompted; explicit Play loaded and played the saved audio. See
MACOS_REAL_UI_TEST.md for scope and remaining physical-validation limitations.

### Five-minute prerecorded validation — 2026-10-27

User requested a 5-minute test. Use the pinned public AMI ES2004a 300–600-second
fixture and existing reference, with no private recordings. Run the production
DeepgramRecordingRunner batch path in an isolated encrypted workspace, using the
operator credential from the built bundle and the saved summary-provider settings.
Represent meeting audio plus a silent microphone channel, make one prerecorded
request and one bounded summary flow, and save elapsed timings, request accounting,
WER, speaker count, transcript and cited notes for direct inspection. Do not insert
fixtures into the user's meeting database. This is an API/pipeline benchmark, not
an automatic-call-detection or physical UI timing test. Refuse reused output paths
to prevent accidental repeated API spending; redact failures and credentials.

Five-minute test exposed a prerecorded timestamp edge: HTTP 200, duration 300.0,
with the final word starting at 299.92502 and ending at 300.08502. Strict live-style
bounds rejected all results. Add a batch-only 100 ms ending tolerance and clamp to
the actual supplied file boundary. Word starts must remain inside supplied audio;
larger overruns remain invalid. Keep live tolerance unchanged. Regress valid/clamped
and rejected cases, validate the captured public response offline, then perform a
fresh bounded batch/summary run. Preserve both failed attempt reports for accounting.

Five-minute validation complete: verified fresh run used one 19.2 MB prerecorded
upload, 58.55 s transcription plus 3.66 s Gemini notes (62.22 s combined), 44 segments,
4 speaker labels, 26.08% WER (199/763), and 100.3 MiB peak benchmark RSS. Two earlier
requests were rejected for the observed final-word timestamp overrun; the repaired
batch-only boundary policy passes regressions and the full 462-test suite. Production
app rebuilt with fix. No DER/native five-minute UI/live latency claim. Full evidence
and attempt accounting: MACOS_REAL_UI_TEST.md and the verified artifact directory.

### Native visual refresh and app icon — 2026-10-27

User requests a better-looking app while keeping the clean layout. Preserve the
sidebar, Notes/Transcript/Audio tabs and existing recording/provider flows. Apply
adaptive warm-neutral/charcoal surfaces, a muted green accent, consistent cards and
borders, stronger title/metadata hierarchy, quieter sidebar rows and status badges,
more readable transcript/speaker labels, polished citations and audio controls.
Keep system focus, accessibility labels and light/dark contrast; do not force an
appearance. Avoid extra navigation or decorative dashboards.

Create an editable vector speech-loop/waveform icon and deterministic macOS PNG/
ICNS assets, package CFBundleIconFile for production/preview and use the mark in
branding. Update bundle verification to check the packaged icon. Build, review
actual screenshots at the default size and check Notes/Transcript/Audio/Preferences
without making provider calls. Keep deferred release signing status explicit.

Visual refresh implemented. Production Swift compilation, 17 focused desktop tests,
Ruff/diff checks and bundle dependency/signature audit pass. The packaged icon is
visible in the rebuilt sidebar and empty/startup state; Info.plist points to the
verified ICNS. Updated the native CI compile list for Appearance.swift. Small-text
contrast checks for accent/warning/action colors exceed 4.5:1 in their tested light/
dark surface combinations. Reviewed actual startup and Preferences screenshots in
dark mode. User is actively editing Preferences; preserve that sheet and do not
change settings or interrupt key entry for remaining main-tab visual checks. Light
appearance was contrast-checked, not switched globally. No provider/capture calls
were made for this visual work. Editable icon and regeneration instructions are in
shell/macos/Assets. Stable release signing remains deferred.

### Preferences simplification — 2026-10-27

User requests removing the Encrypted workspace section, separate Save button and
Remove saved summary key switch. Done saves changed provider/model/key/language/
duration settings and dismisses only after the accepted receipt; failures retain
the draft and show an inline error. Unchanged Done closes without Keychain writes.
Expose only a saved-key preview (first four characters plus a fixed mask; keys of
four or fewer characters remain fully masked). The preview is a placeholder, never
an editable/persisted fake key, and is hidden for a different endpoint. Retain secure
replacement entry. Arrange microphone and system-audio permissions as clear rows
with a trailing action, and call detection as a switch. Preserve current settings
before rebuilding; no provider requests or permission grants are needed to test it.

Preferences simplification implemented and packaged. Production native compilation,
19 focused desktop/fixture tests, masked-key persistence tests, Ruff/diff checks and
bundle audit pass. Actual UI shows the saved-key preview and Saved indicator, no
workspace/Save/key-removal controls, two permission rows and detection switch. User
was actively interacting with the settings form, so attempted Done interaction was
safely rejected by computer control; leave the current form/draft intact. No provider
configuration was changed for verification and no API/capture calls were made.

### Conversation rename/delete controls — 2026-10-27

Expose Rename and Delete in sidebar context menus and the selected conversation's
Meeting menu. Rename uses a focused name dialog, validates a nonempty single-line
name up to 160 characters, waits for a durable accepted receipt, and preserves
manual names across future summaries/reruns. Use a dedicated metadata-only event so
renaming a completed meeting never triggers automatic summary/provider work.
Reuse existing key-first permanent deletion and confirmation; context actions target
the clicked conversation, not an unrelated selection. After deleting the selected
row, move to an adjacent record. Keep active/uncertain operations protected, and
validate storage/API persistence and manual-title precedence with synthetic data.
UI validation must cancel deletion, never remove an existing user conversation.

Conversation management implemented and packaged. 477 Python tests, native build,
Ruff/diff checks and bundle audit pass. Regressions cover Vietnamese/manual title
persistence, malformed names, idle-state checks, receipt idempotence/reconnect and
ensuring rename never dispatches provider work. Existing deletion authorization/
confirmation tests pass. Final running-app menu/dialog inspection is pending local
Keychain approval after the rebuild; startup timed out and offers Retry core. No
existing conversation was renamed/deleted during validation and no provider call ran.

### Preferences outside-click focus — 2026-10-27

User reports that clicking outside a settings field does not unfocus it. Use one
optional FocusState for endpoint/model/key fields, and a local mouse monitor scoped
to the Preferences window. When a click is outside the active field editor bounds,
clear SwiftUI focus and end native editing before delivering the original event.
Do not swallow clicks, inspect field text, install global input monitoring, or
remove window activation needed for physical keyboard focus. Remove the monitor
when the sheet closes. Verify model/key focus, outside clicks and ordinary controls
in the rebuilt UI; preserve all settings and avoid provider requests.

Outside-click focus fix installed by updating only the native UI executable and
re-signing the outer bundle, preserving the existing frozen core. Native compile,
bundle audit and diff checks pass. Actual UI verifies Model ID/key field blur,
physical picker click-through and unchanged Done dismissal. Conversation context
menu Rename and Delete confirmation were also inspected and cancelled; all four
saved records remain intact. No settings or provider state changed during testing.

### Cancel and outside-modal discard — 2026-10-27

User requests recovery from accidental settings edits. Add Cancel/Escape to discard
the draft; Done/Return is the explicit save. Clicking the parent window outside the
Preferences sheet dismisses without saving and consumes that click so underlying
recording controls do not activate. Clicking inside the modal outside a field still
only clears focus. Disable cancellation/dismissal during an explicitly dispatched
save so its outcome remains visible. Stage call-detection and login toggles too;
Cancel must not apply these draft settings. OS permission buttons remain explicit
immediate system actions. Unchanged Done avoids writes. Keep save receipts/error
handling, reset the next opened draft from saved values, rebuild native UI only,
and verify changed-model Cancel/Escape/outside/reopen without provider requests.

### Visible conversation actions — 2026-10-27

Follow-up: replace the sidebar's system selection accent with the notebook's green
palette for selected/focused conversation cards. Preserve native list selection,
keyboard navigation and card actions; inspect the focused selection in the app.

Add always-visible pencil and trash buttons to each sidebar conversation card.
Reuse the existing rename sheet and permanent-delete confirmation, targeting that
card regardless of selection. Provide accessible names and tooltips, keep actions
disabled while busy/disconnected, and verify both dialogs by cancelling them.

Implemented and installed in the development app. Native compilation, signature/
bundle audit and diff checks pass. Physical clicks on the visible pencil and trash
opened the correct rename and named delete-confirmation dialogs; both were cancelled
and all four records remain. Preferences Cancel, Escape and parent-window outside
click discard accidental Model ID edits; reopening restores the saved value. No
provider requests, settings saves or conversation changes occurred in these checks.

Conversation selection follow-up complete: focused cards now use adaptive green
fill/outline. Native list keyboard navigation is preserved and verified with Up
in the installed app. Native compilation and bundle/diff checks pass.

### Delete without unlocking audio — 2026-10-27

Deletion currently reads the session secret to check existence, then repeats secret
reads in the native deletion wrapper. Replace macOS existence checks with exact
service/account Security-framework metadata-only queries (no returned secret).
Add retryable delete-if-present and use it for audio cleanup; retain key-first order,
post-delete verification and fail-closed errors. Test missing keys, denied queries,
failed deletion and zero secret reads. Rebuild the frozen core for delivery.

Delete-without-unlock implemented. macOS checks exact service/account existence
without returning secret data before/after deletion; audio cleanup uses retryable
native delete-if-present. Denial/failure still blocks file removal. 480 Python tests,
Ruff, native/frozen build and bundle audit pass. Installed/reopened development app;
no existing conversation was deleted for validation. Real OS authorization behavior
still depends on Keychain lock/access state and development signing identity.

### Full app audit — 2026-10-27

Audit current native app and core before further delivery: full automated checks,
packaging/signature audit, settings drafts, conversation actions, audio/player,
speaker naming, recording/reprocessing and real cloud transcription/summary using
public or generated test audio. Keep existing user records unchanged. Inspect
failure/cancellation/privacy boundaries and fix concrete defects with regression
coverage. Record observed results, API use and physical acceptance limitations in
an audit report; distinguish tested behavior from untested OS/device scenarios.

Audit corrections: the preview builder calls the production builder and replaces
the production artifact before copying it. Build the fixture app directly at its
own destination, with its own freezer name/manifest and no operator key lookup.
Expose card action buttons as separate accessibility children, retaining native
selection. Verify preview rebuild leaves production executable/config unchanged.

The first-use text promises agreement revocation in Preferences, but that control
is missing. Restore a clear Stop cloud processing action with confirmation, reuse
the existing revoke command, discard settings drafts after successful revocation,
and allow this stop action through the client's uncertain-mutation guard. Verify in
the disposable fixture, never revoke the user's production agreement during audit.

Full app audit completed; see MACOS_APP_AUDIT_2026-10-27.md. Corrected preview build
isolation and missing native agreement revocation, and added named card accessibility
actions. Installed native changes; 481 tests, 15 frontend groups, native smoke with
revocation, Ruff/diff and bundle audit pass. Real public-audio Deepgram batch/live
and Gemini notes pass. OpenAI live validation is incomplete: minimal diagnostic
returned HTTP 429. Distribution credential protection, stable signing, VoiceOver,
and physical call/device/OS acceptance remain explicitly documented.

### Repeated playback unlocks — 2026-10-27

Every playback cursor currently rereads its session key, including metadata scans,
window prefetch, seeks and track changes. Retain successfully unlocked audio keys
in process memory for the app session, including newly created recording keys;
serialize acquisition so simultaneous streams do not prompt twice. Never persist
this cache or alter Keychain access controls. Evict before key deletion/expiry and
clear after workers stop at shutdown. Verify repeated playback uses one acquisition,
new recordings need no extra acquisition, failed unlocks are retried and deletion
cannot continue reading a cached key. An existing key's first OS authorization may
still be necessary after restarting the unsigned development core.

User follow-up requests one authorization for everything at first-use agreement.
Reuse all acquired audio secrets for the whole running core, with settings already
held in memory. Do not claim the agreement grants OS Keychain trust: the database
must be unlocked before the saved agreement is readable, and existing per-record
items may each require their first OS authorization. Permanent cross-launch prompt
elimination requires stable signed credential access or an explicit key-storage
migration; neither can be achieved by a UI consent flag. Preserve encrypted storage
and document this remaining limitation rather than bypassing system protections.

Session-wide audio-key reuse implemented and packaged. 485 tests pass; final focused
shutdown/playback suite passes (33 tests), Ruff/diff and production bundle audit
pass. New captures require no key reread; cold repeated playback/track/window reads
share one acquisition; concurrent readers serialize; failed unlock is not cached;
delete evicts cached keys. Installed/reopened production app. No provider calls or
saved recording changes were made. The requested permanent one-time authorization
across restarts is not completed: existing per-record OS permissions and ad-hoc
signing remain limitations, explicitly distinguished from app terms acceptance.

### Business demonstration and editable summary instructions — 2026-10-27

Add an optional, bounded Summary instructions text editor to Preferences, with the
same draft/Cancel/Done lifecycle as other settings. Persist in secure settings and
apply to section notes and final reductions; include it in node cache identities.
Instructions guide focus/style/detail without relaxing JSON/citation/grounding
validation. Empty restores built-in guidance; apply to future generations only.
Create a clearly synthetic multi-speaker business discussion with budget, disputed
proposals, decisions, named owners, explicit dates and open questions. Generate
local speech, run one real cloud batch transcription and cited summary, and save
playable audio, transcript, notes and measurements for direct inspection. Never
upload private meetings or change the user's saved provider/instruction preference.

Summary instructions implemented: bounded secure setting, multiline native editor,
draft cancellation/Done, instructions applied to all section/reduction requests and
cache fingerprints. 487 tests pass, including Unicode setting roundtrip/clear/bounds
and hierarchical prompt/cache changes; build, Ruff/diff and bundle audit pass.
Completed synthetic business demo: 256.02 seconds, three anonymous speaker labels,
one ASR upload (39.12 s), one Gemini summary (4.76 s), script WER 13.93%. Cited notes
retain conditional launch, budget decisions, four owners/deadlines and unresolved
risks. Raw ASR misspells Priya Shah and propagates that into one action owner; the
review explicitly records it without silently correcting output. Playable HTML,
script/transcript, notes and metrics saved under data/demos/business-pilot-20261027.
No private recordings uploaded or saved provider/instruction settings changed.

### Visible default summary guidance — 2026-10-27

When no custom summary instructions are saved, populate the native editor with
the built-in editorial guidance instead of an empty box. Keep existing custom text,
and initialize the draft signature after this fallback so opening/unchanged Done
does not write settings. Clearing still uses default guidance on future generation.
Rebuild only native UI to preserve the current credential-bearing core.

Visible-default guidance implemented and installed using native-only replacement,
preserving the existing core. Native compilation, bundle/signature audit and diff
checks pass. Default text is a draft fallback and does not overwrite custom settings
or cause unchanged Done to persist a new value. No provider call was required.
