# Automatic meeting detection and audio capture

Requirement recorded **2026-10-22**, before implementation changes. Status:
**partially implemented**: a metadata-only observer and candidate state machine
exist, with authenticated candidate/dismissal UI and manual source selection
([implementation and limits](MEETING_DETECTION.md)). Validated active-call
integration and an integrated live recorder are not yet delivered.

## Product behavior

ScribeLoop must discover likely active calls automatically and obtain their audio
locally. Initial targets are Microsoft Teams desktop calls and Slack desktop
huddles. Subsequent targets are Zoom and browser meetings (Google Meet, Teams web,
and Slack web). Support must be verified per app and OS before being advertised.
Opening a chat app, playing media, or having a calendar event is insufficient proof
that a meeting is active. No cloud bot, calendar connection, or service-account
integration is required for the first local implementation.

1. Watch supported app/process and OS audio-activity metadata without recording PCM.
2. Combine available signals into a candidate with app, evidence, capture scope,
   and confidence/ambiguity visible to the user. Debounce transient activity and
   suppress repeated prompts for a dismissed ongoing candidate.
3. Prompt the user to confirm the app/source, microphone, and recording consent.
   Detection may preselect a source; it never silently records.
4. Capture the meeting app's output and microphone as separate timestamped streams
   through the encrypted recorder, then feed the live transcription pipeline.
   Keep the recording indicator and Stop available throughout capture.
5. Detect likely call end using a grace period. Offer to stop; do not terminate
   a recording solely because of silence or a brief activity change. Manual
   source selection and Stop remain available if detection fails.

Multiple candidates require explicit selection. App disappearance, permission
denial, device changes, and capture failure must produce a clear state and
recoverable action; never silently switch to whole-system capture. Starting a
second recording must respect the existing single-active-session policy.

## Capture boundaries

- Per-app output is preferred. Whole-system output includes unrelated sounds and
  must be an explicit user choice, clearly labeled in the recording UI.
- Microphone capture is part of Phase 1: output alone usually misses the user's
  speech. Preserve separate encrypted streams and a common timestamp reference;
  do not store an early mixed track as the only source. Echo suppression and
  reliable attribution remain separate work; do not promise clean speaker output
  when using loudspeakers. A shared microphone may contain several people.
- Browser audio activity identifies a browser, not reliably a meeting tab or
  service. Present ambiguous browser candidates honestly and require selection;
  do not claim tab-isolated capture from a browser process signal alone. No URL,
  page-content, or browsing-history inspection is part of the initial detector.
- Local output capture does not expose individual remote participant tracks.
  Overlap remains an ASR/diarization limitation, not solved by meeting detection.
- Discovery stores no raw audio or transcript. Retain only necessary session
  source metadata after confirmation; avoid persistent activity history.

## Implementation order

1. Verify native OS metadata APIs and installed app identities. On macOS,
   investigate Core Audio process input/output activity and explicit app/helper
   identity mapping; do not equate microphone use with a confirmed meeting.
2. Build a metadata-only observer and deterministic detector state machine with
   configurable debounce, dismissal, end grace, and permission/error handling.
3. Surface authenticated candidates and source selection in the local UI.
4. Connect the existing native output helper, separate mic source, encrypted
   recorder, consent state, and live pipeline as one session lifecycle.
5. Validate real calls per supported app. Linux requires a corresponding
   PipeWire/PulseAudio discovery adapter and host validation; macOS results alone
   do not establish cross-platform support. Signing remains deferred locally.

The existing macOS ScreenCaptureKit helper supports explicit bundle-ID targeting;
it is not an active-call detector. Core Audio process taps remain the preferred
planned capture path. API availability, permission behavior, and helper-process
coverage must be verified during implementation.

## Acceptance before declaring support

- Teams and Slack: an actual active call/huddle yields one useful prompt; an open
  idle app does not. Exercise microphone mute, silence, output-only listening,
  reconnect, app exit, dismissal, and repeated meetings. Record false positives,
  misses, and detection delay; set thresholds from measurements before sign-off.
- Browser meetings: ambiguous source labeling and manual selection work; no
  unverified claim of service or tab identification.
- Confirmation/consent is required before PCM capture. Denial or dismissal
  produces no recording; permission failures never broaden scope automatically.
- An authorized short call records audible remote and local speech in separate
  encrypted streams, with bounded alignment error measured and documented.
  Stop closes both streams; interruptions and device changes are visible.
- Restart preserves confirmed text and recoverable encrypted audio. Detector
  failure does not block manual recording or Stop. Single-session behavior holds.
- Begin with short controlled calls; use at most five-minute development samples
  for performance and accuracy work. Report detector overhead separately from
  capture/ASR latency. Real app/OS checks remain pending until actually exercised.
