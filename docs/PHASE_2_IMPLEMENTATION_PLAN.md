# Phase 2 — handoff and implementation order

Phase 1 macOS development is closed; [release/quality gates](PHASE_1_CLOSEOUT.md)
remain explicit. This plan starts with Phase 2a finalization from the architecture.
No Phase 2 implementation is claimed by this handoff.

## Preserved decisions

- One selected transcription backend per operation. Deepgram is live; local MOSS
  Q6_K is after Stop. No automatic cloud fallback, paid retry or extra final ASR pass.
- Source-scoped anonymous speakers are required. Model IDs are not real names or
  persistent identities across independent generations.
- Encrypted capture/storage, explicit recording/upload consent and independent
  Stop remain invariants. Plaintext export is an explicit user action.
- Local live MOSS remains experimental. Five-second windows are computationally
  feasible, but IDs reset and boundary/silence handling needs work. Two-second
  updates with 10–15 seconds of context have not been benchmarked or shipped.
- Native packaging/signing, real app/device validation and Linux parity remain
  separate open work; the browser is still the development host.

## Phase 2a implementation sequence

1. **Revision contracts and persistence.** Distinguish provisional/live text from
   the selected saved transcript revision. Preserve old revisions on failure and
   record provider/model provenance. Add migrations only where current tables are
   insufficient; existing saved recordings must reopen without data loss.
2. **Speaker corrections.** Allow the user to rename/merge anonymous labels within
   a meeting, persist mappings and restore them in notes/history. Keep source and
   generation boundaries explicit; no unvalidated automatic biometric registry.
3. **Grounded final summary.** Use the selected transcript revision and the existing
   structured summary schema. Cite source segment IDs for decisions/action items,
   represent uncertainty and validate output. Provider/model choice and consent
   for any external text request must be explicit before wiring that request.
   Transcript and model output remain untrusted data, never tool instructions.
4. **Markdown export.** Export the selected corrected transcript and summary with
   timestamps/source attribution. Keep export explicit and preserve the fact that
   exported files are plaintext. Do not overwrite an existing file silently.
5. **Revision UI and recovery.** Show current revision, processing/failure state,
   speaker corrections and grounded summary. Failures retain the previous result;
   reconnect and deletion must handle all new data consistently.
6. **Acceptance.** Offline provider mocks verify schema/citations, prompt-injection
   handling, failed replacement, export behavior, migrations and deletion. Run
   Phase 1 regressions and all CI jobs before evaluating any real meeting/provider.

Automatic final retranscription from the original architecture is superseded by
the user's single-path decision. Explicit Transcribe again remains available.
Word-level alignment, automatic speaker reconciliation and a resident local live
worker require separate measured designs; none should invent timing or identity.

## Later Phase 2 work

Phase 2b covers retrieval/search and summary context; Phase 2c covers advanced
mic attribution/echo handling; Phase 2d covers second-OS capture and packaging.
Keep those increments separate from the first usable revision/summary/export flow.
