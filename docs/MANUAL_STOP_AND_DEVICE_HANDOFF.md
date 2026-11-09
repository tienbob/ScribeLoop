# Manual Stop and default microphone handoff

## Stop and notes

A failed live transcription worker previously set the same cancellation flag
used for app shutdown and agreement revocation. Capture kept running, but a
later manual Stop saw that flag and marked the conversation Interrupted before
reaching automatic notes scheduling.

Each transcription worker now has its own cancellation event. Shutdown and
revocation still cancel processing; provider/storage failures drain their worker
and retain the original exception. An ordinary Stop finishes capture and the
existing transcription stream. Genuine transcription failures remain Failed
with a diagnostic, rather than appearing as unexplained interruptions.

When a manually stopped recording has confirmed text and a transcription
failure, the original automatic-notes grant can still produce notes from that
text. The UI explicitly says the transcript is incomplete and notes use only
confirmed speech. Empty transcripts, capture failures, revoked consent, changed
summary endpoints, durable budgets, and the existing once-only scheduling guards
retain their checks. No automatic ASR rerun or provider switch was added.

## System default microphone

The helper previously exited as soon as the default microphone changed. It now
replaces the input on the existing AVCaptureSession, leaving meeting-app/system
audio capture and the recording session intact. It drains the old microphone
resampler on the shared delegate queue before switching, starts a new format and
clock epoch, and retains the host-clock gap rather than overlapping timestamps
or moving meeting audio. Runtime/interruption notifications request recovery for
the default input; input replacement runs away from the SCK delegate queue.

Recovery retries at most every half-second for up to five seconds, within the
transport's existing ten-second stall bound. If no usable default microphone
returns, recording fails visibly and retains encrypted audio. An explicitly
selected microphone remains pinned; disconnecting it does not switch to another
device. Stop remains available during recovery and expected shutdown
notifications do not turn Stop into a microphone error.

## Validation

- Full Python suite: 649 passed, one skipped. Tests cover clean Stop, live failure
  followed by Stop, failed usage reservations with zero uploads, summary scheduling
  from confirmed partial text, and cancellation/recovery behavior.
- Swift helper compiles for Apple Silicon/macOS 13. Pure route fixtures cover
  device changes, missing defaults, interruption, retry deadline, format changes,
  and clock discontinuities.
- Generated PCM switches microphone input from 48 kHz mono to 44.1 kHz stereo.
  The real framing reader accepts 32,000 microphone samples and 16,000 meeting
  samples, with a 200 ms microphone gap and no timestamp overlap.
- No hardware recording, speaker playback, or billed provider requests were used.
  A real headset/default-input handoff during a call remains an acceptance check.

Packaged as 0.1.14 (15). Existing interrupted recordings retain their saved status;
this update does not fabricate a completed transcription or silently upload them.

After the local build, ScribeLoop's stale ScreenCapture grant was reset and the
canonical app re-added. The final in-app silent check reports meeting audio
allowed, microphone authorized, and display available. No audio was recorded.
Future canonical local rebuilds automatically run the same app-scoped reset;
the five packaging tests, including reset scoping, pass.

[Apple's running-session configuration API](https://developer.apple.com/documentation/avfoundation/avcapturesession/beginconfiguration()).
