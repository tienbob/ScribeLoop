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

Implemented fallback: `macos_sck/main.swift`. Build with
`python scripts/build_capture.py` from the repo root; then run
`python scripts/check_native_capture.py --consent-confirmed --duration-s 5`.
No audio is saved by the diagnostic. Basic native silence and controlled-tone capture pass after enabling
macOS TCC permission on the development host. Signed attribution remains unverified. Core Audio taps are still unimplemented.

The helper emits `SLP1`, little-endian uint32 sample count, little-endian float64
presentation timestamp in seconds, then mono float32 little-endian PCM at 16 kHz.
The consumer rejects invalid/oversized packets, nonfinite samples, reversed
timestamps, missing data, and helper errors. It preserves gaps and stops the
helper on timeout/close. Pipe writes run on the ScreenCaptureKit output queue,
not an IOProc realtime callback. No screen-output or microphone delegate is added.

License: proprietary (see repo root). See ARCHITECTURE.md §2/§9 for the full
rationale and permission model.
