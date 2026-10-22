# Five-minute overlap review and dedicated diarizer — 2026-10-21

Status: **automated error review and community-1 comparisons on original and
Opus audio completed**. Human listening review,
word-level hybrid attribution, and broader validation remain pending.

## Setup

All tests use ES2004a seconds 300–600 and the same imported manual labels. MOSS
keeps float16 + SDPA; its prior transcripts are reused without rerunning ASR.
Community-1 revision `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` runs locally via
pyannote.audio, CPU / four threads, at the upstream pipeline's default float32
precision. This separate pipeline does not change MOSS's float16 default.

No reference speaker count, identities, transcript, or reference turns are passed
to the inference pipeline. Regular overlapping diarization is used for scoring.
The exclusive-speaker output is saved only for future text/timestamp alignment.
The model was downloaded after the user accepted its gated terms and authenticated
locally. Cached reruns can use offline mode and the pinned revision.

## Scoring correction

Scoring now records protocol `same-speaker-union-reference-collar-v2`:

1. Union overlapping/adjacent spans belonging to the same speaker. Repeated
   timestamps for one voice must not be counted as simultaneous distinct voices.
2. Apply the 0.25 s reference-boundary collar, retaining real cross-speaker overlap.
3. Select one global Hungarian speaker mapping on those evaluated regions.
4. Reuse that mapping for the overlap-only identification diagnostic.

The original MOSS output contained 0.71 s of duplicate same-speaker span coverage.
Normalization reclassifies 0.71 s from confusion to missed coverage; total DER is
unchanged on this segment. Historical reports are retained. The earlier scorer
also selected the overlap mapping without the collar; the corrected scorer uses
one consistent mapping. Other historical reports should be rescored with v2 before
mixing results across protocols.

Independent timeline accounting in `review_diarization.py` reproduces the scoring
library's missed/confusion/false-alarm/total speaker-time within 1e-6 s. Tests cover
same-speaker duplicates, real overlap, speaker permutations, collars, false alarms,
and lexical interval unioning.

## Original five-minute comparison

| Metric | MOSS labels | Community-1 regular labels |
| --- | ---: | ---: |
| Diagnostic DER | 32.16% | 27.57% |
| Overlap-only identification error | 47.33% | 40.57% |
| Missed reference speaker-time | 76.18 s | 57.19 s |
| Confusion | 4.50 s | 8.28 s |
| False alarm | 0.74 s | 4.32 s |
| Detected/reference speaker count | 4/4 | 4/4 |
| Inference time (load excluded) | 82.93 s on MPS | 127.23 s on CPU |

Community-1 reduces DER by 4.59 percentage points (about 14.3% relative), primarily
by recovering missed coverage. Confusion and false alarms increase. Timing is not
a hardware-matched speed comparison: MOSS uses MPS and community-1 uses CPU.
Community-1 produces no transcript, so there is no new WER result from this run.

## Codec robustness comparison

| Five-minute condition | MOSS diagnostic DER | Community-1 diagnostic DER | MOSS overlap error | Community-1 overlap error |
| --- | ---: | ---: | ---: | ---: |
| Original PCM | 32.16% | 27.57% | 47.33% | 40.57% |
| Opus 24 kbps | 35.07% | 27.94% | 54.09% | 40.64% |

The dedicated diarizer's DER increases by 0.37 percentage points under this codec
condition versus 2.91 points for MOSS. It recovers about 28.04 more missed
speaker-seconds than MOSS on the Opus copy (57.92 s versus 85.96 s missed).
Both dedicated runs identify four speakers automatically. The Opus run took
125.13 s on CPU; no GPU or mixed-precision diarizer speed claim follows from it.

These are matched conditions on one five-minute recording, not independent
meetings or proof of general robustness. No speech-recognition WER is attributed
to the diarizer. MOSS transcripts remain unchanged (20.31% original WER, 18.48%
Opus WER); a correctly aligned hybrid output has not yet been evaluated.

## What the missing time contains

For original MOSS labels, 49.18 of 76.18 missed speaker-seconds occur during
reference overlap (64.6%). Of the 76.18 missed speaker-seconds:

- 58.77 s have manually labeled lexical speech evidence with unambiguous identity
  accounting (77.2% of all missed speaker-time).
- 16.61 s have no lexical word interval in those missed reference spans. This can
  reflect broad transcription-segment boundaries, pauses, or nonlexical vocal sounds;
  it is not proof that the reference is wrong or the audio is silent.
