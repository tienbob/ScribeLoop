# ES2004a ASR investigation — 2026-10-21

The initial 57.85% WER is reproducible, but adapter correctness and recognition
accuracy are separate issues. This is an exploratory mixed-headset AMI result,
not official AMI scoring or a corpus-wide product result.

Status: initial investigation, controlled ablations, adapter fixes, and regression
checks are complete. Follow-up MOSS trials and the fixed five-minute float16
benchmark are recorded in [MOSS evaluation](MOSS_EVALUATION.md). Model selection
and release target sign-off remain open.

## Findings

1. **Recognition omissions dominate.** An optimal word edit alignment of the
   initial hypothesis gives 388 substitutions, 1,223 deletions, and 14 insertions
   against 2,809 normalized reference words. The hypothesis has only 1,600 words.
   Tie-breaking can change the individual edit categories, but not total WER.
2. **Endpoint detection was never enabled.** The installed sherpa-onnx API defaults
   `enable_endpoint_detection` to false. The original adapter produced one final
   chunk for the entire 17.5-minute meeting. This is a live confirmation problem;
   enabling endpoints alone does not repair WER.
3. **Model precision selection was nondeterministic.** `.int8.onnx` also matches
   the suffix `.onnx`. The original run used float encoder/decoder files and an
   int8 joiner. Explicit float-only selection slightly worsens WER, so this bug
   does not explain the initial accuracy result. It is still a reproducibility bug.
4. **Fresh streams discard buffered audio at endpoints.** Replacing the stream
   scores worse than resetting the decoder on the existing stream. The existing
   stream must retain its feature buffers/lookahead. The fix follows the
   [upstream streaming reset pattern](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/streaming_server.py).
5. **Overlap and normalization are insufficient explanations.** 764 of 2,614
   original lexical word tokens (29.2%) overlap a word from another speaker.
   That is a token fraction, not an audio-time fraction. However, Sherpa still
   scores 55.11% (151/274) on six oracle-selected, non-overlapping transcription
   segments lasting at least four seconds, totaling 100.85 s. These excerpts are
   small and biased, and oracle boundaries are not a live system capability.
   Joining apostrophes before normalization changes the original WER to 57.86%,
   essentially unchanged; normalization alone is not the solution.
6. **A different recognizer is materially better on the same reference.** Cached
   Whisper-tiny, CPU int8, scores substantially below Sherpa in both our existing
   12-second chunked adapter and a whole-file offline pass. The Sherpa model's
   [upstream source](https://huggingface.co/Zengwei/icefall-asr-librispeech-streaming-zipformer-2023-05-17)
   is a LibriSpeech model. Domain/model mismatch is a plausible explanation for
   poor conversational performance; these experiments do not isolate its training
   data from architecture, model size, or decoder effects.

## Controlled comparisons

All runs use the same audio hash and reference hash. sherpa-onnx 1.13.8 uses two
CPU threads. Whisper-tiny uses faster-whisper CPU int8 / two threads and English.
Model loading is excluded. Timing is diagnostic rather than a controlled resource
benchmark; a short oracle-clip experiment briefly overlapped the first Whisper run.

| Condition | WER | Word errors | Final chunks | Processing s |
| --- | ---: | ---: | ---: | ---: |
| Sherpa historical: mixed precision, endpoints off | 57.85% | 1625 | 1 | 32.62 |
| Sherpa float: endpoints off | 58.10% | 1632 | 1 | 32.47 |
| Sherpa float: endpoints, fresh stream | 60.70% | 1705 | 108 | 30.85 |
| Sherpa float: endpoints, retained stream | 57.99% | 1629 | 114 | 31.00 |
| Whisper tiny: 12 s chunks, beam 1, no VAD | 34.67% | 974 | 274 | 33.42 |
| Whisper tiny: whole file, beam 1, no VAD | 35.85% | 1007 | 310 | 18.97 |
| Whisper tiny: whole file, beam 5, no VAD | 34.89% | 980 | 343 | 28.89 |
| Whisper tiny: whole file, beam 5, VAD | 34.10% | 958 | 256 | 28.74 |

Whisper beam 5 improves whole-file WER from 35.85% to 34.89%; adding VAD
reduces it further to 34.10% on this meeting. The existing chunked baseline
scores 34.67%, so these results do not establish that whole-file decoding alone
is superior. The large observed improvement is associated with changing the
recognizer, not just changing chunk size.

## Applied fixes

- Explicit, deterministic float/int8 file selection; ambiguous model directories
  fail instead of silently selecting an arbitrary checkpoint.
- Enable endpoint detection and reset the decoder on the same stream.
- Add adapter regression tests for precision, endpoint configuration, stream
  retention, flush, and ambiguous models.

A full rerun through the corrected production adapter reproduced 1,629 word
errors (57.99% WER); its local report is
`data/corpus-v1/reports/ami-es2004a-sherpa-corrected-001.json`.

These fixes restore the intended streaming contract. They do **not** meet the
accuracy target, and should not be presented as an accuracy improvement.

## Proposed solution

1. Use Whisper as the current accuracy reference rather than choosing this
   LibriSpeech Zipformer solely for speed. Keep the corrected Sherpa adapter as
   a latency/throughput candidate until the multi-meeting bake-off is complete.
2. Benchmark `small.en` and a stronger offline model next, using full-file or
   context-preserving decoding. Compare beam search and VAD independently.
   On Apple Silicon, include whisper.cpp/Metal in the planned Phase 0a bake-off;
   the present faster-whisper runs are CPU measurements. No promise that a larger
   model will hit the provisional 15% target follows from this one meeting.
3. Keep the architecture's two passes: provisional live text, then a stronger
   offline pass to correct text and regenerate final summaries. Avoid treating
   raw 12-second greedy chunks as final-quality transcripts. LocalAgreement and
   production VAD integration remain future work.
4. Fix the evaluation recipe before signing off targets: retain timed words and
   speaker identity, report overlap and non-overlap conditions separately, and
   establish an overlap-aware multi-speaker scoring protocol once diarized
   hypotheses exist. Keep the existing onset-serialized WER as a named diagnostic,
   rather than silently changing its reference to improve the score.
5. Repeat on multiple held-out meetings and codec variants. One meeting and six
   oracle excerpts cannot establish general performance or justify target changes.

## Reproduction and artifacts

```bash
.venv/bin/python scripts/investigate_ami.py --case original
.venv/bin/python scripts/investigate_ami.py --case fp32
.venv/bin/python scripts/investigate_ami.py --case endpoint-fresh
.venv/bin/python scripts/investigate_ami.py --case endpoint-reset
.venv/bin/python scripts/investigate_ami.py --case whisper-tiny-chunked
.venv/bin/python scripts/investigate_ami.py --case whisper-tiny-offline-beam1
.venv/bin/python scripts/investigate_ami.py --case whisper-tiny-offline-no-vad
.venv/bin/python scripts/investigate_ami.py --case whisper-tiny-offline
```

The importer and model files must already be present. Whisper ablations use the
local model cache only. Reports refuse overwrite; use `--output-dir` for reruns.
The diagnostic script retains historical adapter behavior for original/fresh
stream conditions, independently of the applied production fixes.

Reports and diagnostic transcripts are local under
`data/corpus-v1/investigation/` (gitignored). Files include exact Sherpa model
hashes, configuration, audio/reference hashes, and final chunks. The oracle
excerpt and edit-count reports also live there; those ad hoc checks were run
locally and are not yet exposed as script CLI modes. No transcript text or audio
is committed with this report.
