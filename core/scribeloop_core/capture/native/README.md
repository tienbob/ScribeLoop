# Native capture helpers

macOS capture lives here because taps deliver audio via an **IOProc C callback
on a realtime thread** — Python's GIL/GC must not sit there (ARCHITECTURE.md
§2.3). Each helper is a tiny Swift CLI that:

1. uses ScreenCaptureKit for the implemented fallback (process taps and their
   aggregate-device/IOProc path are still planned),
2. runs capture outside Python's interpreter,
3. writes 16 kHz mono float32 PCM to stdout (the Python core reads the pipe).

## macos_tap/ — Core Audio process tap (preferred, macOS 14.2+)

- No helper exists here yet. Future packaged permission attribution must be
  verified on the responsible app bundle; Tauri is deferred for local v0.
- No public preflight API: detect silent frames from the Python side and
  guide the user to grant the "system audio recording" permission.

Python-side silence diagnostics are implemented in `capture/health.py`;
`scripts/check_capture.py` exercises them on bounded WAV replay. Wire the monitor
into the future capture consumer. This does not validate native permission behavior;
see [Phase 0a status](../../../../docs/PHASE_0A.md).

## macos_sck/ — ScreenCaptureKit fallback (macOS 13+)

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

## macos_observer/ — metadata-only discovery (macOS 14.2+)

Build with `python scripts/build_capture.py --kind observer`. This helper reads
Core Audio process bundle/PID/input/output activity metadata and emits a versioned
JSON snapshot. It never opens a tap, audio stream, or microphone. Missing metadata
is incomplete/unavailable, not proof of idle apps. Run the bounded diagnostic with
`python scripts/check_meeting_detection.py --duration-s 10`; see
[discovery status and limits](../../../../docs/MEETING_DETECTION.md).

## macos_meeting/ — separate mic/output and common host clock

Build with `python scripts/build_capture.py --kind meeting`. AVFoundation supplies
the microphone and SCK supplies explicitly scoped output. `--devices` reads
metadata only; capture modes require consent and existing microphone authorization.
The source is not connected to the UI recorder yet. SLC2/SLP2 framing preserves
separate tracks and shared host timestamps; `STOP\n` drains conversion tails and
finishes with SLE2. Full/closed pipes fail rather than blocking native capture.

`python scripts/check_meeting_capture.py` runs five-minute **generated signals**,
not hardware capture. See [native capture status and protocol](../../../../docs/NATIVE_MEETING_CAPTURE.md)
for supported formats, current denied mic access, and unverified physical gates.