- 0.80 s involve simultaneous identity confusion and undercoverage, making lexical
  allocation to missed-versus-confused speaker-time ambiguous.

Community-1 lowers unambiguous missed lexical speaker-time to 41.18 s. Thus the
observed improvement is not explained solely by trimming reference gaps. These
are automatic annotation/timeline checks; no human listening review is claimed.

The codec MOSS result has 85.96 missed speaker-seconds, including 56.74 s during
overlap. Unambiguous missed lexical evidence is 67.52 s. This supports investigating
missed secondary speech rather than treating the whole DER increase as a label-boundary
artifact. The raw and codec comparisons use identical manual references.

Five short listening clips per report and the highest-priority timestamp windows
are saved locally in `data/corpus-v1/diarization/review-*.json` and adjacent WAVs.
These are review aids, not additional training/evaluation meetings.

## Interpretation and next step

Keep original-audio float16 + SDPA as the development default. Community-1 is a
promising separate speaker-turn provider, but do not replace the product stack
based on one meeting. A hybrid prototype should preserve MOSS text and align it
to the dedicated speaker turns, followed by word-attribution evaluation.

Diarization cannot recover words the ASR never transcribed. MOSS's segment timestamps
are also not word-level forced alignment. Simply attaching exclusive turns to large
text segments does not prove better speaker-attributed transcription; this step
still needs implementation and testing. The speaker registry remains separate.

### Rejected separation experiment

SepFormer-WHAMR was tested on three short overlap clips within ES2004a 300–600 s.
On the two two-speaker clips (46 reference words), clip-local speaker-permuted WER
worsened from 23.91% to 67.39%; the four-participant stress clip also regressed.
Separation took 1.20–1.37 s per clip on CPU and roughly doubled total processing
with MOSS float16. This small, oracle-selected test does not establish that all
separation approaches fail. Reject this implementation and retain original audio.
Its prototype, dedicated runtime/model, generated audio, and detailed reports
were removed after the decision. Recovering missing overlapping words remains open.

## Reproduction

```bash
PYANNOTE_METRICS_ENABLED=0 MPLCONFIGDIR=/tmp/scribeloop-mpl \
  .venv/bin/python scripts/benchmark_diarizer.py \
  --output data/corpus-v1/diarization/community1-original.json

MPLCONFIGDIR=/tmp/scribeloop-mpl .venv/bin/python scripts/score_moss.py \
  data/corpus-v1/diarization/community1-original.json

MPLCONFIGDIR=/tmp/scribeloop-mpl .venv/bin/python scripts/review_diarization.py \
  data/corpus-v1/moss/segment-300-600-fp16-sdpa.json \
  --output data/corpus-v1/diarization/review-original.json
```

Choose fresh output paths for reruns; existing reports are not overwritten. To
rescore an existing MOSS report with v2, use `score_moss.py --output NEW_PATH`.
The dedicated runner defaults to the fixed five-minute source report, validates its
audio hash, and refuses inputs longer than five minutes. It accepts `--device mps`
for a future hardware comparison, but MPS diarizer performance is not measured yet.

All audio, model weights, detailed reports, reference labels, and credentials remain
local. No credentials are copied into benchmark reports or project docs. DER remains
diagnostic because the source labels are transcription segments rather than an
official AMI diarization scoring recipe. Multi-meeting and human validation remain
necessary before target sign-off.

## Completed and next work

- [x] Run dedicated diarization on original and Opus five-minute inputs
- [x] Compare overall and overlap error with automatic speaker counts
- [x] Partition misses using manually labeled lexical timing evidence
- [x] Save short listening clips and original model results for follow-up review
- [x] Correct and version scoring; 41 tests pass locally
- [ ] Human listening review of saved clips and validation of the reference recipe
- [ ] Prototype MOSS-text alignment to dedicated speaker turns and measure word attribution
- [ ] Repeat on another meeting's five-minute excerpt before product model selection

Cached Opus rerun command (choose a new output path):

```bash
HF_HUB_OFFLINE=1 PYANNOTE_METRICS_ENABLED=0 MPLCONFIGDIR=/tmp/scribeloop-mpl \
  .venv/bin/python scripts/benchmark_diarizer.py \
  --reference-report data/corpus-v1/moss/segment-300-600-fp16-opus-24k.json \
  --revision 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee \
  --output data/corpus-v1/diarization/community1-opus.json
```
