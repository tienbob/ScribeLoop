# Local transcript cleanup — 5 November 2026

## Context-aware correction plan

Approved follow-up: replace spelling-only guidance with contextual ASR repair.
Send a bounded, evenly sampled overview of the whole call, actual neighboring
segments, the meeting title and saved vocabulary as untrusted context. Fix the
preceding-context indexing bug. Allow Vietnamese word replacements when the model
cites supplied context IDs; retain number/negation/large-rewrite checks. Require
the model to flag unresolved ambiguity for review and retain the original in normal
mode. Skip mode remains unfiltered. Preserve ASR revisions and exercise synthetic
English/Vietnamese business corrections, context boundaries and uncertain edits.

## Notes-provider independence follow-up

Cleanup currently inherits the active notes credential/model only when the selected
providers match. Switching notes hides that inherited key and can disable cleanup,
even though the provider's credentials remain saved. Resolve cleanup against that
provider's saved notes key/model independently of the active notes choice. Prefer
explicit cleanup credentials, and share a notes credential only at the matching
provider endpoint; never send it to a different custom URL. Mirror these rules in
the key preview and missing-model hint. Test switch-away/reopen, explicit-key
precedence and endpoint mismatch without making provider calls.
For HTTP 503, show a temporary provider-unavailability message while retaining the
ASR transcript; do not describe it as a credential or validation failure and do not
automatically repeat billable requests.

## Experience skip-validation correction

The Experience flag reaches both cleanup engines, but the unchecked response path
still calls the strict Pydantic response validator. Remove that validator from the
unchecked path: decode JSON and map string text to known segment IDs, accepting
extra fields, reordered/dropped segments and changed content. Preserve originals
for omitted IDs. Keep only the response decoding/mapping required to save a usable
revision; never assign unknown IDs to another segment. Distinguish model/response/
storage failures from content-validation failures in warnings. Add regressions for
schema deviations in skip mode, strict-mode rejection and the full save flow.
The native toggle is also inverted: “Skip cleanup validation” is bound directly
to the positive cleanupValidate setting. Bind its display value to the inverse so
turning Skip on actually saves local_cleanup_validate=false.

Desktop 0.1.38 (39) adds a local-only copy-editing pass after local ASR and before
automatic notes. The setting is **Settings → Models → Clean up transcript
locally**, enabled by default. It requires the notes provider to be on-device
Ollama. Deepgram and cloud notes are never used for this stage.

The pass uses native Ollama structured output with explicit context/output
limits and thinking disabled. Batches contain at most four segments / 8 KB of
text, with up to 64 requests per cleanup. The output schema constrains IDs and
segment counts. Independent validation preserves order, digit sequences/signs
and English/Vietnamese negations, and rejects large textual changes. These
checks do not prove that every small wording change is semantically correct.

Every batch must validate before any changed text is committed. A new revision
preserves timestamps, stream, speaker identities, manual speaker labels and
person associations. Original ASR segments remain stored. A database savepoint
makes the edit revision, copied associations and provenance event atomic.
No-op cleanup creates no revision, but records that zero segments changed.
Failed/invalid cleanup keeps all raw text and continues to notes with a warning.

The UI shows “Cleaning up transcript locally…” while this stage runs. Disabling
the setting returns the workflow to direct ASR → notes. It applies after new
recordings and Transcribe again; Generate summary alone does not rewrite a saved
transcript. The current transcript UI shows the latest revision, while older
revisions remain in encrypted storage.

## Verification

- Full suite: **744 passed**. Lint and whitespace checks passed.
- Tests cover cloud rejection before dispatch, cancellation before dispatch,
  invalid IDs/order, numeric/sign/negation changes, excessive rewrites, failure
  in a later batch without partial edits, timestamp/speaker-label preservation,
  rollback after an injected database failure, and cleanup preceding notes.
- Real local Gemma E2B corrected synthetic “tomorow” → “tomorrow”, “we has” →
  “we have”, and Vietnamese “ứng dung” → “ứng dụng”, preserving the number 10.
- On the prior captured eight-segment Vietnamese call, the standalone live
  cleanup test returned unchanged text for all segments. Ambiguous ASR wording
  was not reconstructed; this is not an accuracy benchmark or audio-aware pass.
