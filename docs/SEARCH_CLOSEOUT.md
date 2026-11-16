# F1 Search — implementation closeout

Updated 2026-11-16. **Closed by explicit user approval.**
Delivery requirements: [meeting workspace plan](MEETING_WORKSPACE_DELIVERY_PLAN.md).

The user approved closing F1/F2 after reviewing the recorded results and unrun
gates. Remaining validation below is accepted follow-up work, not a blocker to
F3. Unrun checks are not marked as passed.

## Delivered

Search indexes current titles, transcript revisions and fresh grounded summaries
inside SQLCipher. FTS5 capability checks disclose a basic fallback. Per-meeting
atomic reconciliation, stable source IDs and source fingerprints cover changes,
deletion and person changes. Hybrid retrieval uses provider/model/shape-specific
vector generations and bounded excerpt offsets; supported local and cloud
embedding choices do not share incompatible vectors.

The native sheet debounces query/filter edits for 500 ms, binds results to the
latest request, opens cited transcript/summary sources, and includes meeting/date/
person filters. The responsiveness fix adds cancellable jobs and progress without
blocking recording/revocation controls. Query-size validation stays local and
displays the 200-character limit. Filtered searches index eligible meetings only;
provider responses are streamed with size and elapsed-time limits. Keyword
fallback remains explicit; no automatic provider change or paid retry is added.

These fixes merged in [PR #27](https://github.com/tienbob/ScribeLoop/pull/27),
main commit `3bd4fba`, after core CI, macOS and all four Ubuntu packaging jobs passed.

## Measured evidence

| Check | Recorded result | Limit |
| --- | --- | --- |
| Real SQLCipher scale | 1,000 meetings; 100,000 segments; 101,000 documents | Synthetic development-machine data |
| Warm keyword queries | 100 queries; p50 223.467 ms; p95 233.168 ms; max 237.161 ms | Core call, not native transport; no cloud work |
| Cold keyword indexing/query | 4,173.521 ms | Cold work reported separately |
| Local passage recall | Search/Ask top-five 38/40; top-twenty 39/40 | Developer-authored v1 sample; Local Granite, not Gemini |
| Native visual sample | Automatic results, navigation near cited timestamp, empty state, recovery, Clear | Sampled preview/production flows, not complete acceptance matrix |
| Lifecycle/retention/scope | Existing tests plus new cancellation/filter/response-bound regressions pass | Tests are not independent ranking or human review |

Machine: arm64 macOS 27.2.1, Python 3.12.16; encrypted benchmark uses real
SQLCipher, temporary generated text and an ephemeral random key. Measurement
artifacts: [encrypted scale](acceptance/2026-11-16/encrypted-scale.json),
[local retrieval](acceptance/2026-11-16/local-retrieval-summary.json).

Reproduce without user data or cloud calls:

```sh
PYTHONPATH=core .venv/bin/python scripts/benchmark_meeting_search.py \
  --output data/validation/f1-f2/encrypted-scale.json
PYTHONPATH=core .venv/bin/python scripts/check_search_quality.py \
  --output-dir data/validation/f1-f2/local-retrieval
```

## Accepted limitations and validation follow-ups

- Independently review the labels and confirm ≥36/40 relevant top-five results on
  the agreed corpus/configuration. Existing 38/40 development recall is promising
  but is not independently reviewed release evidence.
- Finish native keyboard, all filters, accent behavior, grouped/page-boundary
  sources, playback at citation time, expired audio and unavailable-index checks.
- Repeat recovery/status timing with a slow/offline/quota provider and complete
  on-screen progress/cancellation checks. The latest character-limit layout was
  blocked by desktop-control window access; native state tests passed.
- The shared production-model evaluation is blocked by the recorded HTTP 429 and
  still requires independent labels and human grounding review. See
  [F2 closeout](MEETING_CHAT_CLOSEOUT.md).

The measured ≤500 ms warm keyword gate passes. Unrun gates remain open; no ASR
rerun, audio upload, private-meeting upload or release-signing claim is involved.
