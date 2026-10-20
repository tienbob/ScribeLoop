# shell/ — Tauri v2 app (placeholder)

Rust shell + webview UI, with the Python core as a managed sidecar over a
local WebSocket (first-message token auth — §6).

**Status: placeholder.** The Tauri-vs-pure-Python decision is a Phase 0a
packaging-spike deliverable (§8 0a) — including GStreamer/PyGObject inside
AppImage/deb and llama-cpp-python wheel availability. This directory gets
scaffolded when that decision lands.

If Tauri wins: `npm create tauri-app`, Rust shell manages the core sidecar
process, passes the per-launch token out-of-band, and enforces a strict CSP
(§6 "the UI is an attack surface").
