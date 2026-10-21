# Phase 0b: corpus and baselines

Status (2026-10-21): **development checkpoint closed at user request**.
Tooling and the scoped public-data experiments are complete. Original Phase 0b
release exit criteria are deferred, not passed. Phase 0a still has open decisions.

## Closeout decisions

- Keep original audio, MOSS float16 + SDPA, and no quantization as development defaults.
- Keep ES2004a 300–600 s as the bounded development input; full-meeting runs stay deferred.
- Retain community-1 as an evaluated offline diarization candidate; hybrid attribution
  and product integration remain unvalidated.
- Reject the tested SepFormer separation approach; its implementation and artifacts
  have been removed. Keep only the decision and measurements.
- Stop further experiments for this checkpoint. Resume validation explicitly when
  the deferred corpus, latency, and sign-off work is scheduled.

## Completed work

- [x] Corpus manifest and validation: consent declarations, file presence, IDs,
  variants, readable audio, and timed speaker labels (`evaluation/corpus.py`)
- [x] Versioned Unicode WER scoring and corpus ASR reports with input hashes
  (`evaluation/metrics.py`, `scripts/evaluate_corpus.py`)
- [x] Import one full public AMI meeting, ES2004a, with manual annotations,
  pinned checksums, license, and provenance (`scripts/import_ami.py`)
- [x] Initial Sherpa/Whisper runs and controlled ASR error investigation
  ([results](ASR_INVESTIGATION.md)); fix endpoint handling and model-file selection
- [x] Run MOSS locally on Apple MPS in an isolated dependency directory
- [x] Compare float32/float16, decoder-only 8-bit, and rejected all-layer 8-bit
- [x] Implement diagnostic DER scoring, including overlap-only reporting with
  the global speaker mapping (`scripts/score_moss.py`)
- [x] Compare float16 SDPA/Metal flash attention on the same five-minute segment
- [x] Set development defaults to ES2004a 300–600 s, float16 + SDPA, no quantization;
  require explicit `--full` for later full-recording validation
- [x] Read only the requested audio interval; verify the crop is byte-identical
  to the existing five-minute benchmark audio
- [x] Five-minute Opus 24 kbps round trip with timing/hash checks and matched
  MOSS WER/diagnostic DER scoring ([results](CODEC_EVALUATION.md))
- [x] Automated overlap/lexical-evidence review, corrected v2 speaker scoring,
  and dedicated community-1 comparisons on original/Opus audio
  ([results](DIARIZATION_REVIEW.md))
