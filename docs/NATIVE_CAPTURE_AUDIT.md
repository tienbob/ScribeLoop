# Native capture audit — 2026-10-22

Scope: M3 changes at `ef498a6`, including shared transport, microphone inventory,
discovery refresh, and source-selection controls. Audit and fixes completed.

| Finding | Fix and regression evidence |
| --- | --- |
| High: concurrent close during child startup could orphan either native helper | Lock child registration and cleanup together; delayed-start tests cover SLP1 and SLP2. |
| High: accepted full-second input buffers could lose resampler output and fail graceful drain | Feed bounded input chunks, copy PCM through audio buffer lists, and fragment converted output into legal protocol packets. Native generated check now includes ten full-second buffers per track. |
| Medium: late packets could reset a stale stream watchdog; partial reads received fresh timeout budgets | Apply the earliest stream deadline across header/payload reads and check it before resetting receipt time. Timed child regression covers late arrivals. |
| Medium: manual discovery never polled microphone metadata; app lookup failures skipped device refresh | Poll configured providers without an observer and refresh devices independently of app lookup. Tests cover device removal and authorization changes. |
| Medium: malformed microphone inventories could introduce ambiguous identities | Reject malformed lists, blank/reserved identities, duplicates, and multiple defaults. |
| Medium: candidate selection remained enabled after microphone removal | Require a microphone in both controls and command validation; regression confirms no selection command is sent with a blank input. |
| Low: refreshes rebuilt unchanged select options and disconnect retained pending labels | Cache option inventories and clear pending state before rendering disconnection. Frontend regression checks option identity. |

Validation: **202 Python tests passed**, frontend regression runner passed, Ruff
passed for changed Python files, and `git diff --check` passed. Repository-wide
Ruff also reported existing violations in unrelated source/tests; these were
not changed by this audit. A macOS CI step now builds and checks the generated
native signals; remote CI has not been run here.

The native generated test opened no devices and saved no audio. Each track
contained 4,800,000 samples at 16 kHz (300 seconds); initial offset was 150 ms,
with 0.4375 ms variation from integer-millisecond frame timestamps. This is
accelerated DSP/transport validation, not live latency or physical alignment.

Remaining: physical microphone/loopback alignment, real Teams/Slack routing,
and the M4 encrypted recorder lifecycle. Record remains disabled. Signing is
still deferred; microphone authorization on this host was last reported denied.
