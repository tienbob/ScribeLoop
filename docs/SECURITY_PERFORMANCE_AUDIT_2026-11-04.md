# Security & Performance Audit — Uncommitted Changes (2026-11-04)

Audit of the uncommitted working-tree changes on `main` (base `adb7218`). Covers
security and performance across the full change set. The audit found **no
exploitable vulnerability**; overall posture is strong, with one medium issue in
the new recording-pause gate and a handful of low-severity items.

## Scope

~28 modified files + ~20 new modules, six features:

1. **Google account sign-in** — `core/scribeloop_core/accounts/*` (hosted API,
   desktop OAuth, Keychain vault, Ed25519 entitlements)
2. **Local Ollama transcription** — `pipeline/local_transcription.py`,
   `model_selection.py` (`TranscriptionRouter`), `api/desktop_settings.py`,
   `llm/summary_provider.py`
3. **Recording pause** — `capture/recording_gate.py`, `pipeline/recording.py`,
   `api/server.py` (`set_recording_paused`)
4. **Calendar linking** — `api/calendar_links.py`,
   `shell/macos/Desktop/Calendar.swift`
5. **Email exports** — `api/email_exports.py`,
   `shell/macos/Desktop/EmailSharing.swift`
6. **Mic device-switch recovery** — `capture/native/macos_meeting/main.swift`,
   Deepgram keepalive, roster-query changes, packaging updates.

## Verified strengths

| Area | Practice |
|---|---|
| Input validation | All new wire models use `ConfigDict(extra='forbid', strict=True)` with fixed ID patterns (`^[0-9a-f]{32}$`), length/enum bounds |
| OAuth | PKCE S256, random `state`, random callback path on `127.0.0.1:0`, single-use nonce challenge (consumed via DELETE), 8-connection cap, 8 KB header limit, 3 s read timeout, 180 s overall timeout |
| Token storage | Access/refresh tokens stored only as SHA-256 hashes (acceptable: 256/384-bit random tokens, not passwords); desktop side uses macOS Keychain |
| Entitlements | Ed25519 signatures, issuer + `user_id` binding; the account service rejects a desktop signing key mismatch at startup |
| Network | `trust_env=False` everywhere (no proxy-env leaks), no redirects, loopback-only enforcement for Ollama (summary and ASR), cloud-model names rejected (`remote_host`/`remote_model`/`:cloud` tag checks) |
| Subprocess | Fixed argv, never shell, `DEVNULL` stdio, timeouts (`afconvert` 180 s; `ollama serve` detached session) |
| File hygiene | `0600`/`0700` modes, `O_EXCL` creation, symlink rejection, temp-root owner+mode verification (`st_mode & 0o077 == 0`) |
| Defense in depth | Swift `EmailAttachment` re-verifies the attachment on the client: path containment under the expected folder, uid ownership, permissions `& 0o077 == 0`, size bounds, name allowlist |
| Error hygiene | Sanitized app-authored failure messages; `RequestValidationError` handler avoids echoing token bodies; no docs/openapi endpoints; `TrustedHostMiddleware` loopback-only |
| DoS bounds | 16 KB POST cap, 60/min/IP rate limit, 4096-challenge cap, 2 GB export cache cap, 4 MB response caps, 200 KB summary cap |
| Engine mixing | `TranscriptionRouter` refuses to mix engines for a saved transcript; resume must use the engine that produced it |

Checked and clean: command injection, SSRF (scheme/credential/query/port checks
on every user-supplied URL), path traversal, credential disclosure.

## Findings

### Medium

**M1. `RecordingGate._intervals` grows unboundedly**
`core/scribeloop_core/capture/recording_gate.py:25`

Every pause→resume appends an interval; nothing merges or compacts them.
`filter()` runs per audio frame (~100 frames/s, both tracks) and is
O(intervals), copying the list under a lock each frame. Rapid Pause/Continue
toggling (not otherwise rate-limited) creates CPU pressure in the hot capture
path and unbounded memory.

Fix: merge overlapping/adjacent intervals in `set_paused`, and/or drop
intervals entirely left of the oldest unconsumed frame position; optionally cap
the total with an explicit failure. Add a regression test for rapid toggling.

**M2. Account rate limiter assumes direct client IPs**
`core/scribeloop_core/accounts/server.py:74-84`

`request.client.host` behind the documented reverse-proxy deployment is the
proxy's IP → one shared 60 req/min bucket for all users (availability risk),
and the in-memory `OrderedDict` is not shared across workers. The comment
delegates enforcement to the proxy, but nothing verifies that.