- Canonical app rebuilt in place and signature/bundle checks passed. Requested
  ScreenCapture and Calendar permission resets succeeded. All nine conversations
  loaded. The installed Models screen displayed the cleanup toggle enabled,
  Qwen Q4_K_M ASR, and local Gemma notes.
- Installed real-call Transcribe locally progressed to “Cleaning up transcript
  locally…” while keeping the saved transcript visible, then Completed and saved
  Vietnamese **Ollama · Revision 4** notes without an error. Read-only audit
  metadata confirmed source revision 4, result revision 4, Gemma E2B, zero edited
  segments: this real-call pass was a successful no-op, not an accuracy gain.

Speakers stayed off. No fresh capture, cloud inference, email send or Drive upload
was performed during this feature's tests.

Correction implemented in **0.1.39 (40)**: Skip on maps to
`local_cleanup_validate=false`. Unchecked cleanup no longer uses the strict
Pydantic edit schema; extra response fields, reordered/dropped rows and content
rewrites are accepted, with original text retained for omitted IDs. Readable JSON
and known segment IDs remain necessary to map output into stored segments.
Warnings distinguish request/response failures from save failures instead of
calling every error a validation rejection. All **768 tests** pass outside the
sandbox (localhost callback and native AAC tests need OS access); the focused
cleanup suite has 31 passing tests. Ruff, whitespace and bundle/signature audits
pass. Staging installation preserves Screen Recording/Calendar permissions.
No existing meeting was retranscribed or uploaded for this fix.

Installed UI verification: opened Settings → Experience in the previous build and
confirmed Skip appeared on while its saved positive validation flag was enabled.
In 0.1.39, enabled Skip, saved successfully, reopened Settings → Experience and
confirmed it remains on. This saves `local_cleanup_validate=false` with the corrected
inverse binding. All eight existing conversations remained available; none was
reprocessed. The user's skip preference is now enabled for subsequent cleanup runs.

Provider-independence correction shipped in **0.1.40 (41)**. Cleanup now resolves
its own provider's saved notes key/model even after notes switches elsewhere;
explicit cleanup keys retain priority. Legacy active notes models are migrated to
the provider-model map on load. A notes credential is shared only when the cleanup
URL matches that credential's provider endpoint. The native key preview follows
this destination-aware lookup. Tests cover switch-away/reopen, key precedence,
legacy model migration and endpoint mismatch.

