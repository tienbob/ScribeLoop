# ScribeLoop

System-audio → speaker-separated transcript → LLM summary → (Phase 3) tool-using agent.
Target platforms: **Linux + macOS**, local-first, privacy-preserving.

Start with the [native macOS app instructions](packaging/macos/DESKTOP.md) and
[current acceptance checklist](docs/CURRENT_STATUS.md). The working app is a
SwiftUI desktop client with a bundled Python core. Signing, real-call acceptance,
and public distribution remain open. [Architecture](ARCHITECTURE.md) also preserves
historical experiments and the longer-term roadmap.

## macOS app

Open `dist/macos/ScribeLoop.app`. Configure notes in Preferences, accept the
first-use agreement, and choose **New recording** or review a detected call.
The app runs without a terminal server, browser, installed Python, or local speech
models. Closing its window keeps the menu bar available; Quit stops the core.
The internal test build uses customer-owned transcription and summary credentials;
no shared API key is bundled. It remains ad-hoc signed until Developer ID signing
and notarization are completed. See [internal Mac distribution](docs/INTERNAL_MAC_DISTRIBUTION.md).

## Repo layout

```
scribeloop/
├── ARCHITECTURE.md        # the plan (source of truth for every module)
├── core/scribeloop_core/  # Python package: capture, pipeline, llm, storage, agent, api
├── scripts/bakeoff.py     # Phase 0a ASR bake-off harness
├── tests/                 # unit tests (run anywhere; no audio hardware needed)
├── shell/                 # native SwiftUI macOS app and fixture host
└── audio/                 # runtime session recordings (gitignored)
```

## Browser/core development setup

Deepgram Nova-3 streaming with anonymous speaker labels is the only app ASR path.
Install core/security dependencies without local ML packages, configure
`DEEPGRAM_API_KEY` in the ignored `.env`, and restart the server. Accept the app
agreement once, then choose **Record / Not now** for each detected call. No repeated
upload checkboxes are needed. Stop finalizes the current stream and never starts
a full transcription rerun. **Microphone only** captures no app audio and does not
require Screen Recording permission.

Encrypted audio is kept for **30 days**, then removed while the app is running,
idle and the agreement is accepted. **Archive audio** exempts it from expiry;
unarchiving an old recording makes it eligible again. Transcripts and notes remain
after audio expiry. While audio exists, use **Load playback** to listen locally or
**Rerun transcription & notes** to explicitly upload it for a replacement transcript
and a bounded notes workflow. No provider fallback or automatic paid retry is used.
See [retained audio and manual reruns](docs/RETAINED_AUDIO.md).

Recording preferences offer 5-minute, 1-hour and 3-hour presets with shared audio
cost caps. Long calls use bounded encrypted readers, playback windows and durable
section notes. Summaries default to OpenAI `gpt-5.4-mini`: set `OPENAI_API_KEY` in
`.env`; explicit `SUMMARY_*` overrides remain supported. Named people can be linked
across meetings manually; voice learning requires a configured cloud extractor and
consented calibration. See [Phase 2b delivery and gates](docs/PHASE_2B_CLOSEOUT.md).
See [native launch instructions](packaging/macos/DESKTOP.md). The
[Phase 1 closeout](docs/PHASE_1_CLOSEOUT.md) preserves historical development evidence.
The [documentation index](docs/README.md) separates current instructions from historical evaluations.
The demo web UI opens directly at `http://127.0.0.1:8757/`, without a launch key.
Restart the server after updating. Add `--require-launch-key` to opt into token authentication.

```bash
uv pip install -e ".[dev,security,sqlcipher]"
```

The optional `local-asr`, `diar` and `sherpa` extras are for historical evaluations.
The new Q6_K option uses transcribe.cpp and does not require PyTorch. Local speech
models are not loaded by cloud meetings.

