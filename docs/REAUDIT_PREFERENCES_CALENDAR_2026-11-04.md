# Audit fixes and re-audit

4 November 2026, desktop 0.1.22 (23).

## Closed findings

| Finding | Change | Regression evidence |
| --- | --- | --- |
| P1: Local reruns default to cloud | Native saved-audio commands include an engine. Desktop server resolves omitted reprocess engines from current Preferences and Resume from saved transcript provenance. Explicit stale engines are rejected. The confirmation names Ollama/local or Deepgram/upload and freezes the reviewed session and engine. | Native command checks plus desktop websocket routing with a spy recorder; local omitted/stale payloads, changed Preferences and saved-provider Resume are covered. No provider dispatch. |
| P2: Overlapping Calendar reads | One blocking read at a time, one latest pending request, debounce, cancellation token and cooperative checks around query/processing phases. Cancel does not release the occupied worker slot prematurely. | Native simulated blocking reader proves peak concurrency one, intermediate selections coalesce, canceled results are dropped, and a new request runs after cancellation. |
| P2: Keychain blocks event loop | Validation, blocking persistence and publication are separate. Persistence runs on a worker thread; conflicting mutations are gated while reads and the event loop stay responsive. The app owns the save task through window disconnect/shutdown. | Gate-controlled vault: another client reads before the gate opens, conflicting writes reject, old settings remain visible until commit, and accepted state survives a disconnect. Failure releases the gate and permits retry. |
| P2: Misleading partial save failure | Preferences tracks each accepted step, advances only its acknowledged baseline and clears acknowledged secret drafts. Failure explicitly explains that some changes are already saved; retry/cancel operate on remaining edits. | Native progress-state regression covers accepted settings followed by failed detection. Desktop failure injection preserves the successful first receipt and persisted value. |

## Re-audit refinements

The re-audit added terminal receipt broadcasts to handle a client reconnecting
while Keychain work is still pending. It can first see dispatching and then receive
accepted/rejected without a second reconnect. The regression test closes the
requesting socket while the vault is blocked and verifies the final receipt on a
different client.

Saved-audio confirmations now retain the reviewed command rather than rebuilding
it from mutable selection/settings at the final click. Server validation rejects
an explicit engine if it no longer matches the applicable destination. This avoids
changing the recording or provider underneath an open confirmation.

## Validation and limits

- Full Python suite: **688 passed**. Existing localhost OAuth callback tests ran
  outside the sandbox's port-binding restriction.
- Focused integration suite: **60 passed**.
- Native Calendar and desktop audit executables passed, including new queue,
  engine-command and partial-save checks.
- SwiftUI typecheck passed with the macOS 13 deployment target; final production
  compilation also covers the frozen confirmation change.
- Canonical app replaced in place as 0.1.22 (23). Bundle audit passed: 38 native
  binaries, zero external dependencies, no bundled recordings or operator
  transcription credential. The app reopened successfully with all nine saved
  conversations. Screen Recording and Calendar resets both succeeded.
- No further actionable P1/P2 issue was found in the reviewed fix paths.

These results cover functional routing, state consistency and bounded concurrency.
They are not whole-app performance benchmarks or validation of private call
quality, actual Keychain prompt latency or calendar-provider sync. No model API
calls, real calendar reads, microphone capture or speaker playback were used.

An EventKit synchronous query already in flight cannot be forcibly interrupted;
it occupies the sole worker slot until it returns. Canceled results are discarded,
and further processing stops cooperatively. Settings writes already acknowledged
remain saved if a later detection/login operation fails; the UI now reports that
partial success rather than promising rollback across independent systems.
