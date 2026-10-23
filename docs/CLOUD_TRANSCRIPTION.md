# Cloud transcription direction — 2026-10-23

> **Current default update (2026-10-23):** Deepgram streaming and individual anonymous speaker labels replace Groq short batches. See [current behavior and open speaker-quality gate](DEEPGRAM_STREAMING.md). Earlier measurements/defaults below are historical.

**Historical decision:** Groq Turbo was integrated after user acceptance of public Vietnamese samples, then replaced by Deepgram streaming. See [current checkpoint and measured limits](PHASE_1_CLOSEOUT.md). Provider investigation below records earlier options, not the current integration plan; quoted prices are historical observations.

User requested a low-cost cloud solution because local MOSS/community-1 inference
is too heavy for the development machine. Heavy local runs were stopped. This
supersedes the assumption that local inference must be the default product path;
local capture, encrypted recording/storage and recovery remain useful.

## Recommended first candidate: Soniox real-time

Official API pricing currently estimates **$0.12 per audio hour** for real-time
and **$0.10 per audio hour** for async. Billing is actually token-based, so these
are estimates rather than fixed per-hour guarantees. Vietnamese is supported in
both modes; streaming supports provisional/final tokens and speaker diarization.
Diarization/language identification are included in the advertised rate.

For separate microphone and meeting-output sessions, budget approximately
**$0.24 per meeting hour**, or **$4.80 for 20 meeting hours/month**, excluding
summaries, taxes, retries, context/translation output and any final second pass.
A five-minute dual-track test is about $0.02 at that estimate. Single mixed audio
would halve the base estimate but removes track-based source separation; preserve
the existing independent tracks for the initial quality test.

Soniox states that audio/transcripts are not used for model training and that
real-time data is not retained; async storage has different retention behavior.
These are provider statements, not independent verification. Audio still leaves
the device and must be disclosed explicitly before a cloud recording starts.

## Lower-cost alternative: Groq Whisper

Groq lists `whisper-large-v3-turbo` at **$0.04/audio hour**, and
`whisper-large-v3` at **$0.111/audio hour**. Its current speech API is file-based.
We could submit bounded audio chunks for delayed updates, with a minimum billed
length of ten seconds per request and additional overlap/retry costs. It is a
strong cheap batch candidate, but not a direct equivalent to a streaming service
with speaker-labeled provisional/final results. Vietnamese accuracy still needs
a matched test; vendor aggregate WER is not evidence for our meetings.

## Additional comparison

OpenAI's official pricing estimates `gpt-4o-mini-transcribe` at **$0.003/minute**
(about **$0.18/audio hour**) for transcription. Current dedicated live models
`gpt-live-transcribe`/`gpt-realtime-whisper` list **$0.017/minute** (about
**$1.02/audio hour**). Do not quote the mini file-transcription rate as the
price of those live models. Two full independent tracks approximately double
all per-audio-hour figures. No provider has been quality-accepted yet.

## Proposed implementation

1. Add an explicit cloud provider setting and cloud audio-processing consent,
   preserving existing recording authorization. No automatic upload of old meetings.
2. Keep native capture, bounded recorder queue, encryption and M5 recovery local.
   Replace default local ASR/diarization workers with a provider adapter; do not
   spawn MOSS/community-1 or local language models in cloud mode.
3. Stream microphone and selected meeting audio independently over TLS; preserve
   recorder timestamps, gaps, source labels and speaker uncertainty. Keep the
   long-lived API key in the local backend/native keystore, outside UI assets.
4. Persist only confirmed text, bound transport queues, and keep recording
   independent of cloud failure. Retry/catch-up must remain within consent and
   spending limits, with deduplication and explicit connection/catch-up status.
5. Show estimated usage/cost and a configurable hard application budget. Avoid
   automatically doing a second full paid transcription pass on every meeting.
6. Test five minutes of public AMI first, then a separately authorized Vietnamese
   reference clip. Compare live/final WER, speaker attribution, latency, actual
   billed usage and client memory before choosing the production default.

