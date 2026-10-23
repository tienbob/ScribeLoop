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
The **Microphone only** recording source works with either backend. Cloud provides
live mic text; Local MOSS processes it after Stop, with no output capture or upload.

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

## Live-window experiment, 2026-10-23

Public AMI, first 60 seconds of the pinned fixture; fixed English; native Metal
backend `MTL0` on this development Mac. The experiment keeps one native model and
session resident, unlike the current one-shot app worker. No capture device or
cloud service was used. Times below are measured decode times; arrival/backlog
times in the detailed report are simulated, not app end-to-end measurements.

| Strategy | WER after invalid-timestamp rejection | Median decode | Slowest decode |
| --- | ---: | ---: | ---: |
| Entire 60-second clip | 13.77% (19/138) | 3.694 s | 3.694 s |
| 5-second updates, 2-second left overlap | 17.39% (24/138) | 0.526 s | 0.949 s |
| 10-second updates, 2-second left overlap | 18.12% (25/138) | 0.649 s | 1.295 s |
| Growing prefix every 10 seconds | 13.77% final (19/138) | 2.021 s | 3.674 s |

Peak process RSS for this complete 60-second comparison was **711.5 MiB**, not a
VRAM measurement. The initial 5-second window contained no reference speech and
produced hallucinated refusal text with timestamps at 5–6 seconds, beyond its
input. Matching the app's timing validation rejects that response. Including it
raises the raw stitched WER to **24.64% (34/138)**. Rejection can withhold bad text;
it does not prove that valid-timestamp speech is hallucination-free.

Speaker continuity is a separate blocker. In both short-window strategies,
raw speaker ID 1 initially aligned with AMI speaker D and then speaker A. These
are reference-word/segment overlap votes, not a DER score. Reusing raw speaker
IDs as persistent person labels would merge different people. Growing prefixes
showed no dominant-reference ID switches in this minute, but that is not general
speaker-quality acceptance.

One separate **five-minute** whole-clip pass completed in **31.664 seconds**,
WER **20.42% (156/764)**, peak process RSS **1868.6 MiB**. This confirms that full
prefix regeneration will outgrow a five-second update budget at the existing
development limit. It does not predict bounded rolling-window latency.

Conclusion: local chunked text updates are computationally feasible. A live app
path still needs a resident, cancellable worker; validated boundary stitching;
silence/invalid-response handling; and speaker continuity across windows. The
current after-Stop mode remains unchanged so this experiment does not weaken
speaker separation or add an automatic second transcription pass.

Reproduce the bounded local experiment (requires the installed runtime):

```bash
.venv/bin/python scripts/benchmark_moss_live.py --seconds 60
.venv/bin/python scripts/benchmark_moss_live.py --seconds 300 --strategies whole \
  --output data/phase1/moss-live-scaling-300
# Reanalyze cached results without GPU inference:
.venv/bin/python scripts/benchmark_moss_live.py --seconds 60 --rescore
```

Detailed outputs, including per-window text, timings, rejected responses and raw
speaker-ID/reference votes, are in ignored `data/phase1/moss-live-trial/results.md`
and `results.json`. The scaling result is under
`data/phase1/moss-live-scaling-300/`. No private recording was replayed.
