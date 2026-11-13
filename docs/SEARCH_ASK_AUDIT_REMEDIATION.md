# Search/Ask audit remediation — 12 November 2026

Branch: `fix/search-ask-quality-and-privacy`.

## Delivered changes

- Semantic chunks now retain one source's exact character offsets. Adjacent
  boilerplate no longer inherits a relevant passage's rank. Common query words
  are removed consistently and semantic rank has greater weight in fusion.
- Date/person eligibility is applied before nearest-neighbor selection. Explicit
  meeting scope also limits lexical reconciliation work.
- Ask sends matched excerpts, not the beginning of a large parent segment. Full
  source fingerprints remain separate from clipped evidence and survive history
  reloads and follow-ups. History filters by scope before applying its limit.
- Provider request limits use the actual UTF-8 JSON body, including schema and
  envelope. Ask adjusts its bounded packet before sending an oversized request.
- Search and Ask navigate first to the containing lazy transcript turn, then the
  cited segment, preserving the segment highlight across sheet dismissal.
- Request-scoped authorization captures a revocation epoch. Every embedding batch,
  query embedding, answer upload, persistence and result return rechecks it.
  Revocation cancels all registered Ask jobs. Reaccepting terms cannot resurrect
  an old job. Ollama's metadata-to-generation boundary is guarded too.
- Metrics include total processing, retrieval/indexing/embedding/answer time,
  embedding and answer call counts, and processed input bytes. Saved answers
  retain those metrics. Earlier answers explicitly report unavailable retrieval
  metrics instead of implying their old generation timer was total latency.
- Summary index text excludes internal segment IDs. Nonempty answer caveats are
  displayed even when the answer otherwise contains supported claims.

Schema v21 adds source-span, excerpt-offset and usage columns. The derived search
index version advances to 4 so old chunk layouts are rebuilt lazily. Vectors keep
their provider/model/dimension generation identity; no mixed-shape comparisons.
Completed meetings retain atomic indexing. A submitted job is bounded at 128
embedding batches; provider failure or a reached budget leaves saved prior
indexing intact and discloses keyword fallback. No automatic paid retry occurs.

## Verification

### Result quality

`python scripts/check_search_quality.py --output-dir data/validation/search-ask-audit-fix`
uses a versioned synthetic 30-meeting, 80-segment corpus with 40 bilingual business
questions. It runs the actual bundled local Granite ONNX adapter without cloud
calls or private meetings. The script requires top-five support recall ≥90% and
top-20 recall ≥95% for both Search and Ask.

| Exact support-passage retrieval | Audit baseline | Remediation |
| --- | ---: | ---: |
| Search top 1 | 21/40 | 33/40 |
| Search top 5 | 24/40 | 38/40 |
| Search top 20 | 32/40 | 39/40 |
| Ask top 5 | 28/40 | 38/40 |
| Ask top 20 | 38/40 | 39/40 |

Observed local first-query time: 216 ms including indexing, excluding model
construction. Warm query median: 3.88 ms. These are small synthetic in-memory
development measurements, not encrypted production-scale or Gemini benchmarks.
One support-passage miss remains; these improvements do not imply perfect recall.

### Rebuilt macOS app and real Gemini

The user's existing configured Gemini providers and the same previously
authorized app/AI conversation were used. No provider/key settings changed.

- `architecture plugins`: relevant 3:12 transcript moved from rank 13 to rank 1.
  The summary was second, with internal source IDs removed from its snippet.
  Cold indexing/search showed three model calls and 3,951 ms total.
- Selecting that Search hit opened the actual 3:12 passage.
- `Kiến trúc hiện tại là gì ?`: returned a cited architecture/Agent/plugin answer.
  Total 1,863 ms, retrieval 432 ms (including indexing 1 ms), answer 1,424 ms;
  one query embedding and one answer call, approximately 15 KB input processed.
- Reopening Ask showed the new answer as current. Opening saved history restored
  its answer, evidence and total usage. Its transcript citation opened and
  visibly highlighted the 3:12 passage; its summary citation opened Notes.

Native compilation, bundle build and packaged signature checks pass. The app is
left open with the cited passage visible. Rebuilding resets capture permissions
through the existing build script; no recording was started for these checks.

### Security and regression evidence

Outcome: **fixed** for the pending Search/Ask consent-revocation path.

The protected invariant is that revoking agreement prevents a pending operation
from starting a later provider request or publishing/saving a new result. The
initial authenticated/accepted workflow, direct library callers without an
explicit guard, configured providers, scope checks and ordinary provider-error
handling remain supported. Data transmitted before revocation cannot be recalled.

The fix is shared by desktop Search/evidence/history and background Ask, rather
than patching only the original same-socket example. An independent read-only
boundary investigation preceded implementation. One independent candidate review
found a remaining history-return window; it was corrected and regression-tested.

`tests/test_search_ask_audit.py` covers 19 cases, including cold multi-batch
indexing, cached query embedding, answer generation, history reconciliation,
revoke/reaccept, same- and cross-socket revocation, cancellation-resistant
adapters, Ollama metadata, valid uninterrupted requests, byte budgets, accounting,
filtered retrieval and source freshness. No real cloud data was used in security
trigger tests. Every interrupted stage starts no later request and saves no new
answer; a fresh authorized operation still succeeds.

`tests/test_encrypted_storage.py` additionally checks real SQLCipher person/date
filtering, a tail beyond 48 KiB, answer history after close/reopen, and absence of
plaintext source text in the database file.

Commands/results: full `pytest -q` **882 passed** (39.02 s); focused audit/encrypted
checks **20 passed**; final Search/Ask accounting/retrieval checks **35 passed**;
`ruff check core scripts tests` and `git diff --check` pass; complete nine-source
Swift compilation, macOS desktop bundle build/signature and real UI/provider checks
pass. The final per-request adjustment prevents metrics accumulating when a direct
library caller reuses a Search/Ask service; the authenticated UI already supplies
one work context per request. Its targeted regression passes.

## Remaining acceptance limits

This closes the listed implementation defects, not the independent release gate.
The 40-query sample is developer-authored and uses Local embeddings. The live
Gemini check covers one meeting and query family. Broader multi-provider relevance,
claim entailment/abstention/conflict review, 1,000-meeting/100,000-segment encrypted
performance, and native source navigation beyond the user's 27-segment meeting
remain acceptance work. A valid citation ID alone is not proof of factual support.
