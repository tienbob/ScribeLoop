# Phase 1 — meeting capture and live transcript MVP (2026-10-22)

**Update:** an opt-in encrypted five-minute Brave call test is now connected.
See [current test instructions and limitations](BROWSER_CALL_TEST.md). Earlier
M2/M3 verification below describes those increments before this integration.

Status: **public replay milestone audited and finalized** (2026-10-22).
The encrypted-storage foundation is also implemented ([details](SECURE_STORAGE.md)).
The full Phase 1 MVP remains incomplete; its remaining requirements are listed below.
Phase 0's deferred validation gates remain tracked separately.

The [codebase audit against the revised plan](PHASE_1_PLAN_AUDIT.md) records
missing meeting functionality, integration risks, and implementation order.
The [implementation plan](PHASE_1_IMPLEMENTATION_PLAN.md) defines seven ordered
milestones with acceptance gates; discovery foundations and M2 service/UI are
implemented, alongside M3 native source/clock primitives and an initial M4 encrypted
recording test path. Full M4 acceptance and physical capture validation remain pending.

## Requirement change — automatic meeting detection

**Partially implemented; real-call support unverified:** detect active Microsoft Teams calls and Slack
huddles automatically, then support Zoom and browser meetings. Offer a source
confirmation prompt and capture meeting output plus a separate microphone stream
so the conversation includes the user's speech. Detection does not authorize
recording. Keep the consent confirmation and visible recording indicator.

This moves discovery and basic dual-stream capture into Phase 1; they are no
longer deferred polish/microphone features. The existing replay milestone remains
delivered, but does not satisfy this requirement. Implementation must follow
[meeting detection and capture acceptance](MEETING_CAPTURE.md).

The first [discovery increment](MEETING_DETECTION.md) delivers a metadata-only
macOS observer, installed-app registry, candidate state machine, diagnostic CLI,
and shared-clock/source contracts. The second increment adds authenticated
candidate/dismissal controls and manual app/system-source selection. Host output
activity is visible. The third increment adds native microphone/output sources,
real device enumeration, and shared-clock transport ([details](NATIVE_MEETING_CAPTURE.md)).
Real-call/physical capture validation and the encrypted recorder remain pending.

## Delivered

- [x] Authenticated metadata discovery and source-selection UI: candidates with
  evidence/ambiguity, dismissal, manual fallback, freshness/installation checks,
  private request-correlated selection acknowledgement, and disabled Record.
  Enumerated microphone preferences and permission states are shown; no PCM opens.
- [x] Native mic-only/dual capture primitives, common-clock framing, resampling,
  nonblocking transport, and graceful drain. Five-minute generated-signal checks
  pass; microphone access is denied on this host and physical capture is unverified.

- [x] Loopback-only FastAPI server with first-message WebSocket token auth,
  five-second authentication and send timeouts, strict text-frame/Host/Origin checks,
  CSP and no-store headers.
- [x] Browser notebook with configured sample selection, authorization checkbox,
  start/stop controls, active replay indicator, session history, timestamped text,
  error/empty states, and ephemeral partial text.
- [x] One active replay, off-thread ASR, bounded worker/UI queues, and model failure
  handling. Slow clients reconnect to restore confirmed text instead of stalling ASR.
- [x] SQLite persistence for session/stream state and confirmed revision-1 segments.
  Partials are never written to the database. Save before broadcasting each segment.
- [x] Restore history/transcripts after reconnect; mark loading/replaying sessions
  interrupted after application restart. No automatic audio-position resume yet.
- [x] Use cached Whisper-tiny CPU/int8, two threads, four-second chunks for this
  integration slice. No implicit model downloads. Clamp padded decoder timestamps
  to actual supplied audio. MOSS float16 + SDPA batch defaults remain unchanged.
- [x] SQLCipher disk storage with native keystore, fail-closed startup, explicit
  public-demo opt-in, and per-session authenticated encrypted-audio primitives.
  Real recorder integration is still pending.
- [x] Actual local WebSocket/model smoke on public AMI seconds 300–320, plus browser
  checks of authentication, transcript display, new replay, start, and stop.

## Run locally

Install the development/security dependencies (`pip install -e '.[dev,security,sqlcipher]'`) and cache
Whisper-tiny using the existing model setup before replay. The server deliberately
uses `local_files_only=True`; a missing model ends the session as failed.

```bash
.venv/bin/python -m scribeloop_core.api.server \
  --public-fixture data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav
```

The terminal prints a local launch URL containing a token in its fragment. Open
that URL: the page removes the fragment and sends the key as its first WebSocket
message. The key stays in page memory, not local storage. After reload/restart,
use the current launch URL/key again. Do not save this short-lived key in a browser
password manager. The API has no arbitrary path, upload, or model-selection endpoint. Replay uses
only startup-configured fixtures of at most five minutes. Native recording requires
encrypted mode, explicit source selection, and participant consent.

The CLI now defaults to encrypted SQLCipher storage in the ScribeLoop platform
user-data directory, with a key in the native OS keystore. Missing dependencies,
missing keys, locked keystores, or invalid encrypted files fail closed. The old
plaintext demo DB is left untouched. Use explicit `--storage public-demo` for
non-sensitive test storage, or programmatic memory mode for portable tests.
See [secure storage setup and limitations](SECURE_STORAGE.md).

Public-fixture replay and the explicit encrypted native meeting test are separate
inputs. Upload is not enabled. No new recorded audio is created by replay.

