# M0 native fixture app

This is a host-only SwiftUI packaging spike, not the production meeting app.
It creates temporary encrypted synthetic data with memory-only keys. It never
loads `.env`, production recordings, provider configuration or Keychain keys.
No microphone/Screen Recording permission is requested. No provider calls occur.
The menu bar remains available after closing the notebook window.

## Build on this Apple Silicon Mac

Run from the repository root with Xcode installed:

```sh
uv venv --python .venv/bin/python build/macos/venv
CFLAGS=-I/opt/homebrew/opt/sqlcipher/include/sqlcipher \
LDFLAGS=-L/opt/homebrew/opt/sqlcipher/lib \
uv pip install --python build/macos/venv/bin/python -r packaging/macos/requirements-m0.txt
build/macos/venv/bin/python packaging/macos/build_m0.py
open 'dist/macos/ScribeLoop M0.app'
```

The builder selects `/Applications/Xcode.app/Contents/Developer` for its commands,
leaving global xcode-select unchanged. It compiles the checked-in Swift sources
with Xcode's compiler, freezes a separate Python environment, bundles the native
meeting/observer helpers, and ad-hoc signs the resulting app. Helpers are packaged
but not invoked for capture. Generated artifacts are ignored by Git. Resolved
package versions and a build manifest are in `build/macos/`.

## Verify

```sh
'dist/macos/ScribeLoop M0.app/Contents/MacOS/ScribeLoopM0' --m0-smoke
.venv/bin/python packaging/macos/verify_m0.py
.venv/bin/pytest -q tests/test_desktop_fixture.py
```

The native smoke uses the same supervisor as the window: private stdin bootstrap,
random token, backend-selected loopback port, authenticated WebSocket snapshot,
reconnect, then owned shutdown. Startup and shutdown have deadlines. Backend
bootstrap is bounded and times out; EOF also shuts down the backend. The fixture
rejects external socket connections and recording commands. Lifecycle verification
checks Mach-O dependency paths and explicit STOP/EOF termination.

Validated on macOS 27.0.1 / arm64 with Xcode 27.1: 412 tests pass, native smoke
passes (~0.64 s), 91 native binaries have no unbundled non-system dependencies,
and STOP/EOF pass. Bundle size is approximately 80 MB. Native app inspection
confirmed Connected, generated transcript and four verified library/storage checks.

The bundle's minimum OS is conservatively the build host version. macOS 14
compatibility, Developer ID/notarization, TCC identity, installer/update behavior,
production data/settings adoption and live capture remain unvalidated. Signing
was deferred by the user. M1 will integrate the real core; this fixture does not
mark production desktop implementation complete.
