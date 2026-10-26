# MOSS-Transcribe-Diarize local evaluation — 2026-10-21

> Historical Python/PyTorch evaluation. The app now uses cloud streaming only;
> the [Local MOSS Q6_K experiment](LOCAL_MOSS.md) is historical.
> Float16 defaults below apply to the old harness.

## Progress and current defaults

**Initial evaluation and five-minute development setup: done (2026-10-21).**
The MOSS production backend and Phase 0b release criteria remain pending.

- [x] Pinned model/helper download and isolated local runtime
- [x] Matched MOSS/Whisper two-minute transcription comparison
- [x] Float16 compatibility, timing, memory, and diagnostic DER checks
- [x] Decoder-only 8-bit quality/memory comparison on two excerpts
- [x] Reject failed all-layer 8-bit generation and restore the working loader
- [x] Five-minute SDPA/Metal flash WER, timing, memory, and diagnostic DER comparison
- [x] Original/Opus dedicated-diarizer comparison and automatic overlap review ([results](DIARIZATION_REVIEW.md))
- [x] Default to a five-minute crop, float16 + SDPA, no quantization
- [x] Read only the selected interval and require `--full` for full-recording runs
- [x] Five-minute Opus 24 kbps comparison and input timing/hash checks ([results](CODEC_EVALUATION.md))
- [ ] Production integration, broader corpus validation, and live-latency evaluation

Current development command is at the end of this document. Full-meeting MOSS
runs are deferred while iterating; no full-meeting MOSS result is claimed.

Model: `OpenMOSS-Team/MOSS-Transcribe-Diarize`, revision
`704aa4a9c304e8520be88901e0d1960158ef5b15`.
Official helper source: `OpenMOSS/MOSS-Transcribe-Diarize`, commit
`e3bdd93f8ad58418e04659818efd5fd87a0bc486`.

The model and official custom inference code were downloaded locally. Test
packages live in `data/moss-runtime`, leaving the main Python environment
unchanged. Runtime: Python 3.12, PyTorch 2.14.1, Transformers 5.20.2, Apple MPS,
float16 by default, SDPA attention, deterministic generation. The initial
comparison below used float32. The model is evaluated as an
offline candidate, not as an incremental audio streaming backend.

## Matched excerpt: ES2004a, seconds 300–420

The excerpt was selected before seeing either result. Both models process exactly
the same PCM audio and use the same 289-word normalized reference. Words crossing
the excerpt boundaries are excluded. Ground truth uses the existing AMI manual
annotation converter and onset-serialized WER, so the usual overlap limitations
apply. This is not an official AMI benchmark.

| Metric | MOSS | Whisper-tiny |
| --- | ---: | ---: |
| Word errors | 62 | 97 |
| WER | 21.45% | 33.56% |
| Processing time | 32.09 s | Not measured in matched scoring |
| Audio duration | 120 s | 120 s |

MOSS produced 31 parsed segments and 830 generated tokens without hitting the
4,096-token limit. The final hypothesis ends at 106.34 s; the final reference
word starts at 105.88 s, followed by silence in the remaining excerpt.

Peak process RSS: 4.35 GB (decimal). MPS driver allocation at completion: 4.98 GB.
These are different memory measurements and must not be summed; the driver
snapshot is not peak GPU memory.

Speaker diagnostics use the imported transcription-segment boundaries, not an
official AMI diarization annotation recipe. With a 0.25 s collar and overlap
included, optimal speaker-label matching gives:

- Four reference speakers versus five generated speaker labels.
- Diagnostic DER: 38.24%.
- Missed reference speaker-time: 33.08 s; confusion: 3.20 s; false alarm: 0 s.
- Overlap-only identification error with the global speaker mapping: 58.17%.
- No invalid or out-of-bounds generated segments in this excerpt.

The transcription result is promising. The speaker result does not support
replacing the planned diarization/registry stack yet. Missing speaker-time can
include differences between transcription-segment reference spans and generated
speech boundaries, in addition to missed overlapping speech.

The tables above and historical optimization tables below retain their original
scorer results. The corrected v2 protocol unions same-speaker spans and uses a
collar-aware global mapping; see [the overlap review](DIARIZATION_REVIEW.md) for
current component accounting and the dedicated diarizer comparison. Overall
five-minute MOSS DER is unchanged by this correction.

