> Current Phase 2b contract: cloud streaming only, versioned first-use app
> agreement, 30-day audio-only expiry with archive, and explicit manual reruns.
> See [retained audio](RETAINED_AUDIO.md). Historical per-upload checkbox behavior
> described in earlier measurements is superseded.

# Deepgram streaming and required speaker separation

Updated 2026-10-23. Anonymous individual speaker attribution is a **core requirement**.
The default app now uses Deepgram Nova-3 with its v1 streaming diarizer, replacing
Groq short batches. Each meeting uses one provider, with no automatic fallback,
local speech model, or automatic final second pass. The independent microphone
and output tracks each use a connection to the same provider.

## Behavior

- **Auto · multilingual** is the default. Nova-3 recognizes English, Spanish,
  French, German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch within
  the same stream. Recognition follows language changes; it does not lock the
  first detected language. Pick a fixed language to constrain recognition;
  Vietnamese is available only as the fixed Tiếng Việt choice in this profile.
  One provider still handles transcription and speaker labels.
- Send continuous recorded mono 16 kHz PCM, including silence. Use interim results
  and 300 ms endpointing. No new client VAD is introduced.
- Final word speaker IDs split transcript rows at speaker changes. In Auto mode, returned word language also splits rows while retaining the same person key. Rows receive
  anonymous `Speaker 1`, `Speaker 2`, etc.; the audio source remains visible too.
  Missing speaker IDs remain unknown. Provisional text is ephemeral and does not
  assert a speaker identity.
- Speaker records, confirmed rows and the audio checkpoint commit atomically.
  Labels restore in history after browser/server reconnect and appear in transcript
  excerpt notes. No new schema migration is needed: existing `session_speakers` and
  `segments.local_speaker_id` tables/fields are used.
- Identity is scoped to each provider connection and source. A resumed connection
  receives fresh labels; identical provider numbers do not prove the same person
  across tracks/resumes. Names, cross-meeting recognition and biometric enrollment
  are not implemented. Speaker attribution does not isolate overlapping waveforms.
- Capture and encrypted writes remain independent. Provider failure stops streaming
  without stopping otherwise healthy recording. No automatic retry/reconnection
  spends more credits. Explicit Resume continues from confirmed checkpoints;
  Reprocess replaces the transcript atomically only on success. Both saved tracks
  authenticate before either uploads. A legacy transcript from another provider
  must be explicitly replaced rather than silently mixing providers on resume.

## Authorization and bounded use

The existing ignored `.env` contains `DEEPGRAM_API_KEY`; it is read only by the
backend. Neither snapshots, browser storage nor API error bodies disclose it.
Restart the existing demo server before using updated code, then open its fresh
launch URL. Record still requires source selection, participant permission and
the first-use app agreement. Each detected call still offers Record / Not now;
manual reruns are explicit actions covered by that agreement.

A 10-second audio reservation is committed before each block can upload. The
current production shared caps across both tracks and saved actions are
$0.25 (five-minute development), $3 (one hour) and $8 (three hours), with
at most 160/1,800/4,800 ten-second reservations respectively. Injected benchmark
configurations may retain lower caps. Use a conservative $0.60/audio-hour rate including
diarization; this is an app safety estimate, not actual account billing. Reserved
but unsent time remains counted after failures. The cap is separate from provider
credits and limits. Native development capture remains bounded to five minutes.
No changes were made to the user's existing running demo during implementation.

## Verification and remaining acceptance

Offline tests cover word speaker changes/unknown labels, separate source namespaces,
live rows before Stop, persistent notes/history, API reconnection, no exposed key,
durable quota reservations, failure isolation, strict saved-track authentication,
new identities after reprocess, and atomic rollback of speaker/checkpoint writes.
Frontend tests render individual speaker badges/rosters, source and language metadata, and automatic multilingual status. Offline integration checks make no cloud requests. One separately bounded 60-second public validation was run below; no private audio was uploaded.

