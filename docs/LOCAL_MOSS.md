# Local MOSS Q6_K and cloud model selection

The recording setup offers two explicit choices:

| Choice | Processing | Languages | Audio upload |
| --- | --- | --- | --- |
| Cloud · Deepgram Nova-3 | Live provisional text and confirmed speaker labels | Auto multilingual or supported fixed language, including Vietnamese | Explicit Deepgram consent required |
| Local · MOSS Q6_K | Transcription and speaker labels after Stop | Auto English/Chinese, or fixed English/Chinese | None |

Deepgram is still the default. Each operation uses one selected backend. A local
failure retains encrypted audio and never invokes a cloud provider. Neither
backend isolates overlapping voices into separate audio files. Model speaker IDs
are anonymous, scoped to a track and generation, and do not establish real names.

## Setup

On the current development Mac, weights are downloaded, SHA-256 verified, and the
native library is built. Restart the existing ScribeLoop server to load the new
UI and code, then choose **Local · MOSS Q6_K — after recording**.

For another development checkout with a C/C++ toolchain:

```bash
uv pip install --python .venv/bin/python cmake
.venv/bin/python scripts/setup_moss_local.py
```

The setup script builds the shared library, downloads weights and checks their
hash before activating them. Downloads/builds happen only during setup, never as
a side effect of recording. Files stay under ignored `models/`. If runtime or
weights are missing, the UI explains setup and blocks local recording.

- Runtime: [transcribe.cpp](https://github.com/handy-computer/transcribe.cpp),
  commit `4d7c1ccefd39e5a9ea36cca5f8fefc6dcc14b99b`.
- Weights: [handy-computer MOSS GGUF](https://huggingface.co/handy-computer/MOSS-Transcribe-Diarize-gguf),
  revision `8c603aa6da33a068e39bbd109786f85786a21834`.
- File: `MOSS-Transcribe-Diarize-Q6_K.gguf`, 768,152,256 bytes.
- SHA-256: `7fd37409dbec9c732f912717f3cd8f4cd2c5083841edfab2f3b670f816512b44`.

Advanced deployments may set `MOSS_RUNTIME_DIR` and `MOSS_MODEL_PATH` in the
server process environment. These paths are not accepted from browser commands.
Packaged desktop distribution and non-macOS installation validation remain open.

## Behavior and saved recordings

Capture remains independent of inference and stops at five minutes. Both encrypted
tracks are authenticated before local inference begins. PCM crosses a bounded
stdin pipe into a separate process; plaintext audio and transcript files are not
created. Credentials are removed from that process's environment. Tracks run
sequentially to avoid loading two models at once. Entirely zero-filled tracks are
skipped; this is not a general speech/silence detector.

MOSS is an offline model. No words are promised while recording. The native
runtime chooses an available compute backend; speed depends on hardware. Each
track has a ten-minute processing timeout. App shutdown cancels and kills a stuck
worker. Model memory is released when the worker exits; weights are reloaded for
the other track rather than kept resident indefinitely.

Saved recordings have their own model selector. **Transcribe again** explicitly
replaces the transcript only after the full new pass succeeds. Selecting another
provider for **Resume** is rejected when a transcript already exists. Local Resume
reruns the saved recording atomically because speaker identities cannot safely
be continued across independent offline generations. Recovery alone does not run
a model. The transcript shows its current Local/Cloud provider after reconnect.

Auto lets MOSS choose between its supported languages. This port may leave the
detected language field blank; the app leaves it unknown rather than inventing a
language label. A fixed choice is retained on transcript rows.

## Validation, 2026-10-23

A real local-worker smoke test used the first 20 seconds of the existing public
AMI five-minute fixture, fixed English. It completed in **2.08 seconds including
worker startup**, produced six timed rows and two anonymous speaker IDs, and made
no provider requests. Raw output is in ignored
`data/phase1/moss-q6-k/smoke-20s.json`. This is one short sample, not a WER, DER,
long-meeting, overlap-quality or physical-capture acceptance result.

A second check replayed the same public excerpt through the actual model router,
SLPA capture and transcript store, with a zero-filled microphone track. It
completed in **5.686 seconds** with six labeled English rows, two speaker labels,
notes and **zero cloud requests**. This burst-fed fixture exercises the pipeline,
not native device timing. Output: `data/phase1/moss-q6-k/app-pipeline-20s.json`.

Offline regressions cover model routing, local capture-before-inference, speaker
persistence, language/setup rejection, authenticated tracks, atomic provider
replacement, no provider mixing, large PCM transfer and cancellation. UI tests
cover location labels, model-specific language choices and separate cloud consent.