## Precision comparison

| Two-minute excerpt | Float32 | Float16 |
| --- | ---: | ---: |
| WER | 21.45% | 21.45% |
| Processing s | 32.09 | 19.70 |
| Peak process RSS, decimal GB | 4.35 | 4.16 |
| MPS driver allocation at end, decimal GB | 4.98 | 2.93 |

## Full meeting

The full float32 run was stopped after 3,584 generated tokens and about 514 s
of processing, after the user questioned precision and speed. It did not finish,
so no full-meeting WER or DER is available. Do not treat its saved input/reference
files as a completed evaluation.

A matched float16 excerpt test then completed successfully: 62/289 errors
(21.45% WER) in 19.70 s versus 32.09 s for float32, approximately 1.63× faster.
This is not a prediction of full-context speed. The benchmark default is now
float16; float32 remains selectable for diagnostics.

Transformers [Metal 8-bit weight quantization](https://huggingface.co/docs/transformers/v5.6.1/en/quantization/metal)
was tested with MOSS custom code: decoder-only quantization passed the excerpt
comparisons; all-layer quantization failed. Details appear below.

## Reproduction

Use the downloaded pinned model in `models/MOSS-Transcribe-Diarize` and the
pinned official helper checkout in `data/moss-source`. Model provenance is local
at `data/moss-model-provenance.json`. The isolated dependencies are installed
under `data/moss-runtime` and reuse the existing environment's PyTorch/audio stack.

```bash
PYTHONPATH=data/moss-runtime:data/moss-source HF_HOME=data/moss-hf \
  .venv/bin/python scripts/benchmark_moss.py \
  --start-s 300 --duration-s 120 --device mps --dtype float32 \
  --max-new-tokens 4096 --output data/corpus-v1/moss/excerpt-300-420-mps-fp32.json

MPLCONFIGDIR=/tmp/scribeloop-mpl .venv/bin/python scripts/score_moss.py \
  data/corpus-v1/moss/excerpt-300-420-mps-fp32.json --whisper

PYTHONPATH=data/moss-runtime:data/moss-source HF_HOME=data/moss-hf \
  .venv/bin/python scripts/benchmark_moss.py --warmup \
  --max-new-tokens 8192 --output data/corpus-v1/moss/dev-five-minute.json
```

Reports refuse overwrite. Choose a new output path for reruns. The scoring helper
requires the existing pyannote.metrics installation; `--whisper` requires the
cached Whisper-tiny model. All audio, references, raw transcripts, model code,
weights, dependencies, and detailed reports remain in ignored local directories.

Development policy: use the fixed five-minute ES2004a interval at seconds
300–600 for optimization. Float16 and SDPA remain defaults. Full-meeting runs
are reserved for later validation and require the explicit `--full` flag.
The full-meeting result remains pending.

## Accuracy-preserving optimization tests

A second excerpt (seconds 600–720) was selected before running its paired tests.
Whisper was not rerun on that excerpt, so the direct MOSS-versus-Whisper conclusion
still applies only to seconds 300–420. Both optimization pairs use matched audio,
references, deterministic decoding, and one-token warmup; model loading and warmup
are excluded from the measured inference times. Runs used for timing were serial.

| Excerpt / mode | WER | Processing s | Diagnostic DER |
| --- | ---: | ---: | ---: |
| 300–420, float16 | 21.45% | 17.74 | 38.12% |
| 300–420, 8-bit decoder + float16 encoder | 21.45% | 18.67 | 38.12% |
| 600–720, float16 | 15.89% | 24.78 | 13.56% |
| 600–720, 8-bit decoder + float16 encoder | 15.89% | 25.87 | 13.56% |

Normalized hypothesis text was identical within each pair, not merely equal WER.
Both modes also had equal diagnostic DER within each pair. This is evidence on
two excerpts from one meeting, not a guarantee for other audio or long context.

| Memory measure | Float16 | Decoder-only 8-bit | Reduction |
| --- | ---: | ---: | ---: |
| Model parameters and buffers | 1.817 GB | 1.404 GB | 22.7% |
| MPS driver allocation at completion | 2.980 GB | 2.594 GB | 13.0% |
| Peak process RSS | ~4.168 GB | ~2.475 GB | ~40.6% |

GB is decimal. These measures overlap on unified-memory Macs and must not be
added. GPU allocation is an end snapshot, not a peak. Peak RSS includes loading;
quantized loading goes directly to MPS while the working float16 path loads via
CPU, so the RSS difference is not a pure measure of weight compression.

Decoder quantization uses Transformers MetalConfig, 8 bits, group size 64,
`kernels` 0.17.2, and the integration's version-1 Metal kernel. The Whisper encoder,
VQ adaptor, embeddings, and output head remain floating point. Decoder-only 8-bit
is about 4.8% slower across these two warmed tests; it is a memory option, not a
measured speed optimization over float16.

### Rejected attempts

- Quantizing all eligible linear layers, including the audio encoder/adaptor,
  produced repeated exclamation marks, no parsed transcript, and hit 4,096 tokens.
  This configuration is rejected. The apparent 100% WER in its raw report is an
  empty-parsed-output artifact, not a valid completed recognition benchmark.
- Direct-device loading for unquantized float16 terminated during weight loading.
  The working CPU-then-MPS path was restored; direct loading is not recommended
  for this configuration. The exact exit status of that attempt was not retained.
- One float16 timing overlapped the failed all-layer run. It is explicitly excluded;
  the serial rerun supplies the 17.74 s measurement above.

The benchmark now returns failure after saving diagnostics if generation reaches
its token cap or produces no parseable segments. Those checks detect these failures;
they do not prove semantic completeness or absence of hallucinations.

### Recommended modes

- **Speed:** float16 encoder and decoder. This remains the benchmark default.
- **Lower memory:** float16 encoder/adaptor plus 8-bit decoder, using
  `--quant-bits 8 --quant-scope decoder`. Keep the floating-point reference mode.
- **Do not select:** all-layer 8-bit. Four-bit quantization has not been tested.

Example memory-mode run (choose new output paths for reruns):

```bash
PYTHONPATH=data/moss-runtime:data/moss-source HF_HOME=data/moss-hf \
  .venv/bin/python scripts/benchmark_moss.py \
  --start-s 600 --duration-s 120 --device mps --dtype float16 \
  --quant-bits 8 --quant-scope decoder --warmup --max-new-tokens 4096 \
  --output data/corpus-v1/moss/excerpt-600-720-q8-decoder-warm-serial.json
```

The initial Metal attention comparison is complete (see the five-minute table
below). An optimized Apple-native runtime remains a possible future investigation,
using the same transcript and speaker-quality checks. Shorter
independent chunks may save time/memory but require overlap handling and speaker
identity reconciliation; they are not a free replacement for full-context input.
Full-meeting speed, peak GPU memory, and accuracy are still unmeasured for the
optimized modes. Full-corpus evaluation remains required before changing defaults
in the product or signing off the provisional quality targets.

## Fixed five-minute development benchmark

Per user instruction, optimization now uses ES2004a seconds 300–600 rather than
the full 17.5-minute meeting. The CLI defaults to that exact interval, float16,
SDPA attention, and no quantization. It reads only the requested audio interval
instead of loading the whole meeting into memory. Warmup also uses that interval.
The explicit `--full` flag is reserved for later validation; it is not run as part
of normal development.

Existing serial five-minute comparisons:

| Float16 attention backend | WER | Diagnostic DER | Processing s | End MPS driver GB |
| --- | ---: | ---: | ---: | ---: |
| SDPA (default) | 20.31% | 32.16% | 82.93 | 3.718 |
| Metal flash (experimental) | 20.31% | 32.11% | 78.51 | 3.617 |

These runs used 763 normalized reference words and 155 errors each. The small
speed difference is from one paired experiment, not a reliable general speedup.
SDPA produced four speaker labels for four reference speakers; Metal flash
produced five labels. Overlap-only diagnostic identification error was 47.33%
and 47.12%, respectively, using the global speaker mapping. Equal WER therefore
does not establish identical speaker attribution. Keep SDPA as the default;
the Metal kernel remains an opt-in experiment.

```bash
PYTHONPATH=data/moss-runtime:data/moss-source HF_HOME=data/moss-hf \
  .venv/bin/python scripts/benchmark_moss.py --warmup \
  --max-new-tokens 8192 --output data/corpus-v1/moss/dev-five-minute.json
```
