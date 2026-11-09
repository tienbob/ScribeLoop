# Preferences, Calendar and local-engine integration audit

4 November 2026. Reviewed working tree corresponding to desktop 0.1.21 (22).
Audit only: no production source changes or new app build. Speakers stayed off;
no private recordings, real calendar reads or provider requests were used.

## Findings

### P1 — Saved-audio actions silently select Deepgram in local mode

`Preferences.swift:491` sends reprocess without `model`; `DesktopApp.swift:340`
does the same for resume. Both server contracts default an omitted model to
Deepgram (`server.py:97`, `108`), and the handlers select that model before work.
The durable agreement supplies cloud authorization (`server.py:785`).

With Ollama selected and a saved Deepgram key, Transcribe again can upload retained
audio to Deepgram and incur charges. Without a key it fails instead of using
Ollama. Resume of an existing local transcript also selects the wrong runner and
can fail its provider consistency check. The current upload-oriented confirmation
does not identify the actual provider and does not honor the saved engine choice.

Evidence: parsed the exact native payload shape through both Pydantic contracts;
both resolve the engine to `deepgram`. The handler has no saved-settings override.
No upload was dispatched in this audit.

Fix: include an explicit engine in native actions; validate/resolve it on the
server without a silent cloud default for local-mode desktop actions. Resume must
respect transcript-provider consistency. Show the actual destination in the
confirmation. Cover native command → server routing in regression tests.

### P2 — Calendar refresh cancellation leaves detached reads running

`Calendar.swift:138–145` cancels the previous parent task, but its EventKit read
runs in an unstructured `Task.detached`. Parent cancellation does not stop that
worker. Refreshes from calendar changes, app activation and selection changes can
overlap once the 150 ms debounce has elapsed. Results are discarded by generation,
but the old EventKit query, sort and link parsing continue consuming resources.

Evidence: a synthetic Swift concurrency check using the same parent/detached/value
pattern completed the detached worker after canceling its parent; the worker did
not observe cancellation. No real EventKit performance measurements were made.

Fix: serialize reads, coalesce pending requests to the latest selection and check
cancellation between expensive processing steps. Bound active work, rather than
only suppressing obsolete UI results.

### P2 — Keychain settings saves block the server event loop

`server.py:737` invokes synchronous `DesktopSettings.save()` in the async receive
loop. It performs Keychain write and verification calls (`desktop_settings.py:179–180`).
A slow or interactive Keychain operation stalls server command handling, discovery
and other scheduled work; Preferences remains in its Saving state.

Evidence: used a delayed in-memory vault. A scheduled event-loop timer could not
run during save and resumed afterward. This proves the blocking path, not the
latency of the user's actual Keychain. The path predates the tab layout and remains
in the current implementation.

Fix: serialize saves and run blocking Keychain I/O off the event loop, preserving
consistent settings/recorder reconfiguration and busy-state checks.

### P2 — A failed multi-step Save can already have persisted some changes

Preferences queues save_settings followed by set_detection (`Preferences.swift:433–435`),
then reports a generic failure if a later receipt is rejected (`385–390`). Earlier
accepted settings are already durable. Cancel cannot undo those writes, and the
draft baseline is not advanced on that failure path. A call starting between
commands is one way the later detection update can be rejected.

Evidence: a synthetic desktop websocket session accepted a microphone settings
save, rejected detection, and a reopened settings store still contained the new
gain. No user's preferences were changed.

Fix: track acknowledged changes and report partial success explicitly; update
the baseline so retry/cancel reflect what is actually saved. Alternatively combine
the compatible backend changes into one operation with well-defined failure
semantics. Login-item operations still require explicit partial-success handling.

## Checks and limits

- Focused Calendar, desktop, local transcription and packaging suite: **56 passed**.
- Contract-default, mocked Keychain scheduling, detached-task cancellation and
  partial-save failure reproductions completed.
- These tests do not measure physical device switching, live-call quality, real
  calendar sync latency or whole-app CPU/memory. No local model benchmarks ran.
- The missing tests are integration/failure-path coverage; passing the current
  suite does not disprove these findings.

Recommended fix order: engine routing; settings save failure/concurrency handling;
Calendar read coalescing.

## Resolution

All four findings are addressed in desktop 0.1.22 (23). See the
[fix validation and re-audit](REAUDIT_PREFERENCES_CALENDAR_2026-11-04.md). The
original findings above describe the pre-fix 0.1.21 working tree.
