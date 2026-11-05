# Pause, Continue, and transcript bubbles

The recording bar offers **Pause**, **Continue**, and **Stop recording**.
Pause excludes subsequent microphone and meeting audio before it reaches the
recorder queue, encrypted tracks (including raw microphone audio), or transcription.
Continue resumes the same conversation without creating another recording.
Stop remains available while paused. Previously accepted audio may finish
transcribing after Pause.

Capture devices remain open during a pause, so macOS can still show microphone
access. Paused time is removed from the retained audio and transcript timeline
using the same sample clock on both tracks. The session's overall wall-clock time
limit and automatic call-end handling remain in effect. Pausing does not generate
notes; Stop completes transcription and follows the existing notes workflow.

Pause is a session-scoped, idempotent desktop command. The authoritative snapshot
includes pause state for reconnections; uncertain actions are never replayed.
Deepgram receives text KeepAlive messages during idle periods, without sending
paused audio or opening a second connection. See [Deepgram's keep-alive protocol](https://developers.deepgram.com/docs/audio-keep-alive).

The Transcript tab uses person-based chat bubbles (0.1.25, 4 November 2026).
Consecutive segments from the same corrected speaker identity form one turn,
even across capture sources. A speaker change or silence longer than 15 seconds
starts another bubble. Explicit linked-person identity takes precedence over
speaker IDs; matching names alone never merges people. Unidentified segments
stay separate. Numbered avatars and stable person colors distinguish speakers;
standalone recordings alternate sides by person; calls captured from another app
place microphone speech on the right and meeting audio on the left. Colors
continue to identify people. Capture
source and segment timestamps remain available in text tooltips. The turn header
shows the first timestamp, with selectable speech and naming/merge actions.
Every original segment keeps its citation anchor and highlight.

Incoming transcript refreshes do not request a scroll to the top. Earlier/Later
open the top of their selected page; Latest lands on the newest line at the
bottom. Citation links center their source line. Turns are grouped within each
100-segment page, preserving the existing pagination and bounded rendering work.
Speaker labels reflect the saved recognition/corrections; this UI change does not
re-run diarization or infer missing identities.

Native grouping regressions cover same-speaker cross-source turns, multiple
speakers on a single source, unknowns, long silence, explicit person links and
segment ordering/citation-ID preservation.

Validation uses synthetic PCM and mocked provider connections, not live capture
or billed requests. Coverage includes boundary packet trimming, shared timeline
compaction, repeated Pause/Continue, Stop while paused, encrypted file exclusion,
reconnection snapshots, keep-alive messages, and native UI state reduction.

The full Python run passed 642 tests (one skipped). The final pause/capture
subset passed 23 tests; the native regression executable, SwiftUI compilation,
Ruff, and bundle audit passed. The canonical app was rebuilt in place as
0.1.13 (14), Apple Silicon/macOS 13+, without bundling credentials or recordings.

Installed 0.1.25 acceptance: a saved two-person conversation captured entirely
through the microphone displays distinct person turns and colors. A Notes source
link navigates to and highlights its original segment inside a grouped bubble.
Native grouping and scroll regressions passed; no recording or playback was used.

## Transcript performance follow-up — 0.1.26 (4 November 2026)

Repeated scroll positions no longer publish unchanged whole-app follow state.
The native scroll observer reports boundary transitions, rather than every clip
bounds change. Speaker turns and parsed segment rows are cached when detail data
changes; unrelated discovery/status updates do not regroup the transcript. Bubble
views receive explicit busy/citation inputs instead of observing every core event.
Follow scrolling runs on attachment, resume and content growth; normal view
updates do not force a synchronous document layout or another bottom-scroll task.

Regression checks exercise 1,000 repeated observer reports and 1,000 identical
client scroll updates without extra notifications. Existing checks cover content
append, reading-position preservation, Latest, flipped coordinates, teardown,
speaker grouping and citation IDs. A short local main-thread sample was largely
idle, with SwiftUI layout and accessibility work during inspection. It is not a
controlled frame-rate benchmark or a claim of a measured speedup.

## Speaker numbers after re-transcription — 0.1.27

Anonymous person numbers are scoped to the selected transcript revision. The
roster previously numbered every stored session speaker before filtering to the
current revision, so fresh rerun identities appeared as Person 4/5 after an older
three-speaker result. It now filters revision participants before assigning
numbers. Default labels use the current numbers in both the roster and desktop
segments; explicit names and person links take precedence. Existing revisions
and raw speaker identities remain intact. Manual merges retain their revision's
existing number slots so other speakers do not shift during a correction.

A regression reproduces the old [4, 5] result, verifies [1, 2] on the fresh revision,
checks explicit renaming, and verifies old transcript segments remain untouched.
No re-upload or provider call is required to repair the displayed numbers.

## Messenger-style sides — 0.1.28

For application/system capture, microphone turns and in-progress microphone text
are always on the right; meeting-app audio is on the left. People retain their
labels and colors on either side. A turn cannot combine both sides. Standalone
microphone recordings alternate by person number (odd right, even left), with
unidentified speech on the left. Capture scope is read from saved session metadata,
so reruns retain the same mode. Layout does not infer that the first speaker is
the device owner.

Native regressions cover both capture modes, multiple people on the microphone,
meeting audio, same-person cross-source boundaries, cached grouping and repeated
scroll-state suppression.

## Pause-history bounds — 0.1.31

Closed pause ranges use binary search and cumulative removed-sample counts rather
than copying/scanning the entire history for every audio frame. Zero-duration
pauses allocate no history; adjacent ranges merge. Historical ranges remain
available for delayed packets on either capture track, preserving the shared
compacted timeline. A session allows at most 16,384 disjoint pause ranges. At the
limit, an additional disjoint Pause request is rejected and recording continues;
Continue and Stop remain available. Synthetic regressions cover rapid toggling,
late second-track packets, packets spanning many pauses and the explicit cap.
