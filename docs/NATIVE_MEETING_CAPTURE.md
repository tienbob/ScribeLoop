# Native meeting capture — M3 implementation

Implemented **2026-10-22**: read-only microphone/running-app enumeration, native
AVFoundation mic capture, SCK meeting output, common host-clock transport,
resampling, bounded writes, and graceful drain. **Generated-signal checks pass;
physical capture/alignment and real Teams/Slack routing remain unverified.**
The API still accepts no recording command. M4 must connect encrypted recording
before real meetings are captured.

[M3 audit and fixes](NATIVE_CAPTURE_AUDIT.md) cover lifecycle races, resampling,
stream deadlines, metadata refresh, and source-selection regressions.

## Build and check without recording

```bash
.venv/bin/python scripts/build_capture.py --kind meeting
.venv/bin/python scripts/check_meeting_capture.py \
  --output data/phase1/native-stream-smoke.json
```

The diagnostic generates two five-minute 440 Hz streams in the native helper,
converts 48 kHz stereo and 44.1 kHz mono using full-second and irregular-sized chunks to 16 kHz mono float32, and checks the
real child-pipe parser. It opens no audio devices, requests no permission, and
writes no audio file. The optional report contains aggregate numbers only.
This is accelerated conversion/transport verification, not live capture, ASR
latency, physical alignment, or a model-speed benchmark.

`data/native/scribeloop-meeting --devices` returns microphone identities/names,
default status, native microphone authorization state, and running app bundle
identities. This mode does not create device inputs, sessions, or PCM streams.
The authenticated discovery service filters app metadata into known source
choices, includes actual microphone choices, and revalidates devices on selection.
Device listing does not prove the device works or grant recording consent.

## Native source behavior

- `MacOSMeetingAudioSource` requires a validated explicit app/system scope,
  microphone ID, and strict consent before constructing a capture command.
  Iterating its frames starts one helper with independent loopback/mic tracks.
- `MacOSMicrophoneSource` supports mic-only capture without SCK/display access.
  Its independent session origin must not be combined with legacy SLP1 output
  as if both streams shared a zero point. Use the unified source for meetings.
- The helper checks microphone authorization and refuses unauthorized capture;
  it never requests permission automatically. Permission acquisition and visible
  recorder lifecycle belong to M4. The host currently reports **denied**.
- SCK inclusion requires every selected app to exist; missing selections fail,
  never broaden into system output. Mic-only mode does not enumerate displays.
- Device disconnection, selected-default-mic changes, selected app exit,
  interruption, runtime failure, unsupported PCM, and clock discontinuity fail
  visibly. No automatic device/scope switch or source reopening is implemented.
- Mono/stereo linear PCM at 8–192 kHz is accepted and resampled through
  AVAudioConverter. Other formats fail explicitly. Each stream has separate
  conversion state. Converter-buffered output is timestamped by its emitted
  sample position; graceful Stop drains the tail instead of dropping it.

The helper uses each session's synchronization clock to convert presentation
timestamps to the host clock. Apple describes the timebases for
[AVCaptureSession](https://developer.apple.com/documentation/avfoundation/avcapturesession/synchronizationclock)
and [SCStream](https://developer.apple.com/documentation/screencapturekit/scstream/synchronizationclock).
The current native implementation compares source timestamps with its sample
cursor and stops if drift/discontinuity exceeds **2 ms**. This conservative
starting limit is uncalibrated; adaptive device-clock correction is not delivered.
Real-device testing may require revising it explicitly.

## SLC2 / SLP2 protocol

All integers are little-endian; PCM is mono float32 little-endian at 16 kHz.

| Message | Fields |
| --- | --- |
| Origin (12 bytes) | `SLC2`, uint64 monotonic host nanoseconds |
| Audio header (20 bytes) | `SLP2`, uint32 sample count, uint8 stream (0 loopback / 1 mic), three zero reserved bytes, uint64 first-sample host nanoseconds |
| Audio payload | `sample_count × 4` PCM bytes, count 1–16000 |
| Complete (20 bytes) | `SLE2`, count 0, stream 255, zero reserved bytes and timestamp |

`SessionClock` converts absolute native timestamps to session milliseconds,
retaining per-stream start offsets. Packet order between streams need not follow
timestamp order; each individual stream must be monotonic. Python receipt time
is diagnostic wall time only. The legacy SLP1 source/protocol remains available
for existing single-stream diagnostics and is not the meeting clock path.

Native capture writes are nonblocking. A full or closed pipe stops capture with
an error; it never silently discards PCM or accumulates an unbounded application
write queue. This is fail-fast overload handling, not proof of lossless hardware
capture. Generated-signal mode alone uses blocking writes so an accelerated test
can be paced by its consumer.

Call `request_stop()` then **continue consuming**: the parent sends `STOP\n` on
stdin, the helper stops both sources, drains converter tails, writes `SLE2`, and
exits. Only valid completion plus exit 0 is success. Missing tracks, EOF without
completion, trailing data, nonfinite samples, bad framing, backwards timestamps,
or stalled/missing individual streams fail. `close()` is forced cleanup and may
discard buffered tail; M4 must use graceful Stop with a bounded abort fallback.

## Verification and open gates

- Native helper compiles on the development host. The compatibility branch for
  macOS 13 emits SDK deprecation warnings for device-type aliases; no older-host
  execution or signed permission attribution is claimed.
- Generated native test: **4,800,000 samples per track**, 300 s each, known
  **150 ms** initial offset; measured offset change **+0.4375 ms** after integer
  timestamp quantization. Both tones remain approximately 440 Hz; tails drain
  without losing samples. Aggregate report is local/gitignored.
- Metadata-only host check: **three microphones**, one default, permission
  **denied**. No permission request, microphone capture, or audio file was created.
- Full local suite: **193 passing tests**, frontend regressions, 46-module import
  smoke, and Ruff checks. Tests cover malformed/truncated framing, independent
  clocks/gaps, per-stream stalls, graceful drain, strict consent, lazy source
  construction, device disappearance, and device-query failures.
- Brave interaction/visual check: physical mic choices and denied permission are
  visible; a physical device preference is acknowledged with its name. App
  running status is labeled, encrypted history restores, and Record stays disabled.

The original M3 **≤50 ms alignment/drift over a five-minute physical check** is
still open. So are actual headset/device-change behavior, real overload tests,
Teams/Slack helper-process routing, mic-only/dual native capture, and Linux parity.
The current host permission state prevents physical mic validation until access
is granted through the future explicit permission flow. Next implement M4's
encrypted recorder and permission/session UI, then perform controlled authorized
hardware checks. Signing remains deferred under the local-development decision.
