# Internal Mac distribution

Updated October 29, 2026. Internal build **0.1.3 (4)** targets **Apple Silicon
(M1 and newer), macOS 13 Ventura or later**. This is the build deployment target,
verified against native dependency metadata. Runtime acceptance on an actual
Ventura Mac remains required; the development host runs macOS 27.2.

Microphone and selected-app/system audio capture use the macOS 13 APIs. Automatic
call detection uses macOS 14.2 audio-activity metadata; on 13–14.1 use **New
recording** manually. macOS 11–12 and Intel are outside the selected release scope.

## What testers install

Send the DMG, its SHA-256 file and the included [install instructions](../packaging/macos/INTERNAL_INSTALL.txt)
through your private internal channel. The image contains ScribeLoop.app, an
Applications shortcut and READ ME.txt. Testers drag the app into Applications,
eject the image and launch from Applications. Python, Homebrew and Xcode are not
required on their Macs. Each Mac creates a separate encrypted workspace.

In Preferences, testers supply their own Deepgram transcription key and choose
their notes provider/model. Cloud notes use their own provider key; Ollama and
LM Studio connect to an already-running compatible local server. Keys remain in
their Mac's Keychain. Provider charges belong to their account. Fully self-hosted
ASR is not implemented in this build. The app does not contain the developer's
shared transcription credential, recordings or settings.

Microphone and Screen Recording permissions are granted by each tester when
needed. Recording is an explicit choice; launching the app does not play audio.
Testing on the development machine must preserve the user's speaker-off rule.

## Build and inspect

Use a portable CPython 3.11 with a native minimum OS no newer than macOS 13.
The installed python-build-standalone interpreter used here declares macOS 11;
do not use the host-built Homebrew interpreter, whose binaries require macOS 27.
Create `build/macos/venv13` with that interpreter and install
`packaging/macos/requirements-desktop.txt`. NumPy is pinned to 1.26.4 because the
previous wheel required macOS 14.

Rebuild sqlite-vec **at the existing pinned version 0.1.9** before freezing:

```sh
git clone --depth 1 --branch v0.1.9 https://github.com/asg017/sqlite-vec.git build/macos/sqlite-vec-src
build/macos/venv13/bin/python packaging/macos/prepare_compat13.py
```

The preparer verifies source commit `e9f598abfa0c06b328d8fe5da9c3760cce74be10`,
compiles for arm64/macOS 13 with M1-compatible instructions, installs only into
the isolated venv, then checks extension loading and nearest-neighbor queries.
`--sqlite-headers` accepts upstream SQLite/SQLCipher headers; the default uses the
existing Homebrew SQLCipher include directory. Its libraries are not linked.
Build provenance/header hash is saved in `build/macos/compat13/sqlite-vec-build.json`.
Apple SDK's SQLite headers disable loadable extensions, so they are unsuitable.
A dependency reinstall may replace the rebuilt extension; the final packaging
check rejects any native binary above the declared OS floor.

```sh
build/macos/venv13/bin/python packaging/macos/build_desktop.py
.venv/bin/python packaging/macos/verify_desktop.py
.venv/bin/pytest -q
```

The verifier rejects the old `transcription.json` credential resource and checks
native dependencies, their minimum OS versions and signatures. Packaging additionally checks every Mach-O
architecture and rejects bundled runtime data. Do not distribute an older app
artifact containing the operator credential.

## Ad-hoc internal test image

```sh
.venv/bin/python packaging/macos/package_internal.py --unsigned-internal
```

Output: `dist/macos/internal/ScribeLoop-0.1.3-4-arm64-internal-unsigned.dmg`, plus
JSON metadata and `.sha256`. The filename and included instructions explicitly
identify this as an ad-hoc, unnotarized test build. This command does not upload
anything. It packages a copy and never changes the source app or user data.
An existing output is not overwritten; use a fresh `--output` directory for a
new trial or increment version/build for a new release.

