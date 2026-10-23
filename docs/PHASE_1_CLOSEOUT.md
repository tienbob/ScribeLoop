# Phase 1 — macOS development closeout

Updated 2026-10-23. **Development scope closed. Release/quality acceptance is not
signed off.** Real-meeting testing and signing remain deferred as previously
requested by the user. The original quality, physical and app-coverage requirements
remain required; the checklist below records them without treating deferral as a pass.

This is the authoritative current status. Deepgram remains the default. A later
explicit [Local MOSS Q6_K option](LOCAL_MOSS.md) now transcribes after Stop without
uploading audio. Older PyTorch MOSS and Groq experiments are historical diagnostics.

## Delivered

| Area | Current implementation |
| --- | --- |
| Discovery | Metadata-only app activity observer, candidate state machine, dismissal/debounce, manual source fallback; detection never starts recording |
| Capture | Explicit microphone-only or app/system output + microphone, native macOS helper, shared-clock timestamps, resampling and bounded transport; mic-only needs no output capture/Screen Recording permission |
| Recorder | SLPA authenticated encrypted tracks, SQLCipher metadata, native key store, storage before capture, recorder-independent inference and Stop/drain |
| Recovery | Authenticated-prefix recovery into fresh encrypted containers; durable per-track checkpoints; explicit saved resume/reprocess; key-aware deletion and retention cleanup |
| Transcription | Explicit Cloud · Deepgram Nova-3 (live, default) or Local · MOSS Q6_K (after Stop); one selected backend, no automatic provider fallback/retry/final second pass |
| Speakers | Word-level v1 diarization; speaker-change rows, durable anonymous person labels, source-aware roster/badges, notes/history restoration; unknown attribution stays unknown |
| Languages | Auto multilingual default for en/es/fr/de/hi/ru/pt/ja/it/nl; eleven fixed choices including Vietnamese. Returned word language is retained. No ineffective language-hint control or false first-language-lock claim |
| Local model | Pinned, verified 768 MB Q6_K GGUF and transcribe.cpp native worker; Auto/fixed English or Chinese, sequential tracks, cancellation/timeout, no plaintext audio files |
| Cloud controls | Backend-only key, separate new/saved upload consent, durable ten-second audio reservations before upload, estimated $0.15/80-reservation cap shared across saved actions and both tracks |
| Local API/UI | Loopback token authentication, strict origin/host checks and CSP, visible recording/scope/Stop, timestamped transcript and transcript excerpt notes |
| Launch | Encrypted cloud mode starts without an old public WAV fixture. Explicit public-demo mode still requires an authorized fixture |

Source tracks use separate connections to the **same** provider. They do not
establish cross-track or cross-connection person identity. New resumed connections
receive fresh anonymous labels; names and biometric recognition are not implemented.
Speaker attribution does not separate overlapping voices into isolated audio.

The conservative app estimate is $0.60/audio-hour including diarization, not actual
account billing. Continuous PCM includes silence. Development capture remains
bounded to five minutes. Provider errors leave otherwise healthy encrypted capture
running; no automatic paid retry or alternative backend is started.

## Verification

- **316 Python tests passed**, seven frontend regression groups passed, and 60 application
  modules passed lightweight import smoke without loading local speech models.
- Automated checks cover consent/key boundaries, independent capture/Stop,
  encrypted recovery/tamper/failure cases, checkpoint rollback, durable cloud caps,
  speaker persistence/reconnect/notes, automatic language routing, language changes
  without new person identities, saved actions and deletion/retention.
- Local/cloud routing additionally covers setup/language guards, no cloud use in
  local mode, speaker persistence, provider replacement, no mixed-provider Resume,
  large PCM transfer and cancellation. A real 20-second local public-audio worker
  smoke test completed in 2.08 seconds with six timed rows and two speaker IDs;
  this does not establish quality or full-meeting speed.
- Mic-only regressions cover native helper routing, single-connection cloud text
  before Stop, local after-Stop processing, empty output-container saved Resume,
  source-consent UI and rejection of unexpected output frames. No private mic audio
  was recorded and no provider credits were used for these checks.
- The package builds with current backend/UI assets and no credentials or audio.
- Native generated-signal checks previously delivered two 4,800,000-sample tracks
  with a preserved 150 ms offset and approximately 0.44 ms offset change over five
  minutes. This is generated transport evidence, not physical device timing.
