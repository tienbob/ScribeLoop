# Phase 0a capture validation — 2026-10-22

Status: local capture implementation delivered; native validation deferred.
See [Phase 0 handoff](PHASE_0_CLOSEOUT.md). Phase 0b remains closed.

## Completed implementation

- [x] Add `CaptureSilenceMonitor` as a reusable Python consumer-side component.
- [x] Emit one `prolonged_silence` event after five continuous seconds below
  -70 dBFS RMS, followed by `signal_recovered` when signal returns.
- [x] Count actual received samples, including partial frames. Timestamp gaps
  reset silence accumulation; invalid/backwards frames fail without changing state.
- [x] Add a bounded WAV diagnostic command and unit coverage. Full suite: 50 pass.
- [x] Replay the existing ES2004a 300–600 s clip: no prolonged-silence alerts.

```bash
.venv/bin/python scripts/check_capture.py \
  --wav data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav
```

The command accepts at most five minutes; no ML models or downloads are required.
The RMS threshold and silence duration are configurable and have not been tuned
on real device capture. A silent recording can be intentional: the event is a
prompt to check expected playback, source selection, and system-audio permission,
not a diagnosis of denied permission. The monitor retains no PCM. Use one monitor
per stream on a consumer thread, never inside a native realtime audio callback.

## Next implementation and host validation

- [x] Implement and compile ScreenCaptureKit fallback helper and framed Python transport.
- [x] Exercise partial packets, invalid data, timestamps, stderr errors, and child cleanup.
- [ ] Implement the preferred Core Audio process-tap helper.
- [x] Wire silence/recovery events into the native diagnostic consumer.
- [ ] Connect events to the future product recording UI.
- [x] Detect missing/stalled native packets via a configurable transport timeout.
- [ ] Verify prompt attribution and PCM capture from a signed/notarized app bundle
  (signing explicitly deferred by user; no valid local identity installed).
- [ ] Validate intentional silence, denied permission, source changes, and recovery
  on the actual macOS host; do not infer native permission behavior from WAV replay.
- [ ] Resolve remaining Phase 0a backend/packaging decisions and second-OS checks
  from the architecture roadmap.

The signed-build exit criterion remains unchecked. This work completes the silence
monitor component, not native capture or the whole Phase 0a spike.

Native capture attempt returned macOS TCC permission denial before successful
audio delivery. No real-capture pass is claimed. New transport tests bring the
full suite to **62 passing tests**; helper build and import smoke also pass.
