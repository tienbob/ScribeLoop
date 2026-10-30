# Ubuntu desktop preview validation

Implemented on `feat/ubuntu-22-desktop`. Install/build instructions and primary
research sources are in [Linux packaging](../packaging/linux/README.md).

The preview shares the core and browser notebook with a GTK 3 / WebKitGTK desktop
host. GStreamer pulls explicit microphone and default-output-monitor sources
through PulseAudio, including the protocol used by PipeWire's Pulse server.
There is no automatic call detection or per-application audio capture on Linux.

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
- Route notebook Blob exports to WebKit downloads and a native Save dialog.

Artifacts are under `dist/linux/`, with `SHA256SUMS`. Packages include exact
resolved dependency versions under `/usr/share/doc/scribeloop/`.

Physical microphone/output alignment, acoustic echo, device removal,
suspend/resume, long recordings, cloud provider acceptance, Wayland, and a real
PipeWire session still require host acceptance. Newer Ubuntu releases require
a build and validation for their own Python ABI; these results cover 22.04 and
24.04. This is an internal preview, not a production Linux release.
