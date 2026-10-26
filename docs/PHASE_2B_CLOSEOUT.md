# Phase 2b — implementation and acceptance boundaries

Updated 2026-10-26. Development branch: `feature/phase-2b-long-meetings`.

## Implemented

- Cloud-only live ASR, with no local-model app route, automatic provider fallback,
  paid retry or full ASR pass after Stop. Explicit retained-audio rerun remains.
- First-use agreement v3, restored across restarts, with Record / Not now per call.
  Updated terms cover long-session audio caps, section summaries and learning prefs.
- Encrypted audio expires after 30 days while idle/running/accepted; Archive exempts
  it. Expiry preserves text/notes. Whole-meeting deletion remains separate.
- Incremental authenticated SLPA cursor, bounded append reads and linear prefix
  scans/recovery. Cloud streaming retains a small batch of frames, not the entire
  meeting. Headers/replacement/truncation/footer are checked; bytes are authenticated
  when consumed. Detecting changes to already consumed bytes requires a new scan;
  saved reprocessing authenticates both full tracks before uploading either.
- Five-minute development, one-hour and three-hour recording presets. Estimated
  shared audio caps are $0.25/$3/$8 in production, covering both sources and saved
  actions. An explicitly injected configuration may choose a lower cap. Disk space
  is checked before capture and while writing; overflow, disk shortage or selected
  timeline violations stop capture visibly with recoverable encrypted prefixes.
- Bounded selectable playback windows, at most 305 seconds, including later parts
  of long recordings. No plaintext temporary audio or playback provider request.
  The full selected track authenticates before any window leaves the backend.
- Durable section notes (15-minute timeline buckets, also split by input size),
  then hierarchical cited reduction. Only closed confirmed sections are summarized
  live; final notes reuse them. Provider/model/endpoint, revision, fingerprint,
  request state and counters persist. Dispatched/failed jobs require an explicit
  retry; startup does not resume uploads. Editing text/names invalidates cache keys.
  Limits: 128 requests, 8 MB total request text and 4,000 output tokens per request
  across a saved meeting's actions; no more than 100 KB per individual request.
- Reusable named people, revision-scoped manual links, separate learning preference,
  source/model/version exemplar provenance, and a cloud extractor adapter with no
  local fallback. Enrollment requires an explicitly named/linked opted-in person,
  retained authenticated audio, manual purity review and at least 30 seconds of
  covered speech without another same-track speaker. Naming never enables learning.
- Forget person removes links and link history, exemplars/provenance, affected
  generated notes/section caches and text vectors, rebuilds anonymous excerpts and
  flags redaction review. Previously unlinked meetings remain covered by provenance.
  Original speech/audio and external exports/backups are preserved, as disclosed.
- Calibration tooling checks disjoint held-out people, condition coverage, false
  matches/misses and a 95% upper false-match bound. Sparse/unreliable evidence cannot
  enable suggestions. Suggestions are never automatic name assignments.
- OpenAI `gpt-5.4-mini` is the default summary model via Responses, with
  `OPENAI_API_KEY`. Custom `SUMMARY_*` providers/models/keys still override it.
  An OpenAI key is never implicitly sent to a custom endpoint. Redirects, ambient
  proxies and automatic request retries remain disabled. `store:false` is sent.
- UI duration/budget controls, live section notes, restored drafts, person controls,
  later playback windows and 500-entry transcript pages with full Markdown export.

## Evidence

Generated native transport/resampling ran an accelerated three-hour timeline on
both tracks: 172,800,000 samples each, 150 ms intentional starting offset and
0.4375 ms offset change at the end. No devices opened or audio files were saved.
See [aggregate evidence](evidence/phase2b-native-3h.json). This verifies the generated
protocol/resampler timeline, not physical microphone/output alignment or real calls.

Offline tests cover bounded long-prefix memory, append/replacement/tamper/footer,
late three-hour resume with one cloud connection per source, recovery, cache/restart,
explicit retry, citations, enrollment prerequisites, vector/name purge and rollback.
OpenAI transport is mocked; no provider quota was consumed. The locally supplied
key was checked for configuration presence only. Python/frontend, lint, import,
wheel-content and generated native checks are required; Linux/macOS CI is tracked
on the PR. Windows remains deferred by prior instruction.

## Gates that remain explicit

**Automatic voice recognition is not enabled.** The shipped cloud ASR returns
session-scoped anonymous labels, not reusable voice embeddings. No real cloud
speaker-embedding service is configured. The adapter, opt-in/provenance, evidence
calibration and suggestion contracts are implemented, but cannot substitute for
provider integration and consented repeated-person calibration. The app says that
learning is a stored preference and collects no voice data for it in this build.
Do not claim this recognition gate is complete or enable a guessed threshold.

Real multi-hour Teams/Slack/browser capture, physical audio alignment, real summary
quality/cost/latency, recognition false-match validation, signed/notarized desktop
packaging and OS parity retain the earlier release gates. User-deferred real-call
and signing validation has not been silently replaced with generated evidence.