Earlier real 20-second public AMI streaming test: first provisional text was 0.386 s
after the first annotated reference word's onset; final WER was 23.08% (6/26).
**That response returned only speaker ID 0**, despite speaker changes in the
reference. It does not demonstrate accurate multi-person diarization. Functional
integration is implemented; the core quality gate remains **open** until a bounded,
representative multi-speaker test and later consented real-call validation establish
speaker counts, confusion and overlap behavior. Do not call this gate passed from
mocked split tests or the single-label public response. Individual labels may be
wrong, merge people or split one person, especially with overlapping speech.

[Provider diarization](https://developers.deepgram.com/docs/diarization) documents
per-word IDs and v1 streaming support; v2 is batch-only and is not selected here.
[Live API](https://developers.deepgram.com/reference/speech-to-text/listen-streaming)
and [CloseStream](https://developers.deepgram.com/docs/close-stream) describe the
protocol. See [cloud trial history](CLOUD_TRANSCRIPTION.md) for measured results.

A separate bounded public validation is planned for AMI 300–360 seconds (four
reference speakers): one connection, 60 seconds, no retries, 85-second wall cap,
and conservative nominal estimate $0.01. Separate attempt marker prevents repeats.
This tests provider speaker output; it does not imply real-call acceptance.

### Completed 60-second validation

The separate 60-second test completed with one connection and zero retries.
Deepgram returned **three IDs (0, 1, 2)** against four reference speakers (raw
reference word counts A=88, D=24, C=11, B=3). First provisional text arrived
**0.375 s after the first annotated reference word began**; final processing ended
**0.305 s after audio transmission ended**. Final WER was **17.39% (24/138 normalized
reference words)**. Count differences and merged early turns mean speaker quality
is still not signed off. Do not infer accurate separation from the speaker count.

The actual saved responses were replayed offline through the app's word splitter
and transactional store, yielding 16 labeled rows and three stable anonymous
labels in transcript notes. No second API request was needed for this replay.
Inspect `data/phase1/deepgram-speaker-validation/results.html` (audio, transcript,
WER and speaker-labeled text), `speaker-transcript.txt`, `app-segments.json` and
`results.json`. The conservative nominal full-test estimate is $0.01, not a verified
charge. No private audio was uploaded.

Final verification: **295 Python tests passed**, all frontend regression groups passed, 56 application modules passed lightweight import smoke, and the wheel built with the streaming module/assets and no credentials or audio. Changed Python files pass Ruff and the diff has no whitespace errors.


## Language and speaker UI update — 2026-10-23

User chose **Auto + fixed language, omit ineffective hints** after reviewing the
provider limitation. The dropdown retains Auto and eleven fixed language choices
(the ten multilingual languages plus Vietnamese). New recording and saved-action
API models validate the same allowlist. Session metadata retains the requested
choice; Auto recognition metadata is persisted as multilingual mode, and segment
languages come from provider word languages rather than a fabricated English label.
Unknown language stays unknown. Fixed choices retain the selected language.

The UI now shows a colored anonymous-speaker roster, matching badges on transcript
rows, separate source labels, per-row language names and a clearly marked live
provisional draft. Speaker names remain anonymous; labels do not guarantee a
person's identity or correct overlap separation. No provider/model fallback or
extra inference call was introduced. Existing notebooks and legacy local speaker
turns remain readable. Record / Not now, Stop and source selection remain explicit. Processing
authorization comes from the persistent first-use agreement; no upload checkbox is shown.

Native browser accessibility inspection verified Auto, the Vietnamese guidance,
three labels from the public fixture, and separate transcript words/metadata.
Screenshot automation timed out following user window changes, so this update's
pixel-level and narrow-width visual QA remain pending. No capture/upload occurred
in the isolated preview. **297 Python tests**, frontend regressions and 57-module
lightweight import smoke passed; no additional API credits were used.

Nova-3 has no streaming language hints. The user-approved UI does not offer a
multiple-select that pretends to bias or restrict recognition. Automatic mode
covers its full supported set. Provider documentation distinguishes Nova-3
[multilingual streaming](https://developers.deepgram.com/docs/multilingual-code-switching)
from Flux-only [language hints](https://developers.deepgram.com/docs/flux/language-prompting).
The separate `detect_language` parameter is [not supported for streaming](https://developers.deepgram.com/docs/language-detection#streaming-and-multilingual-alternatives).
Automatic multilingual recognition is covered by mocked integration tests here;
the earlier real public benchmarks used fixed English.