- [x] Reject and remove unsuccessful SepFormer separation prototype
  ([decision](DIARIZATION_REVIEW.md#rejected-separation-experiment))
- [x] Regression suite: 41 tests passed; changed Python tooling passed Ruff

Experiment cleanup: retain a brief decision and key measurements for rejected
approaches; remove their prototype code, dedicated dependencies, model weights,
and generated artifacts. Keep supported alternatives only when they serve an
identified use case.

The canonical development segment has **763 normalized reference words**.
Float16 + SDPA scored **155 errors / 20.31% WER**, **32.16% diagnostic DER**,
and **82.93 s processing**. See [MOSS evaluation](MOSS_EVALUATION.md) for complete
results, memory measurements, failed experiments, and limitations.

The five-minute Opus development copy scored **18.48% WER / 35.07% diagnostic
DER**, versus 20.31% / 32.16% on the original. Compression did not preserve speaker
quality on this sample; the original-audio benchmark remains the default.

The dedicated community-1 comparison scored **27.57% original DER / 27.94%
Opus DER** with automatic speaker counts, improving on MOSS speaker turns in
both conditions. Hybrid text attribution is not implemented or validated yet.

## Deferred work — outside this closed checkpoint

- [ ] Expand AMI coverage and import VoxConverse; the latter supplies speaker
  timing labels, not word-reference transcripts in its documented release
- [ ] Collect the ten consented, labeled self-recorded meetings required by §7
- [ ] Expand the completed development Opus check to corpus-wide variants and
  manually review aligned references
- [ ] Validate the DER/reference recipe and multi-speaker scoring protocol
- [ ] Measure paced live partial/confirmed latency and reference-machine resources
- [ ] Prototype and evaluate hybrid MOSS text / dedicated speaker-turn attribution
- [ ] Integrate a production MOSS backend; the current runner is experimental
- [ ] Complete broader baseline validation and obtain target-table sign-off

Full-meeting MOSS testing is **deferred during development**, per user instruction.
Use the fixed five-minute segment for optimization; shorter excerpts are retained
as historical accuracy/quantization controls. Whole-corpus release validation
remains a later requirement.

## Local corpus layout

Keep the manifest, audio, transcripts, speaker labels, consent records, and reports
under `data/corpus-v1/` (gitignored). Never put participant names in recording IDs.
Only consented real meetings count toward the minimum of ten original meetings.
Codec copies never count as additional meetings.

For each meeting:

1. Obtain each participant's consent before recording; retain the consent record
   locally. Use the agreed recording scope and retention period.
2. Record the meeting. Hand-correct a UTF-8 transcript in spoken order, preserving
   every spoken word. Do not score an ASR-generated transcript as ground truth.
3. Label local speakers with millisecond start/end times. Include simultaneous
   turns for overlap; do not collapse overlap to a single speaker. Have a second
   pass check both the transcript and the speaker boundaries.
4. Add a manifest entry. Use pseudonymous IDs and keep the source/license or
   consent provenance in `provenance`.
5. Create an Opus round-trip copy, inspect its duration/alignment, and label it
   as a variant of the original. Validate references against the decoded audio;
   codec delay may require boundary adjustments.

Example local `corpus.json` (paths are relative to the manifest):

```json
{
  "version": 1,
  "recordings": [
    {
      "id": "meeting-001",
      "source": "real",
      "audio": "meeting-001.wav",
      "reference": "meeting-001.txt",
      "speaker_labels": "meeting-001.speakers.json",
      "consent_confirmed": true,
      "consent_record": "meeting-001.consent.txt",
      "provenance": "Consented loopback meeting; manually corrected and reviewed"
    },
    {
      "id": "meeting-001-opus-24k",
      "source": "real",
      "audio": "meeting-001-opus-24k.wav",
      "reference": "meeting-001.txt",
      "speaker_labels": "meeting-001.speakers.json",
      "consent_confirmed": true,
      "consent_record": "meeting-001.consent.txt",
      "provenance": "Opus 24 kbps round trip; alignment checked",
      "variant_of": "meeting-001",
      "codec": "opus"
    }
  ]
}
```

Speaker label format:

```json
[
  {"speaker": "speaker-1", "start_ms": 500, "end_ms": 2400},
  {"speaker": "speaker-2", "start_ms": 2000, "end_ms": 3200}
]
```

Add AMI and VoxConverse subsets with `source` set to `ami` or `voxconverse`.
Obtain them from their dataset owners, review the terms, and record the release,
subset/split, original item IDs, and license in provenance. Keep evaluation splits
separate from development/tuning. Public availability alone is not provenance or
permission to redistribute the files. The pinned ES2004a importer is implemented
(see below); general dataset acquisition and VoxConverse import remain pending.

Example codec degradation (requires a local ffmpeg with libopus):

```bash
ffmpeg -i data/corpus-v1/meeting-001.wav -c:a libopus -b:a 24k data/corpus-v1/meeting-001.opus
ffmpeg -i data/corpus-v1/meeting-001.opus -ar 16000 -ac 1 data/corpus-v1/meeting-001-opus-24k.wav
```

## Validate and measure

```bash
.venv/bin/python scripts/evaluate_corpus.py data/corpus-v1/corpus.json --validate-only
.venv/bin/python scripts/evaluate_corpus.py data/corpus-v1/corpus.json --validate-only --require-ready
.venv/bin/python scripts/evaluate_corpus.py data/corpus-v1/corpus.json \
  --backend faster-whisper --model small --language en \
  --machine 'Machine A: hardware and RAM here' \
  --output data/corpus-v1/reports/asr-small-001.json
```

The first validation accepts an incomplete corpus for incremental collection.
`--require-ready` requires ten real originals, both public sources, and an Opus
variant of every original. Validation checks audio readability, file presence,
consent declarations, unique IDs, variant relationships, and label bounds; it
cannot verify consent authenticity, annotation quality, or codec provenance.

Reports include input hashes, backend version, machine description, per-recording
WER and throughput, and separate word-weighted aggregates for each source and
original/codec condition. They exclude transcript text. WER can exceed 100% with
insertions; an empty reference has undefined WER. The shared normalizer preserves
Unicode letters, lowercases, and replaces punctuation with spaces. Its version is
recorded; it is a simplified protocol, not Whisper's full language-specific
normalization. Compare only runs using the same protocol and corpus.

Fast replay measures processing throughput with model construction excluded.
`first_chunk_processing_s` is diagnostic processing time, **not live latency**.
Use separate paced replay instrumentation for partial/confirmed p50/p95 latency.
Model names and versions are recorded, but weight revisions are not hashed yet.

## Exit checklist

- [ ] At least ten consented, hand-labeled real meetings, plus AMI/VoxConverse subsets
- [ ] Reviewed Opus variants with aligned ground truth
- [ ] Actual ASR baseline runs on the reference machines
- [x] Diagnostic DER scorer and local excerpt baselines: 0.25 s reference collar,
  overlap included and also reported separately
- [ ] Validated DER recipe and reference-machine corpus baselines
- [ ] Paced partial and confirmed-text latency distributions
- [ ] Review baseline results and sign off or revise the §7 target table

No synthetic test result satisfies these exit criteria. The AMI import and local
diagnostic DER measurements are complete; corpus coverage, formal DER validation,
live latency, target sign-off, and self-recorded data collection remain outstanding.

## First imported public meeting

The local `data/corpus-v1/ami-es2004a.json` corpus contains one complete AMI
ES2004a meeting, using mixed-headset audio and manual annotations 1.6.2.
This audio condition is **not** a distant microphone or a VoIP loopback recording.
The annotation archive includes the CC BY 4.0 license, copied to
`data/corpus-v1/AMI-LICENCE.txt`; source URLs, revision, attribution, hashes, and
conversion limitations are saved in `ES2004a.provenance.json`.

To reproduce the import, download these two files into `data/corpus-v1/upstream/`:

- [Manual annotations 1.6.2](https://huggingface.co/datasets/FluidInference/ami-corpus-mirror/resolve/722d8891643e1e4dc62cfd0d198fa05a1646c3cc/annotations/ami_public_manual_1.6.2.zip)
- [ES2004a mixed-headset audio](https://huggingface.co/datasets/FluidInference/ami-corpus-mirror/resolve/722d8891643e1e4dc62cfd0d198fa05a1646c3cc/sdm/ES2004a.Mix-Headset.wav)

```bash
.venv/bin/python scripts/import_ami.py
.venv/bin/python scripts/evaluate_corpus.py data/corpus-v1/ami-es2004a.json \
  --backend sherpa-onnx --machine 'describe the local machine' \
  --output data/corpus-v1/reports/ami-es2004a-sherpa-001.json
```

The importer verifies pinned SHA-256 hashes before conversion and refuses to
replace an existing manifest. WER references serialize lexical words by onset,
excluding punctuation and nonlexical vocal sounds. Speaker turns use manual
transcription segments containing lexical speech, preserving overlap. This is an
exploratory conversion, not the official AMI scoring recipe; the resulting WER
must not be directly compared with published AMI numbers or used to sign off §7.

### Initial exploratory result (2026-10-21)

ES2004a: 1,049.35 s of mixed-headset audio, four speakers, 260 transcription
segments. sherpa-onnx 1.13.8 with streaming Zipformer English 2023-06-26,
CPU / two threads on local macOS arm64:

| Metric | Result |
| --- | --- |
| Normalized reference words | 2,809 |
| Word errors | 1,625 |
| Exploratory WER | 57.85% |
| Processing time (model loading excluded) | 32.74 s |
| Processing speed | 32.05× realtime |

Full report: `data/corpus-v1/reports/ami-es2004a-sherpa-001.json` (local,
gitignored). RAM was not collected. The report's model identifier and machine
text were corrected after the run; measured values were unchanged. This high
error rate needs investigation of recognition errors and the overlap/reference
serialization protocol. It is not a release baseline or target sign-off. This initial Sherpa report did
not measure DER or live latency; later MOSS diagnostic DER runs are documented
above. Live latency remains unmeasured.

See [the ASR investigation](ASR_INVESTIGATION.md) for controlled ablations,
streaming adapter fixes, and the proposed accuracy path. Cached Whisper-tiny
scored 34.10% on the same exploratory reference; this remains above the
provisional target and does not sign off any target.