- The real **60-second public AMI** Deepgram diagnostic returned three speaker IDs
  against four reference speakers. WER was **17.39% (24/138 normalized words)**.
  First provisional text arrived **0.375 s after the first reference word began**;
  completion followed audio transmission by **0.305 s**. These are direct-provider
  single-sample timings, not an app end-to-end p50/p95 benchmark.
- Actual saved provider responses were replayed offline through the app splitter
  and transactional store, producing 16 labeled transcript rows and notes.
  The Auto integration is covered by mocked responses; the real trial used English.
- Native browser accessibility inspection confirmed Auto/Vietnamese guidance and
  the public three-label transcript. Screenshot automation timed out after window
  changes; pixel-level and narrow-width visual checks remain open.

Inspect the ignored local report
`data/phase1/deepgram-speaker-validation/results.html`, `speaker-transcript.txt`,
`app-segments.json`, and `results.json`. The one-minute trial had one connection,
zero retries and a conservative nominal $0.01 estimate. Remaining free balance and
actual charges were not verified. Final closeout checks made **no further cloud
requests, private uploads, recordings or permission changes**.

## Acceptance/support matrix

| Requirement | Status and evidence still needed |
| --- | --- |
| Teams web in Brave | Manual scope/UI and an earlier local-model call were exercised. A real call through the current Deepgram path remains deferred. Browser scope includes all tabs |
| Teams desktop / Slack huddles | Plumbing/candidate tests exist. Real-call detection and correct app/helper audio routing are unverified |
| Zoom / other browsers | Candidate/source plumbing exists; app-by-app acceptance is unverified |
| Automatic detection reliability | Measure idle false positives, call misses, delay, mute, listen-only, silence, reconnect and repeated meetings; no automatic call-detection reliability claim yet |
| Physical capture | Controlled audible mic/output, device changes, five-minute continuity, alignment/drift ≤50 ms and failure handling need real-device validation |
| Person separation | Implemented and observed as three labels, but the four-speaker reference demonstrates possible misses/merges. Representative 2–3-person attribution, confusion/DER and overlap quality are required and open |
| Accuracy/latency | Original representative WER ≤18% and partial p50 <2 s remain open; one public excerpt is insufficient sign-off |
| Resource use | No local ML models are loaded. Full native-capture CPU/RAM and network profiles remain unmeasured for the current backend |
| Linux | Native discovery/integrated meeting capture is not implemented/validated. Existing experimental source code is not advertised support |
| Packaged desktop/signing | Browser notebook is the development host. Native packaging, signing and notarization remain deferred; not distribution-ready |
| Visual QA | Accessibility-state checks and DOM regressions pass; screenshot/narrow-layout verification remains open |

## Launch

The key is already configured locally in ignored `.env` as `DEEPGRAM_API_KEY`.
Install `.[dev,security,sqlcipher]` in the existing environment if needed. Stop an
older server with Ctrl+C before opening the same database; only one process can
own an encrypted database. Then run:

```bash
.venv/bin/python -m scribeloop_core.api.server \
  --storage encrypted \
  --database data/phase1/secure-preview.sqlcipher \
  --port 8757
```

Open the fresh terminal launch URL. Choose/confirm the app and microphone, select
Auto or a fixed language, confirm participant permission and Deepgram upload
consent, then Record. Stop finalizes capture while pending text completes. Recovery
opens no microphone and uploads nothing; saved Resume/Reprocess needs its own
consent. English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese,
Italian and Dutch work in Auto; select **Tiếng Việt** for Vietnamese.

## Later acceptance run — when ready

1. Use headphones and a consented short English call with two or three people.
   Each person says a distinct reference sentence; note speaker order and timing.
2. Verify the microphone contains local speech and app output contains remote
   speech; confirm scope and Stop. Record actual app/browser/OS versions.
3. Check speaker changes against the reference, including a brief overlap, and
   whether provisional text appears during speech. Save errors, counts and timings.
4. Test idle/mute/listen-only/reconnect/discovery behavior separately from ASR;
   then run the controlled five-minute physical alignment/continuity check.
5. Exercise a saved interruption/recovery/resume and complete the remaining
   support/quality matrix before declaring the full MVP accepted.

Generated summaries and structured meeting notes remain later-phase work;
Phase 1's notes are transcript excerpts. No Phase 2 implementation is started by
this closeout. See [current streaming details](DEEPGRAM_STREAMING.md) and
[implementation matrix](PHASE_1_IMPLEMENTATION_PLAN.md).