## State and transport behavior

Session states: loading → replaying → completed/stopped/failed. On restart,
previously loading/replaying rows become interrupted; confirmed text remains.
Terminal events are published after the worker has closed and the active slot is
released, so an immediate next replay is accepted. Storage failures stop the
producer, report an unavailable state, and disable new replay until server restart.
If that failure also prevents writing the failed status, restart recovery marks
the last durable active state interrupted. No failed write is reported as saved.

Stop is cooperative between frames/decodes; it cannot interrupt a currently blocked
model load or inference call. A future isolated worker must handle hard cancellation.

WAV replay is paced; queue backpressure may delay it without dropping transcript
events. It is not a native recorder and does not validate live capture latency or
capture/recorder priority under contention. Four-second chunking is an integration
baseline, not LocalAgreement or a new accuracy benchmark. No speaker labels or
word attribution are synthesized. VAD gating is not connected in this slice.

The UI disconnects an overflowing client; confirmed text restores through the next
authenticated snapshot. Reconnection is currently manual. Partials may disappear
on disconnect by design. HTTP routes serve static assets and health only; all
session actions and transcript snapshots require WebSocket authentication.

## Verification

- Meeting discovery foundation: native observer build and metadata-only host
  check passed (25 bundled processes, one output-active); actual call detection
  and microphone activity remain unvalidated. See [details](MEETING_DETECTION.md).

- Native Keychain-backed encrypted API: 20 s public replay, 7 confirmed segments,
  successful reconnect, and storage-mode indicator verified in Brave.
- Full local suite: **193 passing tests** after the third increment; changed
  Python code passes Ruff. Frontend replay/discovery regressions and the
  46-module application import smoke also pass.
- ASGI/WebSocket tests use deterministic ASR to verify authorization, malformed
  origins/hosts, consent, fixture access, persistence, non-persistence of partials,
  single active replay, stop, model failure, reconnect, and restart recovery.
- Real-model smoke used a 20-second public excerpt, reached completed, saved
  confirmed text, and restored the same rows over a new authenticated connection.
  Aggregate report: `data/phase1/real-model-smoke.json` (local only).
- Browser visual/interaction check used Brave: authenticate, restore saved text,
  new replay, authorize/start, and stop with visible stopped state. All transcript
  content is inserted with `textContent`, never interpreted as HTML.

## Audit fixes and acceptance

- [x] Removed/replaced/oversized fixture and source/model cleanup failures terminate
  cleanly instead of stranding a loading session. Fixture bounds are rechecked
  before model loading; this is not protection against a hostile local filesystem.
- [x] Terminal state waits for worker cleanup and frees the active slot before
  publication; restarting immediately on the same socket is covered.
- [x] Validate partial and confirmed model output before persistence/broadcast:
  finite confidence, valid UTF-8, bounded text, ordered timestamps within audio.
- [x] Reject malformed origins, binary/oversized protocol frames, and invalid text;
  explicitly close auth timeouts. Bound blocked socket sends and cancel the sibling
  receive/send task when either finishes.
- [x] A storage failure stops the worker and disables replay; queued work is not
  flushed into a failed consumer. Error text does not expose backend/private paths.
- [x] UI partials are scoped to sessions; disconnected controls cannot issue Stop;
  storage-unavailable state disables Start. Node regressions execute the actual
  UI script with a minimal DOM/socket harness (not a substitute for browser layout QA).
- [x] Fix CI smoke dependency mismatch: default smoke imports the application
  module tree; optional `--full-ml` requires separately installed diarization/Sherpa
  extras. Add frontend regression execution to CI. No remote CI run is claimed.
- [x] Build a Python wheel and verify all three UI assets are included. This is
  not a frozen desktop installer or signing validation.
- [x] Audited real-model check: 20 s public audio, 7 saved confirmed segments,
  timestamps within bounds, active slot released, and exact row count restored
  after reconnect. Local aggregate report: `data/phase1/audit-smoke.json`.

```bash
.venv/bin/pytest -q
node tests/frontend_replay.mjs
.venv/bin/python scripts/smoke_imports.py
# Optional, when diarization and Sherpa extras are installed:
.venv/bin/python scripts/smoke_imports.py --full-ml
```

No known unresolved audit defect remains in the scoped public replay milestone.
Stop/model-load cancellation is still cooperative; hard interruption needs an
isolated worker. Public-fixture declarations rely on the local operator, not
license detection. Explicit public-demo plaintext storage is not approved for real meetings.
The following full-MVP items are not waived by this audit.

## Remaining Phase 1 MVP work

1. Validate the delivered discovery/UI against real Teams/Slack calls, including
   mute/listen-only, idle apps, and measured false-positive/detection delay checks.
2. Connect the delivered encrypted DB/audio/keystore foundation to a complete
   recorder lifecycle before new real audio; finish key-aware recovery and deletion.
3. VAD integration with recorder-timeline preservation; LocalAgreement partial and
   confirmation policy, paced latency/resource measurements.
4. Native per-app output and separate microphone capture/recorder integration with lossless recording priority and device
   change/recovery. Preserve Phase 0 signing and second-OS limitations.
5. Near-online community-1 with stable cross-chunk speaker identities, then tested
   speaker attribution; keep concurrent timestamps for real overlap.
6. Authenticated recording UI, restart/resume at an audio checkpoint, and full MVP
   validation against the architecture's corpus/latency/resource criteria.
