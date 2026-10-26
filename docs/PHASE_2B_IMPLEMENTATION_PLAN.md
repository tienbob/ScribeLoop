# Phase 2b — current plan and implementation status

Updated 2026-10-26. Branch: `feature/phase-2b-long-meetings`.
This plan was updated before each implementation slice. Earlier local-model and
per-action consent proposals are superseded by the decisions below. See the
[delivery and acceptance boundaries](PHASE_2B_CLOSEOUT.md) for implementation details.
Search/ask-past-meetings remains a separate later increment.

## Preserved decisions

Cloud-only Deepgram streaming ASR; one provider, no automatic fallback/retry or full
ASR pass after Stop. Encrypted capture/audio/metadata, visible source/provider and
independent Stop remain mandatory. Accept a versioned first-use agreement once;
each detected call still offers Record / Not now. Summary API defaults to OpenAI
and supports explicit compatible overrides. No paid call or private recording is
authorized by offline validation. Renaming/linking never implies voice learning.

## Implementation sequence

1. [x] **Cloud-only app route and agreement.** Remove local ASR choices and default
   fixture inference. Persist agreement/version/revocation. Gate capture/uploads
   server-side; keep per-call recording choice and native permissions separate.
2. [x] **Audio lifecycle.** Keep encrypted audio for 30 days, archive exemption,
   audio/key-only expiry, retryable key-first cleanup and preserved transcripts/
   notes. Add bounded local playback and explicit cloud-flow rerun. Keep confirmed
   whole-meeting deletion separate. Historical experiments/weights remain untouched.
3. [x] **Long-meeting software path.** Incremental authenticated cursor, bounded
   timeline windows and streaming recovery; explicit five-minute/one-hour/three-hour
   duration presets and shared audio budgets. Enforce storage/checkpoint bounds,
   disk reserves, reader ownership, cancellation and paged UI rendering. Generated
   three-hour native protocol/resampler and late-resume checks pass. Physical
   multi-hour capture acceptance is still an external release gate.
4. [x] **Durable rolling/hierarchical notes.** Bounded confirmed sections, durable
   identities/fingerprints/provider/model/endpoint/request states, cited reduction
   and restart-safe cache. Multiple text requests are disclosed in agreement v3;
   128-request/8 MB per-meeting and per-request input/output limits apply. No automatic
   retry after ambiguous dispatch. Explicit Generate summary can retry failed work;
   successful sections are reused. Edits invalidate affected fingerprints.
5. [x] **Named people and privacy controls.** Reusable profiles, revision-scoped
   links, separate learning preference, reviewed source/model/version exemplar
   contracts and bounded cloud extractor adapter. Forget removes links/history,
   exemplars, derived notes/cache/vectors and stale names, including previously
   unlinked meetings. Voice matching returns suggestions only and is calibration-gated.
6. [x] **OpenAI summary default.** `gpt-5.4-mini` through Responses at the official
   endpoint with `OPENAI_API_KEY`, bounded output and `store:false`. Explicit
   SUMMARY_* overrides retain compatible Chat Completions. OpenAI keys are never
   implicitly forwarded to custom endpoints. The supplied local key is configured;
   validation used mocked transport and consumed no provider quota.
7. [x] **Local acceptance.** Full suite: 402 Python tests; 14 frontend groups;
   Ruff/whitespace, 67-module import smoke, wheel-content audit and generated native
   checks pass. Available Linux/macOS CI must pass on the PR; Windows remains deferred.

## Open acceptance/integration gates — do not mark complete

- [ ] **Real cloud voice-embedding backend integration and calibrated recognition.**
  No such service is configured. The app collects no voice-learning data in this
  build. Calibration tooling checks independent held-out people, per-condition
  false matches/misses, uncertainty and evidence hash; it is not real calibration
  evidence. Do not invent thresholds or enable automatic naming. Named links work
  manually now; learning preference does not make recognition operational.
- [ ] **Consented real-call, physical audio/timing and multi-hour host validation.**
  Generated native signals are not physical alignment or app/device coverage.
- [ ] **Real LLM quality/latency/cost acceptance, signed/notarized desktop packaging
  and OS parity.** User-deferred signing and real-meeting tests remain deferred.

The requested long-meeting, notes and profile software is implemented. All of Phase
2b's original recognition/real-world acceptance goals cannot honestly be declared
complete until the above provider/data/host gates are satisfied.
