# Desktop shell — deferred for local v0

Phase 0 local-development decision (2026-10-22): start with the existing Python
FastAPI core and a browser UI. Tauri scaffolding is deferred; no Rust/TS shell
or distribution artifact is implemented here. The default Groq backend avoids bundled local speech models; optional local ML experiments remain large. Installer measurements are still deferred.

The browser UI must retain loopback-only binding, first-message token auth,
strict CSP, and consent state. Native capture remains a separate Swift helper.
Signing and notarized responsible-app permission attribution remain required
before distribution. See [Phase 0 closeout](../docs/PHASE_0_CLOSEOUT.md).