The observed Settings combination was Deepgram transcription, Gemini cleanup and
Ollama notes. HTTP 503 means a temporary provider-side availability/capacity error,
not a deleted credential (Google's reference:
https://ai.google.dev/gemini-api/docs/generate-content/api-errors). Cleanup now emits
an explicit 503-unavailable warning, retains ASR text and lets notes continue, with
no automatic API retry. No new cloud calls/private uploads were used to test this;
503 behavior is verified with a simulated provider response.

Final suite: **771 passed**, focused provider/cleanup suite **62 passed**, Ruff/diff
and bundle/signature audits pass. Installed canonical 0.1.40 after the user's run
finished, preserving capture permissions and saved provider selections.

Post-install UI verification: all eight records loaded; Models shows Ollama notes
and Gemini cleanup together, with the cleanup saved-key indicator and existing
Gemini model intact. No provider/key field was edited during verification.

Context-aware cleanup implemented in **0.1.41 (42)**. Each batch receives up to eight
source excerpts spread across the call (bounded context, not a speculative summary),
the actual preceding/following dialogue, meeting title and saved vocabulary. Prompt
context is treated as untrusted data. Vietnamese word substitutions can pass normal
validation when the model cites supplied context IDs; digit sequences, negations,
large length/similarity changes and transcript identities remain protected. This
checks structure/edit limits, not proof of semantic correctness or audio accuracy.

The model can mark `needs_review`; normal mode retains the original and stores that
review flag in cleanup provenance, including no-op revisions. Native transcript
passages show review markers and expandable **Original ASR** text for changed rows.
Original text is bounded to a clearly labelled excerpt for long passages; full prior
revisions remain encrypted. The experimental skip flag still bypasses content checks.

Verification: **776 tests passed**, including context indexing/whole-call coverage,
English/Vietnamese edits, protected numbers/negations, unknown context rejection,
uncertainty preservation, durable review flags and original-text retrieval. Ruff,
whitespace, native build and bundle/signature audits pass. Staging install preserved
Screen Recording and Calendar permissions.

One real Gemini `gemini-3.5-flash-lite` request used four fictional Redis discussion
sentences, validation ON. In **1.51 seconds**, it changed “invalidate the cash” to
“invalidate the cache” and “xóa tiền mặt Redis” to “xóa cache Redis”, leaving the two
context sentences unchanged; no edits were rejected. No user meeting was uploaded
or reprocessed, no fallback/retry was made, and saved provider/skip settings were
preserved. Results: `data/phase1/context-cleanup-20261105/results.json`. This tiny
synthetic check demonstrates the behavior, not representative call accuracy.

Post-install UI reopened with all eight records and detection ready. A deeper
transcript-disclosure interaction timed out in desktop automation; disclosure/review
rendering compiled successfully and source retrieval/persistence are covered by the
regression tests, but no successful physical disclosure expansion is claimed here.
Initial automatic approval reviews timed out before execution; the permitted single
retries succeeded, including the one synthetic API request above.

## Cleanup provider resilience and durable failure trace — 5 November evening

A real reprocess run (Deepgram multichannel, revision 24 of the Vietnamese
capability meeting) saved raw ASR text and the run log showed only a notes
failure. The stored event history and a reproduction establish two independent
provider-side causes, not a cleanup logic error:

- The Gemini OpenAI-compatible cleanup endpoint intermittently returned HTTP 503
  (reproduced twice with the four fictional sentences: a 503, then an identical
  request succeeding seconds apart). The same flapping hit Gemini notes at
  14:47–15:10. Cleanup succeeded five times earlier that afternoon, so settings,
  credentials and the request path were correct.
- Local Ollama was not running at 17:33, so Ollama notes failed immediately with
  `summary_failed` (connection refused) — the only failure the run log persisted.

Two code weaknesses turned that into a silent no-op, both fixed here:

1. **One failed request aborted the whole cleanup pass.** Parts are requested
   independently, so a failed request now keeps only its own part on the original
   wording while the remaining parts are still cleaned and saved atomically. No
   automatic retry was added: the no-repeat-billable-requests rule stands, and a
   failed part is never re-requested in the same pass. Cancellation still aborts
   the pass, and a pass where every part failed keeps the established whole-pass
   failure semantics (503-unavailable vs request/decode/validation messaging).
2. **Cleanup failures left no durable trace** (a transient UI warning only).
   Both partial and total failures now record a sanitized
   `local_transcript_cleanup_failed` event — error names, HTTP status codes, part
   counts and affected revision; never transcript text, provider bodies or keys.

The partial-failure warning names the failed parts and states that their segments
kept the original wording. `cleanup_originals` and the `local_transcript_cleanup`
provenance event are unchanged; original ASR revisions remain stored.

## Validation hardening audit — 5 November late evening

Request bounds now live in one place (`_MAX_PART_SEGMENTS`, `_MAX_PART_BYTES`,
`_MAX_PARTS`, `_PART_TIMEOUT_S`) shared by the schema, grouping and timeout, so
they cannot drift. The system prompt is a module constant aligned with the
validator: the row being edited is never acceptable evidence, sibling rows are,
and a row marked `needs_review=true` is told its text will be discarded.
Validator changes, each covered by a regression test:

- Self-citations are stripped before deciding whether a repair may use the
  contextual checks; a segment citing only itself stays on the strict path.
- One added or dropped word no longer fails the length ratio by itself, so
  split and merged words ("ngânhàng" → "ngân hàng") survive; two or more words
  still require the ratio band.
- Vietnamese comparisons normalize to NFC first, so decomposed ASR output
  ("chu" + combining horn + "a") compares equal to its precomposed spelling
  instead of splitting into different words at the combining mark.
- Whitespace-only repairs are rejected (no numbers, no words).
- Skip-validation mode strips one fenced ```json wrapper, tolerates metadata,
  reordered or missing rows, and now fails a part whose response maps no text
  to any known segment ID instead of silently keeping every original.
- Decomposed and precomposed negation spellings count as the same negation.
