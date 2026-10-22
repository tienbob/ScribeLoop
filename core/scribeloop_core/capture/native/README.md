# Native capture helpers

macOS capture lives here because taps deliver audio via an **IOProc C callback
on a realtime thread** — Python's GIL/GC must not sit there (ARCHITECTURE.md
§2.3). Each helper is a tiny Swift CLI that:

1. creates the tap (`AudioHardwareCreateProcessTap` + `CATapDescription`)
   or falls back to ScreenCaptureKit,
2. runs the aggregate device + IOProc on its own thread,
3. writes 16 kHz mono float32 PCM to stdout (the Python core reads the pipe).

## macos_tap/ — Core Audio process tap (preferred, macOS 14.2+)

- `NSAudioCaptureUsageDescription` **must live in the Tauri bundle** — the
  bundle macOS treats as the responsible process, not this bare CLI.
- No public preflight API: detect silent frames from the Python side and
  guide the user to grant the "system audio recording" permission.

Python-side silence diagnostics are implemented in `capture/health.py`;
`scripts/check_capture.py` exercises them on bounded WAV replay. Wire the monitor
into the future capture consumer. This does not validate native permission behavior;
see [Phase 0a status](../../../../docs/PHASE_0A.md).

## macos_sck/ — ScreenCaptureKit fallback (macOS 13.x)

- Requires Screen Recording permission; macOS 15 re-prompts periodically.

Build (Phase 0a, on a macOS host): `swiftc -O -o tap-helper *.swift`

License: proprietary (see repo root). See ARCHITECTURE.md §2/§9 for the full
rationale and permission model.