## Optional local evaluation setup

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev,local-asr,diar,sherpa]"
pytest
```

> The full ML stack (torch + pyannote) is ~2–3 GB. Cloud setup above does not require this stack.

## Platform note (development on Windows)

The pipeline, storage, LLM client, tests, and bake-off harness are platform-independent and
run on any dev machine using `WavFileSource` (WAV replay). The macOS ScreenCaptureKit fallback and Python transport build locally; silent
and controlled-tone host capture pass after enabling Screen Recording permission. The preferred
Core Audio tap and Linux GStreamer source remain placeholders.
`capture/native/` holds the Swift helper source.

## Historical Phase 0a tasks (from ARCHITECTURE.md §8)

These original research checklists are historical; use the
[current acceptance checklist](docs/CURRENT_STATUS.md) for active work. Tauri,
local-model bake-offs and embedded llama.cpp are not the current desktop stack.

See the [Phase 0 local-development closeout](docs/PHASE_0_CLOSEOUT.md) for defaults,
implementation decisions, and explicitly deferred gates.

Current implementation and host-validation status: [Phase 0a notes](docs/PHASE_0A.md).

- [x] Repo on GitHub (CI runs tests + import smoke on ubuntu/macos/windows)
- [x] Consumer-side silent-frame monitor and bounded WAV diagnostic (native integration pending)
- [ ] Signed & notarized macOS build records loopback PCM; permission attributes to the Tauri bundle
- [ ] ASR bake-off:
      ```bash
      python scripts/download_models.py sherpa-streaming-zipformer-en   # ~160 MB, into models/
      python scripts/bakeoff.py --wav <meeting.wav> --backend sherpa-onnx
      python scripts/bakeoff.py --wav <meeting.wav> --backend faster-whisper --model tiny
      ```
      (whisper.cpp / Moonshine measured on macOS/Linux hosts or separate harnesses)
- [x] Select Python/FastAPI + browser for local v0 development
- [ ] Validate frozen distribution, Linux GStreamer packaging, and embedded llama-cpp-python wheels
- [x] Select near-online chunked community-1 for the initial Phase 1 implementation; live quality/delay unvalidated
- [x] SQLCipher + sqlite-vec coexistence check; vec0 delete-scrub check — **both verified 2026-10-21** (SQLCipher 4.19.0 + sqlite-vec 0.1.9 coexist; vec0 deletes scrub vector bytes under `secure_delete=ON` + WAL TRUNCATE checkpoint). Harness: `scripts/check_vec.py`; tests: `tests/test_vectors.py`
- [ ] Smoke test on the second OS

## Phase 0b: development checkpoint closed

Development work is closed for now. Results and deferred release criteria are tracked in
[the Phase 0b checklist](docs/PHASE_0B.md).

- [x] Corpus validation, AMI import, and reproducible ASR baseline tooling
- [x] Sherpa error investigation, adapter fixes, and Whisper comparison
- [x] Local MOSS WER/diagnostic DER and precision/quantization experiments
- [x] Five-minute Opus robustness check with timing/hash safeguards ([results](docs/CODEC_EVALUATION.md))
- [x] Overlap-error review and dedicated community-1 comparison ([results](docs/DIARIZATION_REVIEW.md))
- [x] Five-minute development default: ES2004a 300–600 s, float16 + SDPA
- [ ] Complete multi-meeting corpus, codec variants, live-latency baselines, and target sign-off

Historical five-minute PyTorch MOSS result: **20.31% WER**, **32.16% diagnostic DER**, and
**82.93 s processing**. These are exploratory results from one meeting, not release
validation. See [MOSS evaluation and configuration](docs/MOSS_EVALUATION.md).
Full-meeting MOSS runs are deferred during development.

## Phase 1: macOS development closed

Deepgram Nova-3 now streams microphone/output audio and returns anonymous individual speaker labels. Capture, SQLCipher metadata and SLPA audio stay encrypted locally; the first-use agreement authorizes cloud processing; each call still waits for Record. Use Auto (default) for supported multilingual conversations, or choose a fixed language such as Tiếng Việt. No local speech models load in the default path.

M5 recovery/resume/deletion/retention and durable cloud checkpoints are implemented. See [streaming speaker implementation and acceptance](docs/DEEPGRAM_STREAMING.md) for current validation. The earlier Groq public-audio check measured 13.80 s first text and 78.73 MiB peak client RSS; these are historical batch-backend measurements.

Encrypted cloud startup no longer needs a public WAV fixture. See [Phase 1 closeout and demo instructions](docs/PHASE_1_CLOSEOUT.md). Real-call/device acceptance, automatic detection reliability, speaker-attribution quality, original latency/accuracy targets, Linux and packaged desktop validation remain open. Real-call acceptance and signing remain open. The native SwiftUI app is the current macOS host; the browser UI remains a development/test client.
