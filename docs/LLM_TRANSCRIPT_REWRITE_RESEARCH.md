# Optional LLM transcript rewriting

Research and implementation proposal, October 30, 2026. This document does not
implement or enable rewriting. The proposed feature applies to both macOS and
Linux and does not change the Ubuntu installers already built.

## Recommendation

Add **AI transcript cleanup**, disabled by default, with two processing modes:
**Live** and **After recording**. Keep the original ASR transcript permanently
available. Start implementation with after-recording cleanup, then add live
cleanup using the same validated transformation and storage contracts.

Treat cleanup as conservative editing: punctuation, capitalization, obvious
spelling and vocabulary normalization where the text supports the correction.
Preserve meaning, language, code-switching, uncertainty, negation, names,
quantities and speaker attribution. Fluent paraphrasing belongs in a separately
labelled edited document, rather than silently becoming the transcript.

Text-only rewriting cannot verify what the audio actually said. If the source
contains the wrong name, number, or missing speech, the model may only guess.
Keep ambiguous passages unchanged and allow users to consult the recording.

## Evidence and its limits

OpenAI Docs explicitly describes text-model post-processing for transcript
spelling and formatting, including supplied company/product vocabulary. This
establishes a supported integration pattern, not a guarantee that corrections
are accurate for ScribeLoop's meetings or languages.
[Official speech-to-text documentation](https://developers.openai.com/api/docs/guides/speech-to-text).

Deepgram distinguishes changing interim text (`is_final: false`) from finalized
segments (`is_final: true`). A complete utterance can span several finalized
segments before `speech_final: true`. Downstream cleanup should therefore use
persisted finalized segments and preserve every finalized portion.
[Deepgram endpointing and interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results).

LLM response streaming emits output incrementally over SSE. It does not itself
turn a finite text request into an ongoing audio processor. Structured outputs
can be streamed, but schema adherence does not establish factual correctness.
[Official streaming documentation](https://developers.openai.com/api/docs/guides/streaming-responses),
[official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Research on DiarizationLM demonstrates that LLM post-processing can improve
readability and diarization in evaluated settings. A medical-transcription study
also evaluates ASR correction and domain concepts. These findings justify a
ScribeLoop evaluation; they do not establish gains for arbitrary meeting audio,
Vietnamese/English code-switching, or an off-the-shelf user-selected model.
[DiarizationLM](https://arxiv.org/abs/2401.03506),
[The Sound of Healthcare](https://arxiv.org/abs/2402.07658).

## Modes and controls

| Control | Proposed behavior |
| --- | --- |
| AI transcript cleanup: Off | No cleanup model calls, jobs or automatic catch-up. Ordinary ASR and notes continue independently. |
| On, Live | Clean completed groups of speech while recording. Display original speech immediately; apply validated edits afterward. |
| On, After recording | Wait for the selected ASR pass to finish, then clean the saved transcript. |
| Transcript view: Original / AI cleaned | Read saved variants locally. Switching views never triggers a model call. |
| Clean this saved transcript | Explicit one-time job, available even when the automatic setting is Off. |

Persist the default for new meetings. An explicit meeting-level toggle can
change the current meeting without altering its provider or model mid-job.
Changing the provider/model applies to new jobs; existing jobs retain their
configuration snapshot.

Turning Off stops scheduling, cancels queued/in-flight work where possible, and
rejects late responses using a job generation identifier. It does not delete
already saved cleanup or undo an export. Bytes already sent to a provider cannot
be recalled, and cancellation cannot promise that an in-flight request is free.
Turning Live on partway through a meeting starts with new finalized speech;
cleaning earlier speech requires an explicit catch-up or after-recording action.

Show the destination provider/model, local versus cloud processing, and a
separate cleanup spending limit. Explain that cloud cleanup sends transcript
text, vocabulary and limited surrounding context to that provider. Notes and
cleanup are independent permissions and independent jobs.

## Live processing

Proposed starting parameters, to be tuned through measurement:

1. Feed a separate worker only after original segments have been committed.
2. Group roughly 20–30 seconds of finalized speech, favoring sentence/utterance
   boundaries. Add a hard size/time cap so an uninterrupted monologue progresses.
3. Supply limited preceding original text as read-only context. Only the target
   segment IDs may be edited. Keep mic and output speaker/track identities intact.
4. Validate the entire response before committing one atomic group. Token deltas
   may be shown as a transient preview, but never as accepted transcript text.
5. Bound concurrency and the queue. If the model falls behind, leave original
   text visible, mark cleanup as behind/paused, and offer an explicit final pass.
   Never block audio capture, ASR persistence or Stop to wait for cleanup.
6. Flush a short final group at recording end only while cleanup is still enabled.

Initially prefer complete JSON responses per group; live processing does not
require streaming model tokens. Add SSE previews only if usability testing shows
that they help more than the resulting text movement distracts.

## After-recording processing

Wait for ASR completion. If the existing optional Deepgram final transcription
pass is enabled, finish that pass first. Use the resulting ASR revision as the
cleanup source; do not clean one revision and present it as another.

Split long meetings by segment/sentence boundaries into bounded requests, perhaps
2–5 minutes of speech per target group, subject to a stricter token/byte cap.
Include adjacent original text as read-only context and edit each target once.
Stage partial results in encrypted storage; publish the complete cleaned variant
atomically. On failure, preserve the original and identify the incomplete job.
Retry/resume is an explicit user action with a visible spending limit.

“After recording” should initially use ordinary interactive API requests. It is
not synonymous with a provider's asynchronous bulk Batch API, which has a
separate execution window and is intended for non-immediate workloads.
[Official Batch API documentation](https://developers.openai.com/api/docs/guides/batch).

## Data integrity and validation

Store cleanup as a derived variant rather than overwriting source ASR rows.
Record the base ASR revision, source segment IDs and text hashes, endpoint/model,
prompt version, settings, job state and usage. Keep this data in the encrypted
workspace. Never write transcript content or credentials to diagnostic logs.

Require structured text patches keyed by allowed source segment IDs. Reject
unknown/duplicate IDs, edits outside the target, malformed or truncated output,
oversized responses and destructive deletion of nonempty speech. Permit an
empty patch list to mean “leave everything unchanged.” The model must not change
speaker IDs, timestamps or source confidence scores. Preserve manual edits and
reject responses whose source hashes no longer match.

Use deterministic checks to flag or reject changes to numbers, negation,
names, dates and amounts. These checks reduce risk; they cannot prove semantic
fidelity. Do not present the model's own confidence as an accuracy guarantee.
Transcripts are untrusted data: no tools, URL retrieval, or execution of spoken
instructions. A constrained editing prompt is separate from summary instructions.

Preserve segment-level audio navigation. Rewritten wording has no new measured
word timestamps, so do not reuse old word highlighting as if the new words were
aligned to audio. Batch publication and live-group commits must be transactional.

By default, summaries and citations continue to use original ASR text. A future
explicit “Use AI-cleaned text for notes” setting must bind notes to the exact
variant/version and show the underlying source. Changing that variant must mark
its dependent notes stale. Exports label cleaned text and its provenance.

## Fit with the existing code

- `pipeline/transcription_quality.py` and `deepgram_meeting.py` already implement
  `final_transcription`: a second ASR pass over audio, not LLM cleanup. Keep the
  controls separate: **Refine recognition from audio** and **AI transcript cleanup**.
- `api/desktop_settings.py` can persist cleanup defaults, but provider saves are
  intentionally blocked while processing. A current-meeting on/off control needs
  a dedicated authenticated command, rather than relaxing that provider guard.
- `llm/summary_provider.py` supplies endpoint validation and credential patterns.
  Extract shared transport or add a dedicated cleanup provider; its summary
  schema, prompt, output limits and request accounting are not a cleanup API.
- `storage/sessions.py` currently selects the highest transcript revision.
  Adding a cleanup revision to that path would silently select generated text.
  Use separate variant/job storage and explicit selection semantics.
- `llm/grounded.py` and `llm/rolling.py` bind notes to revision/input hashes.
  Extend provenance deliberately if cleaned text later becomes a notes input.
- Add controls/status to SwiftUI `Preferences.swift` and the shared notebook UI,
  plus bounded authenticated events for live cleanup progress and saved variants.

Proposed settings: `rewrite_enabled=false`, `rewrite_mode=live|after_recording`,
`rewrite_use_notes_provider=true`, optional separate provider configuration, and
an explicit request/token/spending cap. Sharing credentials does not mean sharing
prompts or spending ledgers. Default mode when enabled: After recording.

## Cost and evaluation

Live grouping every 20–30 seconds implies roughly 120–180 requests per hour of
continuous speech; 2–5-minute batch groups imply roughly 12–30, before size limits,
silence or retries. These are scheduling estimates, not token costs or latency
guarantees. Repeated context adds input tokens. Meter input/output tokens when
reported, and show estimates rather than pretending an unpriced custom model is
free. Local endpoints consume the user's hardware resources.

Before choosing a default model, compare the existing notes model, a faster
user-configured model, and a local compatible model on consented test recordings.
Measure original versus cleaned WER/CER, entity/number/negation preservation,
omission/addition rates, bilingual/code-switch fidelity, latency, edit churn,
requests and cost. Readability improvement alone is not enough for promotion.

Acceptance includes Off producing zero cleanup calls; correct live-only and
batch-only triggers; mid-request disabling; stale/reordered/duplicate responses;
ASR reruns and manual edits; unsupported structured output; provider refusal,
timeout and budget exhaustion; crash/restart; plaintext-free logs; and summaries,
exports and playback retaining correct provenance. No paid provider tests or
accuracy benchmark were run during this research.
