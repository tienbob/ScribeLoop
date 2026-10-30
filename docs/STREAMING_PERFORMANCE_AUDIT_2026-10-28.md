# Streaming performance audit — October 28, 2026

**Follow-up:** the findings below describe the initial build. Fixes are now
implemented, rebuilt and re-audited; see the [before/after closeout](STREAMING_PERFORMANCE_CLOSEOUT_2026-10-28.md).

**Result: capture/storage are fast in the local simulation, but streaming has
three actionable bottlenecks.** Runtime code was not changed during this audit.
The new offline harness is `scripts/audit_streaming_performance.py`.

No audio device was opened, speaker output enabled, recording played, private
recording read, or provider/network request made by the harness. Synthetic PCM,
temporary encrypted files, SQLCipher and injected provider sockets were used.
The running native app was inspected read-only and its process counters sampled.

## Findings, in priority order

### P1 — Small native packets can make transcription fall behind continuously

`pipeline/deepgram_meeting.py:196–208` reads the encrypted cursor once per send
refresh, using the cursor's default **eight-frame limit**, then schedules the next
refresh at least 100 ms later. Eight 171-sample packets contain only 85.5 ms of
audio. Receive polling and scheduling can reduce throughput further. This packet
size is realistic: earlier native microphone evidence in this repository includes
170/171-sample packets. The current sender's progress depends on packet size,
rather than the available audio duration.

| Packet size | Audio per packet | 60 s of audio: simulated scheduler time | Throughput |
| --- | --- | --- | --- |
| 170 samples | 10.625 ms | 82.50 s | 0.727× realtime |
| 171 samples | 10.688 ms | 82.00 s | 0.732× realtime |
| 320 samples | 20 ms | 42.10 s | 1.425× realtime |
| 1,600 samples | 100 ms | 8.75 s | 6.857× realtime |

These are deterministic scheduling measurements with real encrypted readers and a
fake clock/provider, **not elapsed host time or provider latency**. The fake clock
advances in receive-timeout increments. Compute/decryption for each trial took only
6–35 ms on this host; the scheduling policy is the limiting factor.

A separate **real-time-paced, eight-second dual-stream simulation**, with Standard
microphone DSP and an encrypted raw microphone copy, confirmed the effect:

- Capture-clock-to-sent-audio-end lag: p50 **1,119 ms**, p95 **2,075 ms**, max **2,254 ms**.
- Processing finished **10.273 s** after starting an eight-second input.
- Capture-to-encrypted-write latency: p50 **0.791 ms**, p95 **0.943 ms**, max **1.612 ms**.
- First saved synthetic transcript event: **113.826 ms** after source start.

The provider responds immediately in this fixture. The first-text result therefore
measures local pipeline overhead, not real speech recognition latency. A final
response is generated per send, which is more frequent than many real calls.

**Fix direction:** read by a bounded audio-duration/sample budget, not eight
packets per timer tick. Increase the frame-count guard consistently with that
duration bound; simply changing `max_frames` would conflict with the existing
16-frame `remember()` guard. Drain bounded catch-up work promptly, while still
reading provider responses and enforcing durable cost reservations. Preserve
authenticated/durable audio-before-upload guarantees.

### P1 — A bounded transcript page triggers whole-transcript work

`api/desktop_contract.py:110` calls `RollingNotes.latest()` for every meeting page.
`llm/rolling.py:53–54` loads and hashes **all transcript segments before checking
whether a completed rolling summary exists**. `SessionStore.segments()` also
re-queries speaker labels for each row. This happens even in the fixture with no
rolling summary jobs and no configured summary key.

| Session segments | Returned rows | Median page time | SQL statements per page |
| --- | --- | --- | --- |
| 100 | 100 | 2.08 ms | 237 |
| 1,000 | 100 | 10.55 ms | 1,137 |
| 5,000 | 100 | 48.48 ms | 5,137 |
| 16,000 | 100 | 156.40 ms | 16,137 |

Payload size stayed around **55–56 KB**. Paging bounds the response but not the
server work. Each timing is the median of three untraced SQLCipher reads; statement
counts were collected separately. Data is synthetic, with one speaker and fixed
short text per segment. These are scaling samples, not statistical latency SLOs.

A CPU profile of the 16,000-segment page attributed **186 of 191 ms** to
`RollingNotes.latest()`, including **16,100 `_speaker_label()` calls**. The native
client can schedule a selected meeting-page refresh every 300 ms during updates
(`DesktopClient.swift:333–338`). Page reads run synchronously on the backend event
loop, so long transcripts can delay transcript persistence, commands and UI events.
The report does not claim this delay was observed in a live multi-hour call.

