# Ubuntu desktop preview validation

Implemented on `feat/ubuntu-22-desktop`. Install/build instructions and primary
research sources are in [Linux packaging](../packaging/linux/README.md).

The preview uses a native GTK 3 shell with the same Notes / Transcript / Audio
layout as the macOS shell. It reuses the same core, encrypted storage and local
loopback backend. The first native pass covers conversation history, live
transcript, notes, retained-audio playback, provider settings, the recording
dialog and the first-use agreement. macOS-only shell integrations such as
Calendar and Email sharing, per-application capture and automatic call detection
are not included. GStreamer pulls explicit microphone and default-output-monitor
sources through PulseAudio, including the protocol used by PipeWire's Pulse
server.

Validation on 2026-10-30:

- Local repository suite: 633 passed; frontend regressions and Ruff passed.
- Ubuntu 22.04 ARM64 / Python 3.10: 629 passed, 2 platform-dependent skips.
- Ubuntu 24.04 ARM64 / Python 3.12: 629 passed, 2 platform-dependent skips.
- Installers built separately for 22.04 and 24.04, each on amd64 and arm64.
- Installed GTK checks use disposable Xvfb, DBus, Secret Service and PulseAudio
  sessions, with no provider requests or personal workspace/keyring access.
  They test generated tone on microphone and output-monitor tracks, authenticated
  notebook connection, encrypted database creation, export through the native
  Save dialog, and owned-backend shutdown.
- CI repeats packaging, installed checks and the core suite across both releases
  and architectures. The workflow is checked in; no remote workflow was triggered.

Compatibility fixes discovered by running on the baseline:

- Use a shared timeout context for Python 3.10 and newer, including test helpers.
- Use sqlite-vec's explicit `k` constraint; Ubuntu 22.04's system SQLite cannot
  supply a LIMIT constraint to the vector virtual table.
- Use the current `sqlcipher3` wheel with SQLCipher 4 and ARM64 support.
- The shell is native GTK 3; it intentionally does not use WebKit. Blob exports
  use the shell's native Save dialog and owned backend.

Artifacts are under `dist/linux/`, with `SHA256SUMS`. Packages include exact
resolved dependency versions under `/usr/share/doc/scribeloop/`.

Physical microphone/output alignment, acoustic echo, device removal,
suspend/resume, long recordings, cloud provider acceptance, Wayland, and a real
PipeWire session still require host acceptance. Newer Ubuntu releases require
a build and validation for their own Python ABI; these results cover 22.04 and
24.04. This is an internal preview, not a production Linux release.

## 2026-11-06 integration

The Ubuntu preview was integrated with the current 0.1.45 core, including
bounded web reads, cleanup-provider resilience and Ollama lifecycle changes.
The Linux package now uses the core version in `pyproject.toml` plus the same
build number as the macOS bundle, so its artifacts are
`0.1.45+46+ubuntu<release>_<architecture>.deb`. This aligns the release label
without making the Linux shell feature-identical to macOS.

Validation for this integration:

- Local Python suite: **815 passed**.
- Frontend replay suite: passed.
- Ruff: passed.
- Ubuntu 22.04 amd64 and 24.04 amd64 disposable installed smoke checks passed:
  generated microphone and system tracks, native GTK notebook window, encrypted
  workspace creation and owned shutdown. The installed smoke harness now runs
  both its capture and desktop checks explicitly.
- Packages were rebuilt for Ubuntu 22.04 and 24.04 on amd64 and arm64 after the
  native-shell command reconciliation. `SHA256SUMS` covers the four packages.

