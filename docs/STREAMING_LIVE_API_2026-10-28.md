# Live streaming API performance — October 28, 2026

Two manually initiated public-audio trials exercised the production recorder,
encrypted SLPA/SQLCipher storage, Nova-3 Auto/multilingual diarization, streaming
workers, write watchdog and transcript persistence. One connection per trial,
**80 channel-seconds total**, no automatic retries and no summary requests.
No microphone, speaker output, playback, user workspace or private recording was
used. Public speech was paced directly from memory in 171-sample packets.

## Measurements

| Measure | Initial 60-second trial | Fresh 20-second verification |
| --- | --- | --- |
| Pipeline outcome | Failed on closing timestamp validation | **Completed** |
| Successful live connections | 1 | 1 |
| Handshake | 0.932 s | 0.837 s |
| First provider text after first annotated reference speech | 1.722 s | **1.395 s** |
| First provisional text published after reference speech | 1.768 s | 1.433 s |
| First saved segment after reference speech | 4.525 s | 4.678 s |
| Local send-backlog p95 / max | 4.046 / 87.500 ms | **3.283 / 37.597 ms** |
| Socket send-call duration, p95 | 0.379 ms | 0.314 ms |
| Pipeline end after source end | Rejected response at +0.448 s | **Completed at +0.553 s** |
| Provider acknowledged duration | Not consumed before rejection | 19.999 s |
| Final confirmed checkpoint | 55.860 s | 19.999 s |
| Process peak RSS | 75.83 MiB | 72.25 MiB |
| Process CPU time | 3.283 s | 0.552 s |

The clip has **6.38 seconds before the first annotated reference word**. First text
therefore arrived 8.102/7.775 seconds after capture-clock start, rather than after
8 seconds of speech. First-text delay is not a distribution of per-word latency.
Confirmed text waits for an utterance boundary; it is not comparable to provisional
text. Send-call time measures local socket queueing, not network RTT. No native UI
rendering or physical device latency was included.

Both trials kept the local send queue short without accumulating the backlog found
in the earlier synthetic audit. The larger remaining first-text delay is consistent
with upstream network/provider delivery rather than local buffering; that is an
inference from the measured send and receive times, not a server-side trace.

## Defect exposed by the real response, then corrected

The 60-second stream sent all 60 seconds. Deepgram's final Results window ended at
60.000 s, but one word ended at **60.020 s**. The adapter's one-millisecond boundary
check rejected that final response, so the first trial is preserved as **failed**.
The provider response was captured locally for public-audio-only offline inspection;
no extra API request was made to diagnose it.

The adapter now permits at most **100 ms of end rounding only after CloseStream**,
clamping accepted ends to the actual sent timeline. Word starts must remain inside
sent audio. In-progress responses retain the original one-millisecond tolerance;
starts at/outside the boundary and end overruns above 100 ms still fail. Tests cover
20/100 ms closing acceptance, 101 ms rejection, strict live checks and outside starts.

Offline replay validated every captured final Results event after this repair,
including the previously rejected last response: 16 segments through 60.000 s.
The first connection's Metadata acknowledgment was never read, so that replay is
not presented as a successful original run. The separate 20-second verification
then completed normally with live provider acknowledgment.

## Fractional-millisecond tail accounting

The 20-second verification sent/acknowledged **19.999 seconds**: the integer-ms
cursor duration rounded down a final fractional packet. A follow-up local correction
now derives availability from reconstructed sample offsets, retaining pending frames
by exact sample position. Only a completed track may pad its final fractional
millisecond with zeros; live tracks wait for additional samples. Resume validates
the same duration convention.

This final tail correction was verified **offline**, not with a third paid trial.
Regression tests compare every transmitted PCM16 sample for 170/171/320-sample
packets, including a one-sample fractional tail. Every real source sample is preserved;
less than one millisecond of zeros may complete the protocol's integer-ms boundary.
Existing duration tests now use native-like timestamps computed from sample counts.
The real 20-second result above remains the measured pre-tail-correction result.

## Verification, cost accounting and artifacts

- **529 Python tests passed** after both boundary fixes; Ruff and diff checks passed.
- Production app rebuilt, frozen duration-helper inclusion and bundle verification passed. The app was reopened without capture or playback.
- The post-API synthetic streaming audit was repeated without additional provider calls; all seven acceptance checks passed, with its report recorded separately.
- Exactly two connection attempts succeeded, one per trial. App ledger entries were six plus two ten-second reservations, **not eight API connections**.
- The app reserved an estimated **$0.01333** using its existing conservative rate. This is not verified provider billing.
- No settings or existing conversations were edited, renamed or deleted by the benchmark.

The 20-second transcript scored 3 edits / 26 reference words (11.54% WER). The
repaired 60-second response scored 24 / 138 (17.39%) offline. These tiny overlapping
AMI excerpts are diagnostic, not representative quality or speaker-accuracy/DER
acceptance. Only public transcript/response text and numeric metrics were saved.

Reproduction script: `scripts/benchmark_live_streaming.py`. It validates the pinned
fixture hash, reads the bundled credential without printing it, and reserves an
exclusive attempt marker before dispatch. Reusing an output directory fails rather
than silently spending again. Preflight makes no API request:

```sh
.venv/bin/python scripts/benchmark_live_streaming.py \
  --output data/phase1/a-new-explicit-trial --preflight
```

A live run additionally requires `--consent-public-upload`; duration is bounded to
20 or 60 seconds, with one connection and no retry. Local ignored artifacts:

- `data/phase1/live-streaming-performance-20261028/results.json` — original failure and performance.
- Same directory: `public-provider-results.json`, `public-transcript.json`, `offline-tail-validation.json`.
- `data/phase1/live-streaming-performance-20261028-verification/results.json` — successful fresh live verification.
- `data/phase1/streaming-performance-20261028/after-live-api-fixes.json` — final local re-audit.

Long real calls, congestion/thermal/device stress and representative latency/accuracy
still require broader acceptance. This report proves bounded live API operation,
local send performance and successful short-stream finalization within the stated scope.