Provider selection/integration is not complete. No cloud credentials were read,
no paid API requests were made, and no audio/transcripts were uploaded during
this investigation. Existing M5/M6 changes remain work in progress, not a finalized
cloud implementation or accepted local-quality milestone.

## Sources checked 2026-10-23

- Soniox pricing: https://soniox.com/pricing
- Languages: https://soniox.com/docs/stt/concepts/supported-languages
- Streaming: https://soniox.com/docs/stt/rt/real-time-transcription
- Speakers: https://soniox.com/docs/stt/concepts/speaker-diarization
- Data handling: https://soniox.com/docs/security-and-privacy
- Groq models, file API and minimum billing: https://console.groq.com/docs/speech-to-text
- OpenAI official pricing: https://developers.openai.com/api/docs/pricing


## Groq public-audio trial — authorized 2026-10-23

User selected free API testing and has a key. Prepare a bounded public AMI
five-minute comparison of Whisper Large v3 Turbo and Large v3, using the exact
existing reference/normalizer. Read the key from `GROQ_API_KEY` or git-ignored
`.env`, never print it or write it to reports. Upload only the explicitly selected
public benchmark WAV (encoded FLAC in memory); do not access/upload saved meetings.
No local transcription/diarization model runs. Limit requests to one per selected
model with no automatic retries or paid fallback. Record timings, WER, returned
segment count, hashes, nominal paid-equivalent cost and peak client memory, not
the key or transcript text. Use a fixed HTTPS provider endpoint and bounded
responses/timeouts; mock the network for regression tests. This trial does not
change the app's recording backend or establish live/Vietnamese/speaker acceptance.
Groq currently publishes free Whisper limits; Soniox ended new-signup API credits
on October 27, 2025 (its app's free plan is separate).


### Groq trial results — 2026-10-23

Completed authorized uploads of the public AMI ES2004a 300–600 s fixture. Source
SHA-256 was verified against the existing local MOSS report and the exact existing
763-word reference/normalization was reused. Audio was encoded to a 3,054,497-byte
PCM16 FLAC in memory. No private recording was read/uploaded and no local model ran.

| Backend | Five-minute request/processing time | Word errors / 763 | WER |
| --- | --- | --- | --- |
| Groq Whisper Large v3 Turbo | 20.53 s request elapsed | 237 | 31.06% |
| Groq Whisper Large v3 | 38.43 s request elapsed | 227 | 29.75% |
| Existing local MOSS float16 + SDPA baseline | 82.93 s inference processing | 155 | 20.31% |

Timing scopes differ: Groq elapsed includes local FLAC encoding, network upload,
service and response; the historical MOSS value excludes model load. These are
file-transcription throughput results, not live partial/confirmed latency.
Cloud-client peak RSS was about 89.1 MiB in the first two-model process; it used no
GPU/ASR/diarization worker. Lower laptop compute is demonstrated for this test;
cloud recognition did not beat MOSS on this sample.

The initial sandbox network attempt failed before a response; the authorized host
run completed Turbo but its first Large v3 request hit a transport error. One
explicit manual retry of Large v3 succeeded. The adapter itself never retries.
The account's actual free/paid status and billing must be checked in Groq Console;
nominal paid-equivalent prices for successful five-minute requests are $0.00333
Turbo and $0.00925 Large v3. Failed requests may have reached the service, so these
figures are not an invoice or a guarantee of zero charges.

Aggregate reports (git-ignored, no transcript or key):
`data/phase1/groq-public-comparison-network.json` and
`data/phase1/groq-public-large-v3.json`. The first report's upload-size metadata
was corrected from encoder cursor position to actual buffer size, without another
Turbo request. The script now reports buffer length correctly.

Delivered: `pipeline/groq_asr.py` with a fixed HTTPS endpoint, bounded in-memory
input/response, sanitized errors, explicit models/language, literal `.env`/environment
key loading, and no fallback/retry; `scripts/benchmark_groq.py` with pinned public
input hashes and explicit upload consent. Fifteen mocked-network tests, changed-file
Ruff, 54-module import smoke and diff whitespace checks pass. `.env` remains ignored
and was restricted to mode 0600; its contents were never printed or committed.

Next: matched public or separately consented Vietnamese audio/reference testing,
then provider integration with explicit cloud consent and timestamp-preserving
chunking. The app recording backend has not been switched to Groq by this trial.


## Inspectable Vietnamese results — requested 2026-10-23

Create a local HTML report with exactly three columns: sound filename, complete
transcription text, and WER. Separate model sections identify Turbo versus Large v3
without adding a fourth column. Include local audio playback and expandable
reference text inside the transcription cell for direct inspection. Use five
public Vietnamese reference-labeled clips, preserve their source/revision/license
and normalized reference text, and never upload saved private meetings. Google
FLEURS row access failed on a server-side Parquet scan limit; use a small bounded
streamed VIVOS sample if available. This is read-speech diagnostics, not meeting
or overlap acceptance. Retain successful request results so rerunning report
rendering does not make extra API calls. No local speech models run.


### Inspectable Vietnamese result file — completed 2026-10-23

Five public VIVOS **test** clips, all speaker VIVOSDEV05, total 14.19 s and 37
normalized reference words were transcribed by both Groq models. Both obtained
4/37 errors (**10.81% weighted WER**), with different per-clip errors. Number-word
formatting differences such as `7` versus `bảy` count under the existing protocol;
no normalization was changed to improve scores. This tiny single-speaker
read-speech selection does not establish meeting, multi-speaker or overlap quality.

User-facing artifact: `data/phase1/groq-vietnamese-inspection/results.html`.
It is a standalone UTF-8 HTML file with embedded audio, exactly three columns
(filename / full transcript / WER), separate model sections and expandable
reference transcripts within the text column. Original public clips, source
revision, license/selection metadata and cached complete responses are retained
in the same git-ignored directory. VIVOS attribution and CC BY-NC-SA 4.0 research
scope are included. No private recordings or keys are in the report.

The report was opened in Brave; column layout, reference expansion and actual
embedded audio playback were verified. Structural checks confirm two tables,
three columns per row and ten complete transcript/audio entries. Fifteen mocked
Groq adapter tests and script Ruff checks pass. Rendering from cached results
makes no new API calls. All ten requests succeeded; no retry was needed.

## Bounded Soniox streaming trial plan — 2026-10-23

User authorized a small streaming trial and warned that free allowance is limited.
Before changing app defaults, use one WebSocket session and the five cached,
hash-pinned public VIVOS clips, with 250 ms pauses and one second trailing silence
(total under 20 seconds, mono 16 kHz PCM). Pace 100 ms frames in real time, enable
Vietnamese hints, language identification, endpoint detection and diarization.
Measure connection/first provisional/first confirmed/end timing and final WER.
Persist the attempt before connecting, refuse overwriting/repeated invocation,
cap connection lifetime at 45 seconds, and never auto retry. Keep key backend-only
in SONIOX_API_KEY/environment or ignored .env. Save an inspectable HTML and JSON
report under ignored data/phase1; no private capture and no production switch.
Docs checked: [real-time](https://soniox.com/docs/stt/rt/real-time-transcription),
[WebSocket API](https://soniox.com/docs/api-reference/stt/websocket-api),
[header authentication](https://soniox.com/docs/guides/websocket-authentication).
The documented model is stt-rt-v5. Token-based pricing is approximately $0.12/hour
per [pricing](https://soniox.com/pricing); this is an estimate, not evidence of
this account's free balance or billed usage.


### Soniox trial outcome — account gate, no quality result

One authorized connection was attempted with the locally configured key. WebSocket
connection opened after 1.226 seconds, then Soniox returned error code **402**.
The sender stopped after **0.3 seconds** of public PCM had been dispatched. No
text was returned; WER/first-token latency cannot be measured. No retry was made,
and the app remains on Groq. The provider error subtype was not retained in this
initial run, so we cannot distinguish exhausted balance from organization/project
budget restrictions. [Soniox's error reference](https://soniox.com/docs/api-reference/errors)
documents these 402 causes. Check API billing/free credits and relevant limits in
the Soniox console before authorizing a further attempt. No actual account charge
or remaining free quota was verified.

Inspect local `data/phase1/soniox-streaming-inspection/results.html` or `results.json`.
The script `scripts/inspect_soniox_streaming.py --consent-public-upload` has an
exclusive attempt marker and deliberately refuses repeat runs against that output;
there is no automatic reset, retry, or paid fallback. Intended full audio duration
was 16.4375 seconds; the full-trial nominal estimate of $0.000548 is not actual
usage/billing for this rejected attempt. Key and upstream error body were omitted.

## Bounded Deepgram English streaming plan — 2026-10-23

User clarified English is the primary language and configured DEEPGRAM_API_KEY.
Before changing the app backend, run one 20-second, real-time paced public AMI
ES2004a mixed-headset excerpt (300–320 seconds). Verify the existing 300–600-second
fixture SHA-256 and select the matching timestamped reference words. Use Nova-3,
English, 16 kHz mono linear16, 100 ms frames, interim results, 300 ms endpointing,
and speaker diarization v1. One connection, no retries, 45-second wall timeout,
exclusive attempt marker, and sanitized failures protect limited trial credits.
Persist final-only WER plus first interim/final timings and inspectable three-column
HTML. This does not switch production, capture devices or upload private audio.
Reference: [Live Audio](https://developers.deepgram.com/reference/speech-to-text/listen-streaming),
[CloseStream](https://developers.deepgram.com/docs/close-stream).

### Deepgram streaming result — completed, one request

Nova-3 English processed all 20 seconds of public AMI audio with zero retries.
Handshake took 0.787 s. First provisional text arrived 7.553 s from connection
attempt / 6.766 s after audio streaming began. The first annotated reference word
starts at audio time 6.38 s, so the observed first-text delay after that word's
onset was **0.386 s**. This is a first-word observation, not a p50 latency result.
First confirmed text arrived 11.627 s from connection attempt. Audio transmission
ended at 20.789 s; final processing completed at 21.426 s (**0.637 s tail**).

Final text: “A kind of dog? Yep. It's actually sitting. So sorry? It's sitting.
It's not standing. I see”. WER was **23.08% (6 errors / 26 normalized reference
words)**. The reference contains repetitions/fillers and overlapping speakers;
this tiny excerpt is not a representative accuracy benchmark and cannot be
compared directly with the earlier 300-second Groq/MOSS scores. Speaker labels
were requested but their correctness was not scored. No app backend switch or
private recording was made.

Inspect `data/phase1/deepgram-streaming-inspection/results.html` for the requested
sound-file / transcript / WER columns, embedded audio, reference and measurements;
`results.json` retains interim/final events and timestamps. The one-request
nominal base-rate estimate was $0.00161 using the previously checked $0.29/hour
rate, not actual charges or confirmation of remaining trial credit. The script
uses an exclusive attempt marker and refuses repeat calls. Offline fixtures check
interim/final accumulation and failure handling; lint/preflight pass.

Next: a separately authorized larger English sample can evaluate accuracy and
word-latency distribution before deciding whether to integrate Deepgram as the
streaming app backend. The production default remains Groq Turbo.


Current single-provider app integration: [Deepgram streaming/speaker status](DEEPGRAM_STREAMING.md). A separate 60-second public AMI validation returned three speaker IDs, 17.39% WER (24/138), 0.375 s first-text delay from reference speech onset and 0.305 s final tail. Reference contains four speakers, so multi-person diarization acceptance remains open. App label persistence was also checked offline against these actual responses. No retries or automatic alternative backend were used.
