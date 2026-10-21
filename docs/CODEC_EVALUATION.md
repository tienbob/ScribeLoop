# Five-minute Opus robustness check — 2026-10-21

Status: **development codec preparation, timing checks, and one matched MOSS run
completed**. Corpus-wide codec coverage and human annotation review remain open.

The fixed ES2004a interval is seconds 300–600: mono 16 kHz PCM16, 4,800,000
samples, with the same 763-word reference and speaker labels used by the original
benchmark. The copy passes through FFmpeg libopus at 24 kbps with the `voip`
application setting, then decodes back to mono 16 kHz PCM16. MOSS remains float16
+ SDPA, no quantization, deterministic generation, with warmup excluded.

## Timing evidence

- Exact decoded sample count matches the source.
- Five-second energy-envelope windows at the start, middle, and end show zero
  lag at 10 ms resolution, with correlations 0.976, 0.993, and 0.979.
- The minimum envelope correlation is 0.9 and maximum detected lag is zero frames.
- Source and decoded WAV hashes bind the timing report to the actual inputs.
  The benchmark refuses failed timing reports, changed inputs, or changed formats.

The first raw waveform correlation attempt was rejected. At this bitrate,
waveform phase/filter changes made raw correlation unsuitable as a timing test.
The energy-envelope method checks speech timing without requiring sample-level
waveform similarity. Its accepted artifact lives separately in
`data/corpus-v1/codec-dev-envelope/`; the earlier rejected attempt remains in
`data/corpus-v1/codec-dev/` for audit.

These are three automatic local checks, not proof of sub-frame alignment or a
human review of the whole recording. Only codec compression is simulated;
packet loss, jitter, echo, and real meeting-service processing are not included.

## Results

| Metric | Original PCM | Opus 24 kbps round trip |
| --- | ---: | ---: |
| Reference words | 763 | 763 |
| Word errors | 155 | 141 |
| WER | 20.31% | 18.48% |
| Diagnostic DER | 32.16% | 35.07% |
| Overlap-only identification error | 47.33% | 54.09% |
| Missed reference speaker-time (v2) | 76.18 s | 85.96 s |
| Generated/reference speaker count | 4/4 | 4/4 |
| Invalid/out-of-bounds segments | 0 | 0 |
| Processing time (excluding load/warmup) | 82.93 s | 77.19 s |

The v2 scorer unions duplicate same-speaker spans and uses a collar-aware global
mapping. On this segment total DER is unchanged; 0.71 s is reclassified from
confusion to missed speaker-time in the original MOSS condition. See the
[overlap and dedicated-diarizer review](DIARIZATION_REVIEW.md).

DER uses the diagnostic transcription-segment references, a 0.25 s reference
collar, and overlap included. Overlap-only scoring keeps the global speaker
mapping. These are not official AMI scores. WER uses the same onset-serialized
Unicode normalization protocol for both conditions.

WER improved by 1.83 percentage points on this sample, while diagnostic DER
worsened by 2.91 points. The extra missed speaker-time is consistent with the
worse overlap diagnostic, but this experiment does not isolate a causal mechanism.
Different generated transcript lengths also affect processing time. Do not infer
that compression generally improves ASR or speed from this single comparison.
The original PCM / float16 + SDPA development default remains unchanged.

## Reproduction

The source WAV is the existing five-minute benchmark input. All files remain
local under ignored `data/` directories.

```bash
.venv/bin/python scripts/prepare_codec.py \
  --output-dir data/corpus-v1/codec-dev-envelope

PYTHONPATH=data/moss-runtime:data/moss-source HF_HOME=data/moss-hf \
  .venv/bin/python scripts/benchmark_moss.py \
  --audio-override data/corpus-v1/codec-dev-envelope/segment-300-600-fp16-sdpa-opus-24k.wav \
  --audio-provenance data/corpus-v1/codec-dev-envelope/segment-300-600-fp16-sdpa-opus-24k.json \
  --warmup --max-new-tokens 8192 \
  --output data/corpus-v1/moss/segment-300-600-fp16-opus-24k.json

MPLCONFIGDIR=/tmp/scribeloop-mpl .venv/bin/python scripts/score_moss.py \
  data/corpus-v1/moss/segment-300-600-fp16-opus-24k.json
```

Existing outputs are not replaced: choose fresh output paths for reruns. The
preparation command accepts canonical mono 16 kHz PCM16 audio up to five minutes,
saves encoder commands/version and all file hashes, and fails if timing checks
fail. The benchmark requires provenance for audio replacement and checks it
against the original selected AMI interval before retaining the reference labels.

## Follow-up

- [x] Add timing/hash validation and regression tests (34 tests pass overall)
- [x] Prepare and score the fixed five-minute 24 kbps development variant
- [ ] Review annotation alignment manually and expand to multiple held-out meetings
- [ ] Compare additional codec conditions and establish joint WER/DER acceptance gates
- [ ] Validate the diarization reference recipe before release target sign-off