**Fix direction:** short-circuit when no completed section notes exist; fetch and
validate only the candidate section's bounded inputs, or use an invalidated cache.
Batch/cache speaker labels. Avoid fetching unchanged full detail for every segment
event, and add a page-work/SQL-count regression at long-transcript scale.

### P1 — Provider writes are not bounded by the advertised receive/close timeouts

`deepgram_meeting.py:208` calls synchronous `ws.send()` without a write deadline.
The configured `open_timeout=10`, `close_timeout=2`, and `recv(timeout=.05)` don't
bound an in-progress send. Inspection of installed **websockets 17.3** confirms
that `Connection.send_data()` uses blocking `socket.sendall()`; the close timeout
is applied only after a close deadline exists.

A bounded fake-send fixture remained blocked **200 ms after cancellation** and
returned only when the harness released the send. No actual network stall was
induced. Static inspection establishes that the stream loop cannot check
cancellation or its finalization deadline while inside that call. One blocked
track also prevents the executor from finishing the two-track transcription task.
Capture is independently threaded and can continue retaining encrypted audio, but
transcription completion/shutdown may require the native host's force-stop path.

**Fix direction:** make sends deadline- and cancellation-aware, and actively close
a stuck connection on cancellation. Add controlled stalled-write tests for Stop,
revocation, Quit and failure of the other stream. Keep paid retries disabled.

### P2 — Native UI does not expose the streaming lag it already receives

The backend emits `transcription_progress` with recorded/confirmed/lag times.
`DesktopClient.swift:270–273` handles batch modes but ignores streaming progress.
Users can see Recording without knowing that the transcript is catching up.
Show a bounded lag/status indicator, distinguishing local send backlog from
provider confirmation delay. This helps diagnose slow networks after fixing the
local scheduler; it does not replace that fix.

## Resource observations and working safeguards

The paced encrypted/DSP simulation used **1.195 process CPU seconds over 10.273 s**.
Event-loop extra delay was p50 **0.687 ms**, p95 **1.278 ms**, max **3.579 ms**.
Its local capture path was fast; increasing local compute capacity alone will not
fix the send policy. This test does not include SwiftUI rendering or native device
callbacks. Harness-wide RSS high-water was **95.81 MiB**, including long-page
fixtures; it is not steady-state application RAM or a long-run leak test.

A single running-app snapshot, after user testing, showed:

| Process | RSS | Reported CPU |
| --- | --- | --- |
| Native app | 176.06 MiB | 5.4% |
| Python core | 87.52 MiB | 4.2% |

The UI showed three completed conversations and no active recording. These are
single `ps` readings, not interval CPU measurements or an idle/streaming budget
sign-off. No leak or steady CPU conclusion follows from them.

Existing safeguards remain useful: bounded capture/output/client queues, slow
client disconnection, independent encrypted capture, lazy/cached key acquisition,
authenticated cursor checkpoints, bounded pages/playback windows, pre-dispatch
spend reservations, and no automatic provider fallback or paid retry. The sender
also calls `fsync` on durable reads; optimize that only with a replacement durable
watermark contract, not by removing persistence-before-upload guarantees.

## Reproduction and limits

```sh
.venv/bin/python scripts/audit_streaming_performance.py \
  --output data/phase1/streaming-performance-20261028/results.json
```

The JSON includes the scheduler matrix, page timings/counts/profile, paced pipeline
and blocked-send fixture. The separate running-app snapshot was appended to the
local report after the harness run. The directory is gitignored and contains only
synthetic measurements/aggregate process counters. Temporary audio is encrypted
and deleted with the isolated workspace afterward.

All **59 focused streaming/recording/desktop/microphone regressions passed**, as did
Ruff and diff checks. The preceding full implementation suite had 511 passing
tests; this audit did not rerun that entire suite or rebuild the production app.

Real provider/network latency, native capture under hardware/device stress,
SwiftUI frame rate, multi-hour RAM growth, disk-pressure behavior, battery/thermal
impact and physical Stop/revocation acceptance remain unmeasured here. No live
call was started, and existing user conversations/settings were not modified.

Recommended implementation order: **duration-bounded streaming reads → bounded
page/rolling-note work → cancellable write deadlines → visible streaming lag**.
