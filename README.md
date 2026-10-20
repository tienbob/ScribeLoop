# ScribeLoop

System-audio → speaker-separated transcript → LLM summary → (Phase 3) tool-using agent.
Target platforms: **Linux + macOS**, local-first, privacy-preserving.

📖 **Read [`ARCHITECTURE.md`](ARCHITECTURE.md) first** — this repo is in **Phase 0a (Spike)** per its §8 roadmap.

## Repo layout

```
scribeloop/
├── ARCHITECTURE.md        # the plan (source of truth for every module)
├── core/scribeloop_core/  # Python package: capture, pipeline, llm, storage, agent, api
├── scripts/bakeoff.py     # Phase 0a ASR bake-off harness
├── tests/                 # unit tests (run anywhere; no audio hardware needed)
├── shell/                 # Tauri app (placeholder until the packaging spike decides)
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
run on any dev machine using `WavFileSource` (WAV replay). The **platform capture sources**
(`macos_client` — Swift tap helper; `linux_pipewire` — GStreamer) are implemented and
validated on their target OSes per Phase 0a. `capture/native/` holds the Swift helpers.

## Phase 0a tasks (from ARCHITECTURE.md §8)

- [x] Repo on GitHub (CI runs tests + import smoke on ubuntu/macos/windows)
- [ ] Signed & notarized macOS build records loopback PCM; permission attributes to the Tauri bundle
- [ ] ASR bake-off:
      ```bash
      python scripts/download_models.py sherpa-streaming-zipformer-en   # ~160 MB, into models/
      python scripts/bakeoff.py --wav <meeting.wav> --backend sherpa-onnx
      python scripts/bakeoff.py --wav <meeting.wav> --backend faster-whisper --model tiny
      ```
      (whisper.cpp / Moonshine measured on macOS/Linux hosts or separate harnesses)
- [ ] Tauri vs pure-Python packaging decision (incl. GStreamer/PyGObject in AppImage/deb, llama-cpp-python wheels)
- [ ] Online-diarization decision (diart → near-online chunked → DIY)
- [ ] SQLCipher + sqlite-vec coexistence check; vec0 delete-scrub check
- [ ] Smoke test on the second OS
