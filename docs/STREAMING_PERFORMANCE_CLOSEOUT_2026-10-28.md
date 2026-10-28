# Streaming performance fixes and re-audit — October 28, 2026

**Live follow-up:** [real API trials](STREAMING_LIVE_API_2026-10-28.md) now document
provider latency, a repaired closing timestamp edge case, successful live
finalization and the final offline sample-tail regression checks.

Implemented and installed the fixes from the
[initial audit](STREAMING_PERFORMANCE_AUDIT_2026-10-28.md). The rebuilt native app
reconnected successfully and displayed all three existing conversations. No real
recording, playback, speaker-output operation or paid provider request was used
for this work. Saved recordings, transcript content and provider preferences were
not changed by the fixes or validation.

## Before and after

Same local synthetic audit harness, real SQLCipher/SLPA storage and injected provider:

| Check | Before | Final re-audit |
| --- | --- | --- |
| Send lag, p95, 8-second paced dual-stream test | 2,074.7 ms | **3.0 ms** |
| Finish time for the 8-second input | 10.273 s | **8.137 s** |
| 100-row page, 16,000 saved segments, median | 156.4 ms | **4.1 ms** |
| SQL statements for that page | 16,137 | **35** |
| Blocked send still running 200 ms after cancel | Yes | **No** |

The small-packet scheduler matrix now has no artificial refresh waits for 170/171-
sample packets while catching up on 60 seconds of saved audio. Local read/compute
work took about 27 ms for the 171-sample case. Zero simulated wait is **not zero
provider latency**: the harness responds immediately and uses a deterministic clock.
320/1,600-sample cases completed with 0.25 s of simulated scheduler wait.

The paced fixture includes Standard microphone processing, encrypted original mic
retention, two encrypted source tracks, SQLCipher persistence and the production
transcription workers. Final capture-to-write p95 was 0.792 ms and event-loop extra
wait p95 was 0.718 ms. These short samples are not resource, latency or hardware
SLOs. Fake final results arrive more often than many real calls.

## Implementation

**Duration-bounded streaming reads.** The cursor accepts a sample budget as well
as a packet-count limit. The sender fills at most a one-second target per iteration,
with bounded frame overshoot and pending PCM guards, then gives provider responses
a turn. It catches up immediately while data remains rather than waiting another
100 ms after each eight-packet read. Cost reservations still precede dispatch, and
all uploaded PCM still comes from authenticated durable reads. No retry/fallback
was introduced.

**Cancellable writes.** A per-connection watchdog enforces a default five-second
write deadline and observes cancellation every 20 ms. It shuts down the underlying
socket to wake a blocked `sendall` without acquiring the WebSocket protocol mutex.
Timeout/cancellation preserves encrypted audio and never retries. Existing connect,
receive and close/finalization limits remain separate. Tests saturate an actual
local socketpair for both cancellation and deadline expiry; the audit also exercises
the complete stream loop with an abortable fake provider. Real internet congestion
and remote provider failures were not induced.

**Less transcript work per page.** Speaker labels are fetched together, replacing
one query per transcript row. Rolling-note reads return immediately when no
completed section notes exist. For existing notes, bounded per-store caches reuse
validated results. Schema **12** adds per-session, 15-minute source-bucket generation
metadata and triggers for transcript and speaker/person changes. Later live
sections don't invalidate closed section notes; changing their source does. Warm
stale results are cached too, without returning invalid notes. Final-summary hashes
are cached, with source-count checks making append-only resumes stale cheaply.
Caches hold at most eight sessions/summary identities each. The migration is
additive metadata; it does not rewrite audio or transcript rows.

**Streaming feedback.** Backend progress distinguishes waiting-to-send audio from
sent audio awaiting confirmation. The native window displays Catching up or
Awaiting confirmed text when applicable, uses a finishing-live label after Stop,
and clears old progress after reconnect, shutdown or a transcription failure.
Periodic progress is throttled; confirmed results can also update it. A separate
native smoke checks status labels without starting the core or any audio device.

## Existing-note correctness and cold-cache limits

At 16,000 segments with both completed rolling notes and final notes:

- First validation: **138.5 ms**; recurring warm page median: **4.6 ms**.
- First page after a later transcript append: **5.7 ms**; final notes became stale while closed section notes remained valid.
- First page after changing an original source: **93.8 ms**; affected section notes disappeared and final notes stayed stale.
- Repeated stale pages: **5.6 ms** median.

Cold validation and edits that affect existing sources still perform a full check.
The improvement is removal of repeated whole-transcript work from ordinary live
refreshes. Tests verify invalidation/restoration for text, speaker labels, person
links and canonical person names, alongside checkpoint-only and future-section
updates. The audit's extra existing-note fixtures increase its RSS high-water;
that number is not comparable to the smaller initial harness workload or evidence
of application memory growth.

## Verification

- **521 Python tests passed** in the final full suite.
- All **15 browser replay groups** passed.
- The silent native streaming-label smoke passed. It is included in macOS desktop CI; remote CI was not run here.
- Production native rebuild, frozen-core inspection (write guard and schema 12), bundle dependency/signature verification, Ruff and diff checks passed.
- The rebuilt app opened its existing workspace and displayed three saved conversations and saved notes. No new call or playback was started to check the banner.
- **All seven local audit acceptance checks passed:** scheduler catch-up, bounded page SQL, paced send-lag p95 under 250 ms, finish within one second of input end, blocked-write cancellation, section invalidation on source edits, and final-note invalidation on append.

Reproduce the silent local re-audit:

```sh
.venv/bin/python scripts/audit_streaming_performance.py \
  --output data/phase1/streaming-performance-20261028/after.json
```

Local ignored artifacts preserve `before.json` and `after.json`. The final harness
includes added existing-note cache validation; direct before/after table comparisons
use the original unchanged fixture cases. Page timings use three repetitions,
with SQL tracing performed separately. Results vary with host load.

Real provider/network response latency, physical call routing, multi-hour memory,
thermal/battery use and SwiftUI frame-rate acceptance remain open. This re-audit
establishes the local fixes and regression guards within that explicit scope.