Fix: document `--proxy-headers` / X-Forwarded-For trust explicitly in the
deployment doc, or key on a proxy-validated header, and note the single-worker
assumption.

### Low

**L1. Plaintext exports leave the encrypted workspace for up to 7 days**
`api/email_exports.py` (`TTL = 7*86400`). User-initiated and documented,
0700 dir + 2 GB cap; but TTL is only enforced when `cleanup()` runs (startup
and each `_write`). Consider purging on graceful app quit and confirming
FileVault expectations in `docs/EMAIL_SHARING.md`.

**L2. `calendar_links.read_link` trusts stored JSON**
Parses `json.loads(row[0])['event']` with no re-validation or error handling;
a corrupted row raises inside the page handler. Write path validates strictly;
the read path should tolerate/validate. Also uses private `store._now()`.

**L3. Roster query scalability** — `storage/sessions.py` adds a correlated
`EXISTS (SELECT 1 FROM segments ...)` per identity row. Without an index
covering `(session_id, revision, local_speaker_id)` this is
O(speakers × segments). Verify the index or restructure as a join against a
distinct-speaker set. Also: `import re` sits inside the per-row loop — move it
to module level.

**L4. Private-attribute coupling** — `email_exports.py` calls
`retained._worker` / `retained._durations` and flips `retained.busy`
externally; `TranscriptionRouter.configure` pokes `cloud.base_config` /
`cloud.cloud` directly. Fragile against refactors; add public methods on
`RetainedAudio` and a `configure` method on the runner.

**L5. `OAuthCallback.connections` never decrements** (`accounts/desktop.py:74`)
— 8 total connections for the callback lifetime; browser link-prefetch can
exhaust the budget before the real redirect lands. Track concurrent
connections instead of cumulative, or raise the cap.

**L6. Build-time TCC reset** —
`packaging/macos/build_desktop.py:reset_local_capture_permission` runs
`tccutil reset` for Screen Recording *and Calendar* on the build machine as a
side effect of every build. Gate it behind a flag or environment opt-in.

### Info / design confirmations

- `verify_entitlement` has no upper bound on `issued_at` age (only
  `≤ now+60`). Consistent with the offline "local ownership never expires"
  design; confirm cloud usage is gated by `cloud_until` at the point of use,
  not just at sign-in.
- `AccountStore.challenge()` cleans expired rows only when a new challenge is
  requested — bounded by the 4096 cap, so acceptable.
- Swift concurrency changes look careful: `CaptureOutput` is
  `@unchecked Sendable` with state confined to the delegate queue,
  `switchMicrophone` drains the queue via `queue.sync` before
  `resetMicrophone()`, and `microphoneEnd` rejects stale callbacks from the
  previous input. New self-tests (`--microphone-route-self-test`,
  `--self-test-microphone-switch`) cover the logic.
- The non-audio `EmailExports.prepare` cancellation loop (repeatedly
  `shield`-awaiting the writer thread, then discarding the orphan) is unusual
  but correct; orphans remain bounded by TTL/cleanup.
- `set_recording_paused` requires exact command keys, strict `bool` type, and
  an active-session match — properly validated.

## Performance assessment

Good:

- Deepgram 3 s `KeepAlive` — fixes idle disconnects.
- Conditional `invalidate` handling in `DesktopClient` — fewer full reloads.
- `worker_cancelled` event fixes the emit-blocking cancellation bug.
- Bounded response/queue sizes throughout.
- `CalendarReadQueue` debounces (150 ms) and serializes EventKit reads.

Watch:

- M1 above (hot-path cost).
- L3 (roster query).
- Local transcription is sequential 30 s windows with 300 s timeouts per
  window — a 3 h meeting can take a long time; progress events are emitted and
  it is a deliberate batch design.
- `summary_provider` adds one `/api/show` roundtrip per Ollama summary
  (negligible).

## Recommended remediation order

1. **M1**: interval merging/compaction + cap in `RecordingGate` + regression test.
2. **L2**: validate/tolerate `read_link` parsing; replace `store._now()`.
3. **L3**: move `import re` to module level; verify/add the covering index.
4. **L4**: public wrappers (`RetainedAudio.export_worker()` / `durations()`);
   runner-level `configure` method.
5. **L5**: concurrent callback-connection tracking.
6. **L6**: opt-in flag for the build-time TCC reset.
7. **M2 / L1**: documentation updates (proxy header trust; export TTL /
   quit-time purge decision).
8. Run the full test suite (`pytest` + native Swift tests) to validate.
