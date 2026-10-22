# ScribeLoop

System-audio → speaker-separated transcript → LLM summary → (Phase 3) tool-using agent.
Target platforms: **Linux + macOS**, local-first, privacy-preserving.

📖 **Read [`ARCHITECTURE.md`](ARCHITECTURE.md) first** — **Phase 0 local-development handoff is documented; signing, hardware validation, and release gates remain deferred** (see §8).

## Repo layout

```
scribeloop/
├── ARCHITECTURE.md        # the plan (source of truth for every module)
├── core/scribeloop_core/  # Python package: capture, pipeline, llm, storage, agent, api
├── scripts/bakeoff.py     # Phase 0a ASR bake-off harness
├── tests/                 # unit tests (run anywhere; no audio hardware needed)
├── shell/                 # deferred desktop shell; local v0 uses Python/browser
└── audio/                 # runtime session recordings (gitignored)
```

## Dev setup

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev,diar,sherpa]"
pytest
```

> The full ML stack (torch + pyannote) is ~2–3 GB. For a light start: `pip install -e ".[dev]"`.

## Platform note (development on Windows)

The pipeline, storage, LLM client, tests, and bake-off harness are platform-independent and
run on any dev machine using `WavFileSource` (WAV replay). The macOS ScreenCaptureKit fallback and Python transport build locally; silent
and controlled-tone host capture pass after enabling Screen Recording permission. The preferred
Core Audio tap and Linux GStreamer source remain placeholders.
`capture/native/` holds the Swift helper source.

## Phase 0a tasks (from ARCHITECTURE.md §8)

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

Current five-minute MOSS result: **20.31% WER**, **32.16% diagnostic DER**, and
**82.93 s processing**. These are exploratory results from one meeting, not release
validation. See [MOSS evaluation and configuration](docs/MOSS_EVALUATION.md).
Full-meeting MOSS runs are deferred during development.
