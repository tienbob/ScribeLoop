# Phase 2a — finalization development closeout

Updated 2026-10-26. The agreed six-item finalization scope is implemented and
locally verified. Required Linux/macOS CI must pass on the review PR before exit.
This is development completion, not live quality, release or full Phase 2b–2d sign-off.

## Delivered scope

| Item | Result | Acceptance evidence |
| --- | --- | --- |
| 1. Revisions | Registry, provider/model provenance, selected-revision metadata, historical reads, atomic replacements and correct Resume appends | Upgrade/retry and rollback tests preserve original segment/speaker IDs and previous results |
| 2. Speaker corrections | Per-revision names and same-track/generation merges; original model IDs intact | Merge boundaries, chained edits, note-write rollback, reconnect and deletion tests |
| 3. Grounded summaries | Configurable OpenAI-compatible text endpoint; strict cited draft sections, bounded requests, no retries/fallback | Prompt/data separation, citation membership, invalid JSON/schema, response cap, consent, freshness and error-redaction tests |
| 4. Markdown export | Corrected transcript and matching summary, UTC meeting dates, relative timestamps, source attribution and citation anchors | UTF-8/injection-shaped text, corrected names, stable anchors, stale fingerprint, unique filenames and rejected arbitrary path/stale/deleted selection tests |
| 5. UI/recovery | Current revision, live/processing/error state, corrected roster, cited notes, explicit plaintext download, reconnect restoration | Eleven frontend regression groups and API lifecycle tests; desktop preview checks described in the implementation plan |
| 6. Acceptance | Offline regression suite and package verification; required CI matrix is Linux/macOS | 369 Python tests passed locally; Ruff, 64-module lightweight import smoke, frontend checks and wheel inspection passed |

The default UX watches app metadata, asks one per-call permission, creates a dated
record, ends on sustained app inactivity, then attempts one approved summary and
content-based title. Detailed setup/manual controls remain under preferences.
Synthetic end-to-end tests verify this with no capture before approval and continued
completion after browser disconnect. This does not prove real Teams/Slack call states.

## Using the completed increment

Use the Phase 1 encrypted launch command; configure the summary endpoint/model/key
in ignored `.env` as described in [summary setup](MEETING_SUMMARIES.md). One selected
ASR path remains active. New-call permission discloses both audio processing and
the configured summary destination. Saved manual summary requests retain separate
text consent. Export never invokes a provider, and produces plaintext outside app
encryption; browser save settings apply. App deletion cannot erase exported copies.

## Explicitly open

- Real provider/model compatibility, summary factual quality and representative
  meeting accuracy/latency/speaker tests. Citation IDs validate membership only.
- Physical capture and app-specific meeting detection/end acceptance. Current end
  inference needs 60 seconds of fresh complete inactivity evidence; unknown states
  keep recording. Browser scope includes all tabs and the test cap is five minutes.
- Narrow-layout visual QA, native desktop notifications/packaging/signing, Windows
  CI (deferred by the user) and second-OS capture parity.
- Broader roadmap work: speaker registry/learning and calibration, hierarchical
  summaries/retrieval, advanced mic attribution/echo handling and Phase 2d parity.
- Historical revision selection controls, word alignment and automatic identity
  reconciliation remain outside this increment. No automatic second ASR pass ships.

See [implementation history and current decisions](PHASE_2_IMPLEMENTATION_PLAN.md),
[automatic-call UX](AUTO_CALL_UX.md) and [Phase 1 acceptance gates](PHASE_1_CLOSEOUT.md).
