# Retained audio and explicit manual reruns

The app uses only Deepgram Nova-3 streaming ASR. The default route neither imports
nor constructs the local MOSS runner. Local model choices are rejected by the API;
old local transcripts remain readable. Historical experiments and downloaded
weights are preserved outside the app path. Default public-fixture local inference
is disabled; deterministic injected replay backends exist only for testing.

## Recording and notes

Accept the versioned first-use agreement, then choose **Record / Not now** for each
detected call. Accepting the agreement never starts capture. Provider/model and
capture scope remain visible. Stop finalizes the existing stream; there is no full
ASR rerun on Stop, failure, startup or reconnect. Summary configuration remains an
independent OpenAI-compatible API in `.env`. No provider request is made at startup.

## Thirty-day audio policy

Unarchived meeting audio expires 30 days after its saved end time, using start time
when end time is absent. The backend checks at startup and every minute while
running, idle and the current agreement is accepted. Closed apps cannot delete
files; overdue recordings are cleaned up at the next eligible check. The updated
agreement must be accepted before the new automatic policy runs on existing data.

**Archive audio** removes the expiry deadline. **Unarchive audio** restores the
original deadline; if it is already past, cleanup may run at the next check. Archive
cannot restore expired audio or a missing key. Playback reads, capture and ASR
exclude cleanup/deletion while their audio keys are in use. In-progress cleanup
finishes its current removal after revocation and does not start another one.

Expiry deletes the per-session key and encrypted audio files, then marks metadata
expired and clears audio paths. Transcripts, speaker labels/corrections, revisions
and grounded notes remain. Key/file or metadata failures are retryable; a missing
key makes playback/reruns unavailable even before expiry metadata is committed.
Archive is app-managed local retention, not a separate backup. Backups are outside
this policy. Confirmed whole-meeting deletion still removes audio and text together,
including archived recordings, and remains separate from automatic audio expiry.

## Playback and rerun

Select a saved meeting and **Load playback**, choosing Meeting audio or Microphone.
The authenticated local WebSocket carries an in-memory mono WAV only after the
entire encrypted track, including its footer, authenticates. Recorder gaps become
silence. No plaintext file is written by the app and no provider is called. Leaving
the meeting or expiry clears the browser audio blob. Browser/OS memory and any
user-created copies are outside key deletion.

Playback windows are bounded to 305 seconds. Select a start minute to hear later
parts of one-/three-hour recordings. The full encrypted track authenticates with
bounded memory before releasing that window; tampering or timelines past the
three-hour-plus-finalization bound are rejected. No whole long-recording WAV is
materialized. Rendering the transcript is paged at 500 entries; export is complete.

**Rerun transcription & notes** is an explicit cloud upload under the app agreement.
It reuses retained audio without opening capture, authenticates both tracks before
outbound ASR, and atomically replaces the transcript only after success. It then
starts one bounded final-notes workflow at the currently displayed configured
summary endpoint, reusing completed sections and reducing their notes.
The old transcript/notes survive failure; successful replacement makes old notes
stale until valid new notes arrive. Automatic topic titles refresh for records that
were originally auto-titled; custom manual titles remain unchanged. Without summary
configuration the button says **Transcribe again** and only ASR runs. No automatic
retry/fallback is added. Expired/unavailable audio disables playback/resume/rerun;
reading saved text/notes and exporting Markdown still works.

## Remaining work

The default development preset is five minutes; one-/three-hour presets now use
incremental streaming/recovery and bounded playback windows. See the
[Phase 2b acceptance boundaries](PHASE_2B_CLOSEOUT.md) and
[people/calibration workflow](PEOPLE_AND_CALIBRATION.md). Real-device validation
and automatic voice-recognition calibration/provider integration remain gated.
