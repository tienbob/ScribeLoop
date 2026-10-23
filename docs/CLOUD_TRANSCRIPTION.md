# Cloud transcription direction — 2026-10-23

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
