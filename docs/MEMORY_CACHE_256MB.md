# Shared 256 MB memory cache — October 28, 2026

The native app core now uses one shared **256 MiB (268,435,456 byte)** LRU budget
for cached authenticated audio blocks/indexes, transcript pages, speaker rosters,
validated rolling notes, transcript hashes and source counts. Entries allocate only
when used; the app does not reserve 256 MiB at startup. This is an accounted cache
budget, not a cap on total process RSS, temporary request buffers or native players.

## Behavior

- Least-recently-used entries are evicted across all namespaces to stay within the shared budget. Oversized entries are skipped. Accounting includes Python containers, NumPy storage and bookkeeping allowance; shared objects across entries may be counted conservatively more than once.
- Native audio seeks reuse authenticated 60-second float32 PCM blocks. Overlapping windows reuse the same blocks. Callers receive independent output arrays, so changing an output cannot poison a cached block.
- Original audio stays encrypted on disk. No plaintext cache file or swap-management change is introduced. The cache creates no capture, playback or provider requests.
- Cache hits still check the audio key lease and file signature. Changed ciphertext invalidates cached indexes/PCM and must authenticate again. Authorized audio-key removal/deletion evicts the session while serialized with playback reads; existing caller-held buffers remain subject to the prior unlocked-reader limits.
- Transcript pages and rosters are invalidated by schema-12 transcript/speaker/person change generations. Whole transcripts over 201 rows are not duplicated as page-cache entries. Public page results are shallow-copied because their fields are scalar; UI-added fields do not mutate cached originals.
- Notes/hash/source-count caches now share the byte budget rather than an independent eight-session limit. Their existing source validation and stale-note behavior are retained. Forget-person clears cached data for affected sessions before rebuilding derived notes.
- Session deletion/expiry clears owned entries. Core shutdown/workspace change clears the shared cache along with unlocked audio keys. This releases process references; Python does not promise forensic wiping of every previously allocated buffer.

The production server explicitly shares the cache between SessionStore and
SessionAudioStore. RetainedAudio does the same for standalone clients. No new
Keychain item, configuration screen or database migration is required.

## Silent benchmark

A temporary encrypted fixture contains two 120-second generated audio tracks in
171-sample packets. WAV response bytes are created in memory and discarded; no
player or audio device is opened. Cache-disabled and cache-enabled cases use the
same new code and files. Both first-minute and second-minute blocks are warmed
before five overlapping 30–90-second seeks.

| Measurement | Cache disabled | Shared 256 MiB cache |
| --- | --- | --- |
| First seek | 151.8 ms | 134.6 ms |
| Median overlapping seek | 191.8 ms | **1.6 ms** |
| Cursor/decryption opens during five repeated seeks | 30 | **0** |
| Accounted retained entries after warming | 0 | **15,457,643 bytes (~14.7 MiB)** |

This is a generated local benchmark, not physical playback latency or a comparison
to the old implementation. First access still authenticates/indexes files. The
configured upper bound was tested with smaller deterministic budgets for eviction
and oversized entries; the benchmark did not fill 256 MiB unnecessarily.

Reproduce without any sound output:

```sh
.venv/bin/python scripts/benchmark_memory_cache.py \
  --output data/phase1/memory-cache-256mb-20261028/results.json
```

## Verification

**537 Python tests passed**, including shared-budget LRU eviction, retained array
base accounting, concurrent invalidation, a zero-budget mode, caller mutation,
source edits, cached-ciphertext tampering, overlapping seeks and key deletion.
The local streaming audit also passed all seven acceptance checks: p95 local send
lag was 3.03 ms in the paced synthetic fixture, and the 16,000-segment page used 32
SQL statements with a 3.42 ms median. No new paid/live provider test was made.

Production rebuild, frozen 256 MiB constant inspection, bundle verification,
Ruff and diff checks passed. The native app was restarted to activate the cache.
Initial startup attempts timed out; a subsequent retry connected successfully and
showed both saved conversations. The initial delay's cause was not established.
Artifacts live under `data/phase1/memory-cache-256mb-20261028/` (gitignored):
`results.json` and `streaming-audit.json`. Existing recordings/provider preferences
were not changed, and speaker output was not enabled.
