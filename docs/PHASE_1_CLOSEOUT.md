# Phase 1 development closeout — 2026-10-23

> **Current default update (2026-10-23):** Deepgram streaming and individual anonymous speaker labels replace Groq short batches. See [current behavior and open speaker-quality gate](DEEPGRAM_STREAMING.md). Earlier measurements/defaults below are historical.

**The cloud development checkpoint is complete. Original full Phase 1 acceptance remains open.** The user accepted the public Vietnamese sample and deferred real-meeting testing. This closes implementation work for this checkpoint, not release approval.

## Delivered

- Groq `whisper-large-v3-turbo` is the default meeting backend. The backend reads `GROQ_API_KEY` from environment or the repository `.env`; keys never enter browser snapshots or reports. Local ASR/diarization dependencies are optional extras.
- Native microphone and selected output capture feed separate encrypted SLPA tracks and SQLCipher metadata. Capture and Stop drain independently of inference. Discovery suggests sources; only explicit Record starts capture.
- New recordings require participant permission and separate cloud-upload consent. Resume/reprocess require saved-audio cloud consent again. Browser capture includes all tabs in the selected browser.
- Short audio batches produce confirmed text while recording, share an early detected language lock, preserve gaps/timestamps, and catch up from durable checkpoints. Manual Vietnamese/English overrides are available. No automatic paid final pass, retry, local speech model, or remote speaker identity is added.
- M5 explicit authenticated-prefix recovery writes fresh encrypted containers, retains originals, and never starts capture. Resume, permanent deletion and explicit retention cleanup are available. Full-file verification remains strict; reprocess verifies both tracks before uploading either.
- Cloud reservations commit before upload. Limits are 80 requests and nominal $0.05 per saved session, shared across resume/reprocess; failed reservations remain counted. These application estimates are not a provider billing guarantee. Quota/network/model failure leaves otherwise healthy encrypted recording running.

## Evidence

286 Python tests passed, frontend regressions passed, and all 55 application modules passed lightweight import smoke. Fault tests cover interrupted/tampered audio, checkpoints/recovery, deletion retries, consent, durable cloud reservations, quota failure, and no duplicate resume after revision. Browser controls were checked on desktop and at 400px width without starting capture.

The real cloud coordinator check used five public VIVOS Vietnamese clips plus silence, paced over 30 seconds and duplicated into two test tracks. First text arrived at **13.80 seconds**, before capture stopped at **29.93 seconds**; processing completed at **33.17 seconds**. Both tracks reached recorded/confirmed checkpoints of 30,000 ms, Vietnamese locked automatically, and no errors were reported. Peak client RSS was **78.73 MiB** and no local ML runtime loaded. Four reservations represented 55.3 billable audio seconds, nominal **$0.00061444**. This is a public synthetic routing test, not a physical meeting or broad latency benchmark.

Local report: `data/phase1/cloud-coordinator-check.json`. Reproduce only with explicit public-upload authorization: `scripts/check_cloud_meeting.py --consent-public-upload` (see script CLI).

The inspectable Vietnamese report is `data/phase1/groq-vietnamese-inspection/results.html`: five clips, 14.19 seconds, 37 normalized reference units, **10.81% WER** for both tested Groq models. Vietnamese whitespace scoring counts syllables and number formatting differences. This tiny sample does not establish general meeting accuracy. Turbo scored **31.06% WER** on the separate five-minute AMI sample. See [cloud investigation and trials](CLOUD_TRANSCRIPTION.md).

## Acceptance and support matrix

| Area | Current evidence / remaining gate |
| --- | --- |
| macOS native capture | Implemented; generated timing/transport checks pass. Controlled physical audio, timing, device failures and real-call routing still need acceptance. |
| Teams via Brave | Earlier manually selected test produced text with poor local-model Vietnamese accuracy. New cloud backend has public-audio validation; real Teams cloud call remains deferred. Browser audio scope includes all tabs. |
| Automatic call detection | Metadata observer/candidates/manual fallback implemented. Teams/Slack automatic detection reliability in real calls remains unverified. Detection never authorizes capture. |
| Teams desktop, Slack, Zoom, other browsers | Source plumbing exists; app-by-app real-call acceptance remains unverified. |
| Transcript latency | Short batches require approximately 10–20 seconds of audio plus network time; observed first text 13.80 seconds. Original partial p50 <2 seconds target is unmet. |
| Speaker attribution / overlap | Separate microphone/output labels only in cloud default. Individual remote identities and 2–3 speaker acceptance are unavailable. Local community-1 experiments are diagnostic, not default support. Overlapping remote speech may reduce recognition accuracy. |
| Accuracy | Public samples measured; original representative corpus WER ≤18% and target sign-off remain open. |
| Linux / packaged desktop | Not validated. Current browser UI hosts local development; native desktop packaging is deferred. Signing/notarization was explicitly deferred by the user. |
| Notes and summaries | Timestamped transcript notes are available. Generated summaries and structured meeting notes belong to later work. |

## Run the demo later

Install `.[dev,security,sqlcipher]` into the existing environment as needed. The Groq key is already saved locally. Restart an older server with Ctrl+C before launching updated code; a database cannot be opened by two servers at once.

```bash
.venv/bin/python -m scribeloop_core.api.server \
  --storage encrypted \
  --database data/phase1/secure-preview.sqlcipher \
  --public-fixture data/phase1/public-preview.wav \
  --port 8757
```

Open the fresh launch URL printed in the terminal. Choose/confirm Brave and microphone, use headphones, check participant and Groq upload consent, then Record. This development test stops after five minutes. Stop finalizes recording while pending text catches up. Saved recovery does not upload; saved Resume/Reprocess needs its own cloud consent. The default cloud app disables the old local-model replay action.

Next acceptance work is a consented real call, controlled physical routing/timing, and app detection checks. Individual remote speaker labels or sub-two-second partials require a separately evaluated streaming/diarization backend. No private saved audio was uploaded during this closeout.
