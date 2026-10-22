# Phase 0 local-development closeout — 2026-10-22

The local implementation checkpoint is ready for handoff to Phase 1. This is not
an assertion that the original Phase 0 exit criteria have passed. The user
explicitly deferred signing; after the user enabled Screen Recording permission,
native capture succeeded on both silence and controlled-tone checks. Phase 0b's earlier scoped closeout remains unchanged.

## Working defaults and implementation decisions

| Area | Development decision | Evidence and limitation |
| --- | --- | --- |
| Batch ASR | MOSS float16 + SDPA, original audio, no quantization | Five-minute WER 20.31%, 82.93 s processing on MPS; benchmark adapter, not product integration |
| Live ASR | Keep Whisper-family chunked decoding as the Phase 1 starting point; do not promote Sherpa on speed alone | Whisper-tiny 34.10% vs Sherpa 57.85% exploratory whole-meeting WER; neither proves the live accuracy/latency targets. LocalAgreement remains to implement |
| Offline speaker turns | Community-1 candidate | Five-minute diagnostic DER 27.57% original / 27.94% Opus; no validated hybrid word attribution |
| Delayed live speaker labels | Choose near-online chunked community-1 for the first Phase 1 implementation | Reuses the locally working pipeline and embedding family. Avoid adding diart compatibility dependencies now. Stable cross-chunk identities and delay still need implementation/measurement |
| macOS development capture | Implemented ScreenCaptureKit fallback through a native helper | Compiles locally; pipe, timeout, errors, timestamps, silence events, and cleanup tested. Silence and controlled-tone capture passed after enabling TCC permission. Core Audio process taps remain the preferred future path |
| v0 host | Start with the pure-Python FastAPI/browser fallback; keep native capture separate | Existing Python environment is 1.3 GB, MOSS weights about 1.8 GB; adding Tauri does not remove ML payload. This is a development scope decision, not a frozen-installer size measurement |
| LLM provider for initial integration | Use the existing OpenAI-compatible external local-provider interface | Embedded llama-cpp-python wheels and memory coexistence are not validated. External Ollama memory must be budgeted separately |
| Storage | SQLite + sqlite-vec; SQLCipher coexistence verified | Delete-scrub verification passed; full encrypted application lifecycle remains Phase 1 |
| Overlap separation | Rejected tested SepFormer implementation | Worsened short-clip recognition; code, runtime, weights, and output artifacts removed |

No new model downloads or full-meeting experiments are needed to start Phase 1.
Five-minute development bounds remain. Existing benchmark alternatives are
experimental controls, not supported product configurations.

## Native capture delivered

- Swift ScreenCaptureKit helper, system-wide or selected running bundle IDs.
- Audio only: no screen frames or microphone buffers are requested as outputs.
- Validated framed 16 kHz mono Float32 pipe with capture presentation timestamps.
- Consumer-side silence/recovery events and stalled/failed helper detection.
- Bounded diagnostic with explicit capture authorization, no audio saved.

```bash
.venv/bin/python scripts/build_capture.py
.venv/bin/python scripts/check_native_capture.py --consent-confirmed --duration-s 5
```

The build passed locally. The initial native attempt returned a TCC denial. After
the user enabled permission, the five-second check delivered 5.0 s of silent PCM
and emitted `prolonged_silence` at 5000 ms. An eight-second retry with two seconds
of controlled 440 Hz tone delivered nonzero PCM: RMS 0.00156436, peak 0.00450415,
wall time 8.20 s. No captured audio was saved; the generated playback WAV was
removed. Aggregate evidence is local in `data/native/capture-validation-tone.json`.
This verifies basic native signal delivery, not calibrated fidelity or signed-app
permission attribution. ScreenCaptureKit needs
Screen Recording permission; it is not the narrower process-tap permission.
The helper is a development executable, not a signed/notarized distribution.

Protocol and implementation use Apple's [audio stream output](https://developer.apple.com/documentation/screencapturekit/scstreamoutput)
and [audio channel configuration](https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration/channelcount).

## Explicit deferred gates

- Developer ID signing, notarization, responsible-app prompt attribution, and
  full host permission-denial/recovery/source-change validation. Basic silent and
  nonzero signal delivery now pass.
  No valid signing identity is installed; signing was deferred by the user.
- Linux capture and packaging checks on a real second-OS host. The CI matrix is
  configured, but no new remote CI or second-OS run is claimed in this closeout.
- Frozen installer spike, Linux GStreamer/PyGObject distribution, and embedded
  llama-cpp-python wheel availability. The browser/Python starting point avoids
  blocking local Phase 1 on these distribution decisions.
- Complete consented meeting corpus, VoxConverse/codec coverage, validated release
  scoring, paced live partial/confirmed latency and resource baselines, and §7
  target sign-off. Existing throughput timings are not live-latency measurements.

These gates remain required before claiming the original Phase 0 exit criteria or
release readiness. None has been checked off on the strength of a mock test.

## Verification and next handoff

Local suite: **62 tests pass**; new capture tooling passes Ruff; full dependency
import smoke passes; Swift helper builds and its `--version` check passes.
The import smoke emits duplicate FFmpeg/AVFoundation class warnings from installed
native libraries; imports complete, but native distribution must isolate libraries.
No signed, Linux, or live-transcript performance result follows from these checks.

Next: Phase 1 vertical slice using bounded WAV replay first: capture/source → VAD →
ASR → persisted session/segments → authenticated transcript UI. Keep first-message
auth, consent state, and encryption ahead of collecting real meeting recordings.
Native capture can be connected once macOS permission is enabled. Cross-chunk
speaker identity and speaker-to-word attribution need their own verification.