macOS may block an unnotarized download. A tester or IT can review Apple's
[app-specific Open Anyway instructions](https://support.apple.com/102445).
Managed Macs may disallow exceptions. Do not disable Gatekeeper or strip
quarantine. Prefer the signed path below for routine internal installation.

## Developer ID release path

No valid signing identity was installed on the development Mac when this work
began. An Apple Developer account holder must install a **Developer ID Application**
certificate and its private key, and configure a `notarytool` Keychain profile.
Account enrollment, agreements, credential entry and certificate provisioning
must be completed by the account owner; no signing credentials belong in Git.

After that setup:

```sh
security find-identity -v -p codesigning
.venv/bin/python packaging/macos/package_internal.py \
  --identity 'Developer ID Application: YOUR ORGANIZATION (TEAMID)' \
  --notary-profile ScribeLoop-notary
```

The script signs nested native binaries/frameworks before the app, uses hardened
runtime and the audio-input entitlement, submits a ZIP to Apple's notary service,
staples and checks the app, creates the DMG, signs/notarizes/staples the DMG, and
checks Gatekeeper. It emits a signed release only after those checks pass.
This path uploads the distributable app to Apple; it contains no workspace data.
No broad hardened-runtime exceptions are enabled preemptively. Actual signed
runtime compatibility and notarization still need validation with the real
identity; the ad-hoc package does not establish them.

Apple references: [Developer ID](https://developer.apple.com/developer-id/),
[notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow),
[hardened runtime](https://help.apple.com/xcode/mac/current/en.lproj/devf87a2ac8f.html).

## Internal rollout acceptance

- Install the downloaded image on a second Mac with a fresh user/workspace;
  verify first launch, Keychain setup, permissions and a consented recording.
- Test supplied API keys without exposing them in snapshots or errors; verify
  blank/unset keys prevent transcription and removal persists after restart.
- Verify microphone quality, selected-app audio, interruptions and recovery.
- Quit and replace the app with a subsequent build; verify recordings, notes,
  settings and Keychain access survive. No database downgrade is supported.
- Measure cold startup and repeat it: the previous development build had
  unexplained startup timeouts before a successful retry. That is still open.
- Confirm actual supported macOS versions on testers' machines. The current
  explicit macOS 13 target does not substitute for runtime testing on Ventura.

No second-Mac physical acceptance, signed upgrade or real-call compatibility
claim follows from generated tests. See [current status](CURRENT_STATUS.md).

## Verification on the development Mac — October 29

Version 0.1.3 adds the [recording Stop fix](RECORDING_STOP_FIX_2026-10-29.md)
and supersedes the 0.1.2 installer. The compatibility measurements below describe
the macOS 13 runtime transition in 0.1.2; 0.1.3 repeats the build and bundle checks.

- **539 Python tests passed** in the development environment. The portable
  Python 3.11 shipping environment passed **536 tests**, with the optional
  `pyannote.metrics` module skipped (its three tests are outside this cloud build).
- SwiftUI client and both helpers compiled with explicit macOS 13 targets;
  the CI workflow now compiles production code/helpers at that target too.
- Production rebuild and bundle verification passed: **38 native binaries**,
  comprising 34 with macOS 11.0 minimums and four with macOS 13.0 minimums.
  No external native dependencies or build-machine RPATHs remain. No operator
  credential or recordings are bundled. Ruff and whitespace checks passed.
- The macOS 14-only sqlite-vec wheel was replaced with a same-version source
  build. NumPy 1.26.4's unused external Fortran RPATHs were removed before signing;
  the copied Fortran libraries remain inside the bundle. Build and packaging both
  reject binaries requiring a newer OS than Info.plist declares.
- Final ad-hoc DMG creation, image verification and SHA-256 checks passed.
  Its read-only mount contained the Applications shortcut, labelled instructions
  and all **114 app files** matching the source bundle byte-for-byte. The image
  is **56,664,062 bytes**. Use version 0.1.2, not the previous 27.2-only image.
- Local credential-value scan found no matches in the app bundle.
- Rebuilt app connected on the observed host launch and showed both existing
  saved conversations. No recording or saved audio playback was triggered.
- No speaker output, provider request or Apple notary submission was performed.
  Physical Ventura capture, clean installation, signed upgrade and cold-start
  reliability remain acceptance work; metadata checks do not establish them.

Local build evidence is under `build/macos/`: `desktop-audit.json`,
`compat13-native-report.json`, `compat13-installer-verification.json`,
`compat13/sqlite-vec-build.json` and `tests13.log` (all generated/gitignored).
