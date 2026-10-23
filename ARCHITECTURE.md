# ScribeLoop — Architecture Plan

> System-audio → Speaker-separated transcript → LLM summary → (Phase 3) Tool-using agent.
> Target: Linux + macOS. Local-first, privacy-preserving.

---

## 1. Product overview

**ScribeLoop** listens to what your computer is playing (meetings, calls, videos, podcasts),
transcribes it, separates speakers so you know *who said what*, and produces live + final
summaries. In later phases an agent layer turns summaries into actions: notes, calendar
events, todos.

```
System audio ──► Capture ──► VAD ──► ASR (streaming) ──► Diarization ──► Speaker registry
                     │                                                      │
                     │                                              Attributed transcript
                     │                                                      │
                     │                            ┌──── rolling summary ◄───┤
                     │                            │                         │
                     │                     Event bus ──► final summary ──► Storage ──► UI
                     │                                                      │
                     │         summaries + user commands ──► Agent layer (MCP tools)
                     └──── session recorder: encrypted per-stream SLPA (offline pass at Stop; retention §3.1)
```

### Meeting discovery and capture — core requirement (2026-10-22)

ScribeLoop must automatically detect likely active meetings/calls and capture their
local audio, starting with **Microsoft Teams and Slack huddles**, then Zoom and
browser meetings such as Google Meet and Teams web. This is **Phase 1 scope**, not
Phase 4 polish. These are required targets, not currently verified integrations. The first metadata-only
macOS observer, candidate state machine, shared-clock contract, and authenticated
source-selection UI are implemented, alongside an opt-in encrypted five-minute
[recording test](docs/BROWSER_CALL_TEST.md); real-call validation remains pending. See
[discovery implementation](docs/MEETING_DETECTION.md).
See [the detection and capture specification](docs/MEETING_CAPTURE.md).

Detection observes app/process and audio-activity metadata; an open app alone is
not a meeting. A detected candidate prompts the user to select/confirm the source
and recording consent. Detection never silently starts recording (§6). Prefer
per-app capture; whole-system capture requires an explicit scope selection.

Output capture contains remote participants but usually omits the user's own
microphone. **Separate microphone and meeting-output streams are now required in
Phase 1** for a complete conversation, with a common timestamp reference and
separate encrypted storage. Native mic/output source primitives and a shared-clock
transport are implemented with generated-signal checks; physical alignment and
encrypted recorder integration remain open (docs/NATIVE_MEETING_CAPTURE.md).
A microphone can contain multiple people; label it
"Microphone" unless attribution is validated. Phase 2c retains advanced echo
cancellation and attribution work. None of this capture integration is delivered
yet; audio input remains public-fixture replay only, alongside source-selection UI.

---

## 2. Layered architecture

| Layer                         | Responsibility                                               | Tech (recommended)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Capture**             | OS loopback / per-app audio capture, resample to 16 kHz mono | macOS: **Core Audio process taps** (macOS 14.2+, `NSAudioCaptureUsageDescription` — dedicated "system audio recording" permission, lighter than Screen Recording; per-process targeting) with `ScreenCaptureKit` (13+, Screen Recording permission, periodic re-prompt on macOS 15) as fallback. Linux: PipeWire `monitor` / per-stream capture, PulseAudio fallback. Unified behind an `AudioSource` protocol                                                                                                                                                                                                              |
| **Pre-processing**      | Resampling, normalization, Voice Activity Detection          | `silero-vad` (ONNX), `ffmpeg`/`soxr`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Transcription**       | Speech → text, streaming + batch                            | Offline: `faster-whisper` (CTranslate2; **CPU/CUDA only — no Metal backend**; CPU-only on macOS) or `whisper.cpp` (Metal/Core ML) or `mlx-whisper` on Apple Silicon. Streaming: Whisper + LocalAgreement-style confirmation policy, or native streaming ASR (sherpa-onnx streaming Zipformer, Moonshine) — Phase 0 bake-off picks per OS                                                                                                                                                                                                                                                                                     |
| **Diarization**         | "Who is speaking" per segment                                | Batch: `pyannote/speaker-diarization-community-1` (CC-BY-4.0, HF-gated download — see §9). Online, in fallback order: `diart` (compatibility with the pyannote 4 / community-1 stack unverified — Phase 0) → **near-online chunked offline diarization** (re-run the offline pipeline on 30–60 s overlapping chunks; labels arrive ~1 min late — simple, likely good enough for UX) → DIY sliding-window on community-1's segmentation + WeSpeaker embeddings (**one embedding space online and offline**). Note: "pyannote streaming" exists only as pyannoteAI's commercial cloud (Live-1), not an open component |
| **Speaker registry**    | Stable identities across sessions                            | WeSpeaker embeddings (community-1's internal space) or SpeechBrain ECAPA — **one model online and offline**; Resemblyzer excluded (dormant)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **LLM / Summarization** | Rolling + final summaries, structured extraction             | Local default: **llama.cpp embedded in the GPU worker** (llama-cpp-python) — keeps a single GPU owner; **Ollama** supported as an *external* provider (own process/VRAM, budgeted separately); cloud optional. All behind an OpenAI-compatible client                                                                                                                                                                                                                                                                                                                                                                       |
| **Event bus**           | Decoupled pipeline stages                                    | In-process `asyncio` queues first; Redis/NATS later if we split processes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Storage**             | Sessions, transcripts, speakers, summaries; semantic search  | SQLite (+ WAL, SQLCipher) + `sqlite-vec` (SQLCipher+sqlite-vec coexistence verified in Phase 0 — §6); Markdown export (plaintext by design)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Agent (Phase 3)**     | Tool use: notes, calendar, todos                             | Function-calling loop + **MCP (Model Context Protocol)** servers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **App shell / UI**      | Settings, live transcript, summaries                         | Local v0: **FastAPI + browser UI**; Tauri shell/sidecar packaging deferred                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

### 2.1 Why this stack

- **Python core** — every ML piece (whisper, pyannote, silero, embeddings) has first-class Python bindings.
- **Local v0 host** — Python/FastAPI + browser UI; native Tauri menus/tray and managed-sidecar packaging are deferred. ML weights/runtime dominate installed size (see Phase 0 closeout).
- **Local-first LLM** — transcripts stay on-device; Ollama/llama.cpp expose an OpenAI-compatible endpoint so the same code path works with cloud APIs later.
- **Event-driven core** — each stage is a component publishing/subscribing typed events; each stage is testable with recorded audio, and movable to separate processes.
- **Implementation languages** — Python core; local v0 uses FastAPI/browser (2026-10-22 Phase 0 development decision; Tauri deferred); **Swift capture helper on macOS**; GStreamer on Linux. See the [Phase 0 handoff](docs/PHASE_0_CLOSEOUT.md) for implementation and deferred distribution validation.

### 2.2 Simpler fallback for v0

**Selected for local v0 development (2026-10-22):** Python + FastAPI + small browser frontend. Frozen distribution with `briefcase`/`pyinstaller`, Linux packaging, and signing remain unvalidated. Existing Python environment is 1.3 GB plus approximately 1.8 GB MOSS weights; these are installed sizes, not installer measurements. Original packaging-spike rationale: a Tauri + Python-sidecar bundle with PyTorch and models is multi-GB, which undercuts the "tiny binary" argument — if packaging weight/complexity is unacceptable, ship pure Python.

### 2.3 Compute & backpressure model

- **Where things run**: a **native capture helper owns the OS realtime callback** — a Swift helper on macOS (taps deliver audio via an IOProc C callback on a realtime thread; Python's GIL/GC make that fragile, and pyobjc bindings for the 14.2 tap APIs may not exist), piping PCM to the Python core over a local pipe/socket. On Linux: GStreamer `pipewiresrc`/`pulsesrc` into an `appsink` **pulled** from a Python thread (no realtime-callback contract); `pw-cat` subprocess / sounddevice Pulse-monitor are experiments, not defaults. VAD, bus, storage, UI live in the main Python process.
- **GPU ownership**: one **GPU worker process** owns ASR, diarization/embeddings, and an **embedded llama.cpp LLM** (llama-cpp-python bindings) — a single owner of GPU memory. `Ollama` is supported as an *external* provider with its own process and VRAM, budgeted separately; the default is the embedded llama.cpp so the single-owner rule holds.
- **Model swap policy in the worker**: live mode holds streaming-ASR + a 7–8 B LLM; the offline pass swaps to large ASR + diarization, **unloading (or CPU-offloading) the LLM for its duration**, then reloading. Swaps happen between chunks, never mid-chunk.
- **One active session at a time**: a new session's live ASR preempts a previous session's offline pass **at chunk boundaries** (the single GPU worker can only preempt there); the paused pass resumes at low priority.
- **Reference machines** (all §7 targets are per machine):
  - **A — Apple Silicon laptop, 16 GB unified memory**: whisper.cpp via Metal; streaming `small`/`distil` (≤ 2 cores or ≤ 2 GB); offline `large` via Metal; LLM 7–8 B q4 ≈ 5 GB; total live RAM ≤ 10 GB.
  - **B — Linux desktop, 8 GB NVIDIA GPU**: faster-whisper CUDA streaming `small` + offline `large`; pyannote on GPU; **LLM 7–8 B q4** (≈ 5 GB VRAM — a 14 B q4's weights alone exceed the card once ASR and diarization share it; anything larger runs on Machine A's unified memory or the optional cloud path); live CPU ≤ 1.5 cores (GPU does ASR/diarization).
- **Backpressure**: if ASR falls behind realtime it **coalesces** (more audio per decode — higher latency, no speech lost) and the UI shows a "catching up" state. Capture never drops audio (the session recorder writes everything). **Lag cap**: at 30 s lag, Whisper-family streaming downshifts one model size (native streaming models have no smaller size — they go straight to the next step); at 60 s, live ASR pauses, the session is marked "deferred to the offline pass", and the user is warned — lag cannot grow unbounded. **Upshift hysteresis**: a downshifted model stays downshifted until lag stays < 10 s for 60 s (model swaps have load cost — without hysteresis it oscillates). Summarization runs at low priority and pauses under contention.
- **Priority order under contention**: capture > session recorder > live ASR > online diarization > offline pass (preemptible at chunk boundaries) > rolling summary > final summary.

---

## 3. Process & data layout

```
scribeloop/
├── core/                      # Python package "scribeloop-core"
│   ├── capture/
│   │   ├── source.py          # AudioSource protocol (pulls PCM frames)
│   │   ├── native/
│   │   │   ├── macos_observer/ # implemented metadata-only process observer
│   │   │   └── macos_sck/     # Swift helper: ScreenCaptureKit fallback
│   │   ├── macos_client.py    # talks to the Swift helper over a local pipe
│   │   └── linux_pipewire.py  # placeholder: planned GStreamer appsink source
│   ├── meetings/              # metadata contracts, detector, macOS observer client
│   ├── pipeline/
│   │   ├── vad.py             # silero VAD gating
│   │   ├── asr.py             # streaming ASR adapter
│   │   ├── diar.py            # online diarization adapter
│   │   ├── attributor.py      # merges ASR words ↔ diarized speaker turns
│   │   └── bus.py             # typed event bus
│   ├── speakers/
│   │   ├── embeddings.py      # extract + persist voice embeddings
│   │   └── registry.py        # match → named speakers, manual override
│   ├── llm/
│   │   ├── client.py          # OpenAI-compatible client (Ollama/llama.cpp/cloud)
│   │   ├── prompts/           # rolling-summary, final-summary, extraction
│   │   └── schemas.py         # pydantic: Summary, ActionItem, Decision, Participant
│   ├── storage/
│   │   ├── db.py              # sqlite schema + migrations
│   │   └── vectors.py         # sqlite-vec index for "ask past sessions"
│   ├── agent/                 # Phase 3
│   │   ├── loop.py            # tool-calling agent loop
│   │   └── mcp_tools.py       # MCP client → notes/calendar tools
│   └── api/                   # local WS/HTTP server: loopback-only + per-launch token (§6)
└── data/                      # local artifacts; recorder integration still pending
```

### 3.1 Core data model (SQLite)

`PRAGMA foreign_keys=ON; PRAGMA secure_delete=ON;` — both set on every connection at open.

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, started_at INTEGER, ended_at INTEGER,
  source TEXT, title TEXT, status TEXT,
  consent_confirmed INTEGER DEFAULT 0);

-- one row per captured stream ('loopback' | 'mic'): separate encrypted SLPA files
CREATE TABLE session_streams (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  stream TEXT, audio_path TEXT,
  sample_rate INTEGER, started_at INTEGER, ended_at INTEGER);

-- global registry: canonical people. Learning is OPT-IN per person (§6):
-- embeddings are never persisted for unmapped "Unknown-N" speakers.
CREATE TABLE speakers (
  id TEXT PRIMARY KEY, canonical_name TEXT, color TEXT,
  learn_enabled INTEGER DEFAULT 0, created_at INTEGER);

-- one row per exemplar, taken ONLY from offline-reconciled segments
-- (≥ ~30 s, purity-checked); embedding model versioned for re-embedding;
-- source_session_id ties exemplars to origin so "forget this session"
-- can remove the exemplars derived from it
CREATE TABLE speaker_embeddings (
  id TEXT PRIMARY KEY, speaker_id TEXT REFERENCES speakers(id),
  source_session_id TEXT REFERENCES sessions(id),
  model_name TEXT, model_version TEXT,
  embedding BLOB, exemplar_ms INTEGER, created_at INTEGER);

-- per-session local labels ("Speaker 1"), optionally linked to a person
CREATE TABLE session_speakers (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  stream TEXT, local_label TEXT,
  speaker_id TEXT REFERENCES speakers(id),   -- NULL = unmapped
  confidence REAL, renamed_by_user INTEGER DEFAULT 0);

-- transcript. Partials (revision 0) are EPHEMERAL — in-memory only;
-- only confirmed (1) and offline-corrected (2) rows are persisted.
CREATE TABLE segments (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  stream TEXT, local_speaker_id TEXT REFERENCES session_speakers(id),
  start_ms INTEGER, end_ms INTEGER, text TEXT, language TEXT,
  confidence REAL, source TEXT,       -- 'stream' | 'offline'
  revision INTEGER);                  -- 1=confirmed, 2=offline-corrected
CREATE INDEX idx_segments_time ON segments(session_id, start_ms);

-- online→offline segments are N:M (boundaries differ): explicit mapping
-- table; the diff view falls back to time overlap when unmapped
CREATE TABLE segment_links (
  online_segment_id TEXT REFERENCES segments(id),
  offline_segment_id TEXT REFERENCES segments(id),
  overlap_ms INTEGER, method TEXT,
  PRIMARY KEY (online_segment_id, offline_segment_id));
CREATE INDEX idx_links_offline ON segment_links(offline_segment_id);

-- persisted remap table from speaker reconciliation (Flow 2)
CREATE TABLE speaker_remaps (
  session_id TEXT REFERENCES sessions(id),
  stream TEXT, online_label TEXT, offline_label TEXT, method TEXT, confidence REAL,
  PRIMARY KEY (session_id, stream, online_label));

CREATE TABLE words (
  id TEXT PRIMARY KEY, segment_id TEXT REFERENCES segments(id),
  session_id TEXT REFERENCES sessions(id),
  text TEXT, start_ms INTEGER, end_ms INTEGER, confidence REAL, source TEXT);
CREATE INDEX idx_words_time ON words(session_id, start_ms);

CREATE TABLE summaries (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  kind TEXT,                           -- 'rolling' | 'final' | 'section'
  span_start_ms INTEGER, span_end_ms INTEGER,
  content_md TEXT, actions_json TEXT, model_id TEXT, created_at INTEGER);

-- RAG chunks carry time anchors + segment ids so Flow 4 citations
-- resolve to speaker names and timestamps
CREATE TABLE embeddings_chunks (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  chunk_start_ms INTEGER, chunk_end_ms INTEGER,
  segment_ids TEXT,                    -- JSON array
  chunk_text TEXT, model_name TEXT, vec BLOB);

-- audit log: session/state transitions, errors, agent actions ONLY
CREATE TABLE events (
  id INTEGER PRIMARY KEY, session_id TEXT, ts INTEGER,
  level TEXT, type TEXT, payload_json TEXT);
```

**Delete semantics**: deletion is hard — no soft-delete column; rows are `DELETE`d with `secure_delete=ON`. Because WAL retains deleted content until checkpoint, every purge finishes with `wal_checkpoint(TRUNCATE)`; a full `VACUUM` is reserved for bulk migrations (mostly redundant under `secure_delete` and expensive on large DBs). **Phase 0 check (verified 2026-10-21)**: deleting from `sqlite-vec` `vec0` tables with `secure_delete=ON` followed by `wal_checkpoint(TRUNCATE)` **does scrub** vector bytes from the raw DB file — verified by a byte-level scan for a distinctive float pattern after delete (`scripts/check_vec.py`, `tests/test_vectors.py`). The rebuild-into-fresh-table fallback is unnecessary. **Audio is crypto-shredded**: each session's SLPA streams share a per-session key from the keystore. Key deletion prevents new locked reads, subject to unlocked readers and backups; the explicit authorized deletion/retention workflow is implemented; unattended cleanup is deferred. See docs/SECURE_STORAGE.md.

"Forget this person" deletes their embeddings (via `source_session_id`), unlinks `session_speakers` rows, anonymizes their labels in retained transcripts (`[deleted speaker]`), scrubs their labels in `speaker_remaps`, redacts their mentions in `events.payload_json`, flags summaries for redaction review, purges their RAG chunk text, and reminds the user that Markdown exports are plaintext and may still contain their name (§6).

**Bootstrapping a registry person**: rename → opt into learning → exemplars are extracted from that session's **retained** audio (offline-reconciled, ≥ ~30 s, purity-checked). This only works within the audio retention window (default 30 days) — the UI says so at rename time.

**Retention**: audio auto-purges after a configurable period (default 30 days). The offline pass runs immediately after Stop, so the window only limits *later* re-runs; transcripts persist until deleted.

---

## 4. Major feature flows

### Flow 1 — Live session (the core loop)

1. **Discover and start**: automatic detection presents a meeting candidate with app, capture scope, and microphone selection; user confirms recording consent and hits Record (manual Record/hotkey remains available) → shell asks core to open a session. A visible **recording indicator** stays active for the whole session (consent flag recorded on the session — §6).
2. **Capture**: platform source yields 16 kHz mono PCM in ~100 ms frames; the **session recorder** writes full-timeline encrypted SLPA streams (decrypted incrementally for the offline pass; no plaintext session WAV). Mid-session device changes (Bluetooth headset, sample-rate switch) are caught via OS device notifications: the source re-opens, resamples, the recorder continues in the same session, and the event is logged — the session survives.
3. **VAD**: silero gates frames; silence discarded, speech spans time-stamped.
4. **Streaming ASR**: Whisper + a **LocalAgreement-style confirmation policy** (a partial is confirmed when consecutive decode passes agree) — or a native streaming model (sherpa-onnx Zipformer), per the Phase 0 bake-off. Word/segment timestamps retained.
5. **Online diarization**: initial implementation uses near-online community-1 on overlapping 30–60 s chunks; labels arrive delayed. Cross-chunk identities and actual delay require validation.
6. **Attribution**: merge ASR segments with speaker turns → `Segment{local_speaker, start, end, text}` (community-1's exclusive diarization output exists precisely to ease this alignment).
7. **Speaker registry**: embeddings compared to registry → reuse known name or mint "Unknown-N"; user renames once, it sticks (Flow 3).
8. **UI**: transcript events stream to the shell over WebSocket; live pane with per-speaker colors; if ASR lags realtime the UI shows a "catching up" state (§2.3) — never silent drops.
9. **Rolling summary**: incremental map-reduce — the LLM summarizes each ~5 min block and maintains a running digest (hierarchical strategy scales to 1–3 h meetings); topic-shift detection can trigger off-schedule updates.

### Flow 2 — Session finalization (accuracy pass)

1. On Stop, batch pipeline re-runs the **full session recording** (all streams) with the best models (whisper large / pyannote community diarization on the GPU worker).
2. Word-level alignment corrects the streaming transcript; corrected segments are written as `source='offline', revision=2`. Offline and streaming boundaries differ (N:M), so links are stored in `segment_links` (overlap + embedding tie-break), with time overlap as the diff view's fallback.
3. Online ↔ offline speaker reconciliation uses **maximum temporal overlap, many-to-one allowed** (online diarization can split one person into two clusters or merge two people), with embeddings as the tie-break — not one-to-one Hungarian matching. The result persists in `speaker_remaps`; the UI applies it as visible corrections rather than silently changing IDs shown during the live view.
4. **Provisional summary** (same pydantic schema: `Summary{overview, key_points, decisions[], action_items[{task, owner, due?}], open_questions[]}`) is emitted from the **live** transcript at Stop (target ≤ 2 min) so the user has something immediately; the **final summary** is rebuilt from the corrected transcript once the offline pass completes and replaces the provisional. Where budgets allow, the offline pass also runs in **rolling low-priority chunks during the meeting**, shrinking post-Stop time.
5. Store summary + transcript; emit Markdown export (`~/ScribeLoop/2026-10-20-standup.md`).
6. UI shows a diff card: "live summary vs corrected final".

### Flow 3 — Speaker management

- Registry is global, not per-session, and **learning is opt-in per person** (§6): no embeddings are persisted for unmapped "Unknown-N" speakers.
- Profiles keep **multiple exemplars** (up to ~10) taken **only from offline-reconciled segments** with ≥ ~30 s of speech and a purity check — a user-renamed online cluster may be impure, so a manual rename relabels the session and *flags a candidate* exemplar rather than adding one automatically.
- Matching scores against all exemplars (max cosine), using the **same embedding model as offline diarization** (WeSpeaker space) so online and offline comparisons share one space.
- Thresholds are **calibrated per embedding model and audio condition** (clean mic vs VoIP/codec-compressed audio differ); 0.75 / 0.60 are starting defaults, re-measured in Phase 2 on real meeting recordings.
- Per-person controls: pause/resume learning; "forget this person" (delete semantics in §3.1).

### Flow 4 — Retrieval ("what did they say about X?")

- Finalized segments are embedded and indexed (sqlite-vec).
- "Ask your sessions" chat: RAG over transcripts → answer + citations with speaker names and timestamps.

### Flow 5 — Agent actions (Phase 3)

1. Trigger: "File the action items from today's standup."
2. Agent loop plans → calls tools via **MCP**: `notes.create`, `calendar.create_event`, `tasks.create`, `sessions.search`.
3. Confirmation UI: every mutating tool call shows a preview card → approve / edit / deny (human-in-the-loop by default).
4. Results recorded on the session (audit trail) and surfaced as chips under the summary.

---

## 5. Agent architecture (Phase 3 detail)

```
                    ┌────────────────────────┐
   Session events ─►│  Agent runtime         │──► plan / act / observe loop
   (summary ready,  │  (function-calling LLM)│
    user command)   └───────────┬────────────┘
                                │ MCP protocol
        ┌───────────────┬───────┴────────┬────────────────┐
        ▼               ▼                ▼                ▼
   Notes server    Calendar server   Tasks server     Search server
   (Obsidian md /  (macOS EventKit,  (local json /    (sqlite-vec over
    Notion API)     Google Calendar)  todo.txt)        past sessions)
```

- **Framework**: start with a minimal custom loop (~300 lines) over the OpenAI-compatible tool-call API — no heavyweight framework lock-in. Revisit LangGraph only for durable multi-step workflows.
- **MCP-first**: every integration is an MCP server; tools stay reusable by other clients (Claude Desktop, IDEs) and ScribeLoop stays thin.
- **Triggers**: a completed final summary, an explicit user command, or a scheduled reminder (diagram above shows the summary/command path — not raw transcript events).
- **Threat model — transcripts are untrusted input**: meeting content, podcasts, or videos can contain injected instructions ("the assistant should now create a calendar event…"). Therefore: (1) tool arguments derived from transcript text are treated as **data, never as user intent**; (2) all mutating tool calls require explicit user approval with the **full argument payload** visible before execution; (3) transcript content is wrapped in clearly delimited, instruction-neutral blocks in prompts; (4) the agent never sends transcript data to tools the user hasn't enabled; (5) per-session action caps and the `events` audit log bound blast radius.
- **Second-order injection**: summaries and extracted `action_items` are LLM output derived from untrusted transcripts — they inherit the distrust. Agent triggers on summaries apply rules (1)–(5) unchanged to summary text.
- **Exfiltration channels**: "harmless" tool arguments can carry data out (note content containing image URLs that auto-fetch when synced to Notion). Confirmation previews render text as plain text only; any external sync is the user's explicit action. Third-party MCP servers are **version-pinned**, and their network egress reviewed before enabling.
- **Tool-specific rules**: `calendar.create_event` **disallows attendees by default** — transcript-extracted emails must not send invites to third parties; each address is confirmed individually. `sessions.search` stays an **internal** tool by default — exposing it to external MCP clients would flow transcripts to those clients' models, contradicting §6.

---

## 6. Privacy, consent & security

**Recording consent & legal**

- Some jurisdictions require **all-party consent** for recording conversations; others one-party. The app targets compliant use: a **visible recording indicator** while capturing (always on), a per-session consent flag the user affirms, and in-app guidance (share notes, not raw audio; respect meeting-app recording norms). Auto-start features **prompt and pre-arm — they never silently record**.
- **Whose biometrics?** Voiceprints of *other participants* are their special-category data — the user's consent doesn't cover them. Defaults: registry learning is **opt-in per person**; no embeddings are persisted for unmapped "Unknown-N" speakers. Even **transient, in-memory** embeddings of unnamed speakers are biometric processing under GDPR — explicitly in scope of the counsel review. **Have counsel review GDPR and BIPA before shipping** (BIPA has retention-policy and written-release requirements).

**Data protection**

- Database and session audio **encrypted at rest**: SQLCipher for SQLite + AES-256 for audio, keyed from the OS keystore. **Phase 0 verification (done 2026-10-21)**: SQLCipher 4.19.0 (Homebrew; SQLite 3.53.4 base) and the `sqlite-vec` 0.1.9 loadable extension **coexist** — `vec0` loads into an encrypted database and KNN queries work (`.load` via the `sqlcipher` CLI; see `scripts/check_vec.py`, `tests/test_vectors.py`). The fallback (OS volume encryption (FileVault/LUKS/fscrypt) plus application-layer encryption of sensitive BLOB columns) is not needed.
- **Linux keystore fallback**: no guaranteed OS keystore on headless setups — fall back to a passphrase-derived key (argon2).
- Markdown/JSON **exports are plaintext by design** — the encryption story says so explicitly, and the export dialog reminds the user.
- Retention controls: audio auto-purge default 30 days; transcripts until deleted; hard-delete semantics (§3.1): `DELETE` + `VACUUM` with `secure_delete=ON`.
- Nothing leaves the device by default: LLM inference is local; any cloud feature is opt-in and badged "cloud" in the UI.

**Local API security** (core ↔ shell on localhost)

- Bind to `127.0.0.1` only; **random per-launch bearer token** handed to the shell out-of-band. Browsers cannot set `Authorization` headers on WebSockets, so the mechanism is **first-message auth**: the client sends the token as its first WebSocket frame; the server closes the connection on mismatch. The **`Host` header is validated** against a fixed allowlist (DNS-rebinding defense). Stronger alternative where available: a **Unix domain socket** owned by the shell process, proxied to the UI.
- **The UI is an attack surface**: transcripts and summaries are untrusted text rendered in a webview that holds the API token. Everything renders as **sanitized text — no raw HTML**; remote images and auto-fetched links are **blocked** in rendered Markdown (a classic exfiltration path); the current browser UI serves a **strict CSP**; a future Tauri shell must preserve it.
- Agent MCP servers are local processes with scoped tool permissions; cloud egress only via explicitly enabled tools.

**Current implementation (2026-10-23):** Deepgram Nova-3 streaming with v1 per-word diarization is the single default meeting provider. One connection per capture track; no automatic fallback or final second pass. Capture, SLPA/SQLCipher, authenticated recovery, checkpoints and explicit deletion/retention remain local. Uploads need provider-named consent; backend-only keys and durable ten-second audio reservations bound use. Word speaker changes split persisted transcript rows and restore in notes/history. Anonymous identities are scoped to a connection/source; unknown labels and overlap uncertainty stay explicit. Auto multilingual is default across Nova-3’s ten supported multilingual languages, with fixed choices including Vietnamese; no unsupported hint parameters or first-language-lock claim. See [speaker integration and the open real multi-person quality gate](docs/DEEPGRAM_STREAMING.md). Earlier local GPU/streaming diagrams are experimental/future architecture. Original quality, physical and real-call targets remain open.

## 7. Evaluation plan

**Corpus (built in Phase 0, before targets are final)**: AMI + VoxConverse subsets (public), plus ≥ 10 self-recorded real meetings with ground-truth transcripts and speaker labels — real labeling work, scheduled in Phase 0, and **recorded participants must consent**. AMI is far-field room audio while the product ingests loopback VoIP audio, so the corpus also carries **codec-degraded copies** (Opus round-trips at meeting-typical bitrates) of each test file.

**Scoring protocol**: WER currently uses the shared, versioned `unicode-lower-punctuation-v1` protocol (Unicode NFKC + lowercase + punctuation replaced with spaces; preserves accented text, but is not the full language-specific Whisper normalizer); the implemented diagnostic DER scorer uses pyannote.metrics with a **0.25 s reference-boundary collar**; overlapped speech is included in DER and also reported separately (it is the known hard case).

**Phase 0b status (2026-10-21)**: development checkpoint **closed at user request**; original release exit criteria are deferred, not passed. Corpus validation, AMI ES2004a import, WER/throughput tooling, ASR investigation, local MOSS WER/diagnostic DER and precision/attention experiments, one five-minute Opus robustness check, and dedicated community-1 comparisons on both audio conditions are complete. Development defaults are **ES2004a 300–600 s, float16 + SDPA, no quantization**; full-meeting MOSS runs are deferred during development. The five-minute result is 20.31% WER, 32.16% diagnostic DER, and 82.93 s processing. Community-1 separately scored 27.57% original / 27.94% Opus diagnostic DER with automatic speaker counts; hybrid word attribution remains unvalidated. These are exploratory benchmarks, not target sign-off. The ten self-recorded meetings, VoxConverse/codec coverage, validated release scoring, paced live latency, production integration, and target sign-off remain pending. See [the completed/pending checklist](docs/PHASE_0B.md) and [MOSS measurements](docs/MOSS_EVALUATION.md), and [codec comparison](docs/CODEC_EVALUATION.md).

**Targets are provisional** until the Phase 0 baseline run; the numbers below are expectations to confirm or revise:

| Metric                                          | Machine A (Apple Silicon, 16 GB)                                                     | Machine B (Linux, 8 GB NVIDIA) |
| ----------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------ |
| Offline ASR WER (AMI eval)                      | ≤ 15%                                                                               | ≤ 15%                         |
| Streaming ASR WER                               | ≤ 18%, partial p50 < 2 s                                                            | ≤ 18%, partial p50 < 2 s      |
| Offline DER (community-1, AMI)                  | ≤ 20% (online ≤ 30%)                                                               | ≤ 20% (online ≤ 30%)         |
| Offline pass speed                              | ≥ 2× realtime (whisper.cpp Metal)                                                  | ≥ 6× realtime (CUDA)         |
| Provisional summary at Stop (live transcript)   | ≤ 2 min                                                                             | ≤ 2 min                       |
| Final summary (offline pass + LLM)              | pass completion + ≤ 2 min → a 1-h meeting ≈ 32 min at 2× realtime                | ≈ 12 min at 6× realtime      |
| Confirmed-text latency (LocalAgreement)         | p50 < 4 s, p95 < 8 s                                                                 | p50 < 4 s, p95 < 8 s           |
| Attribution accuracy                            | ≥ 95% of words carry the correct local speaker after reconciliation (spot-checked)  | same                           |
| Summary quality (human-rated, 10 real meetings) | action-item precision ≥ 80%, recall ≥ 70%; overview "useful" ≥ 4/5                | same                           |
| Resources (live mode)                           | total RAM ≤ 10 GB; CPU ≤ 3 cores (ASR ≤ 2, VAD+diarization ≤ 0.5, UI/bus ≤ 0.5) | VRAM ≤ 6 GB; CPU ≤ 1.5 cores |

"Offline strictly better than live" is an **aggregate corpus metric** (mean WER/DER), not a per-session guarantee. Metrics gate releases; every pipeline change re-runs the corpus.

---

## 8. Phased roadmap

**Phase 1 replay milestone audited (2026-10-22; full MVP still open):** public WAV replay → cached Whisper chunked ASR →
confirmed SQLite segments → authenticated browser transcript UI is implemented.
SQLCipher/native-keystore storage and authenticated encrypted-audio primitives
are implemented ([details](docs/SECURE_STORAGE.md)); real recordings remain disabled
until the recorder is connected to that lifecycle. VAD/LocalAgreement,
speaker attribution, native recorder integration, and full resume remain pending.
See [Phase 1 progress](docs/PHASE_1.md).

**2026-10-22 local-development handoff:** native ScreenCaptureKit fallback compiles,
framed transport and health diagnostics are implemented, and 62 tests pass. Basic
native silence and controlled-tone capture pass after the user enabled macOS TCC
permission; signing was explicitly deferred by the user.
Original Phase 0 exit criteria remain unmet where listed in the
[closeout and deferred gates](docs/PHASE_0_CLOSEOUT.md). This is a scoped development
handoff, not release or target sign-off.

| Phase                                | Duration (rough) | Deliverable                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Exit criteria (measurable)                                                                                                                                                                                  |
| ------------------------------------ | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0a — Spike**                | 2 wk             | **One OS first** (developer's daily driver) + smoke test on the second. Capture validation with a **signed & notarized build** — prerequisite: **Apple Developer account**; the `NSAudioCaptureUsageDescription` string must live in the **Tauri bundle** (the responsible process — a bare Swift CLI inside it is easy to get wrong; there is no public preflight API, so the UX detects silent frames and guides). **ASR bake-off** (faster-whisper vs whisper.cpp vs sherpa-onnx streaming vs Moonshine — latency + quick WER). **Tauri vs pure-Python packaging decision**, including **GStreamer/PyGObject inside AppImage/deb** (known packaging pain — in the spike, not Phase 4) and **llama-cpp-python wheel availability** per platform (releases lag upstream). **Online-diarization decision** (fallback chain: diart → near-online chunked → DIY). **SQLCipher + sqlite-vec coexistence** and **vec0 delete-scrub checks** (§6, §3.1) | Signed build records loopback PCM; permission prompt attributes correctly; silent-frame detection works; backends chosen with measured numbers; packaging, diarization, and encryption decisions documented |
| **0b — Corpus & baselines**   | 1–2 wk          | **Corpus creation**: ≥ 10 consented, ground-truth-labeled real meetings (hand-correction runs several × realtime — budget accordingly), AMI/VoxConverse subsets, codec-degraded copies; baseline metric run; §7 targets confirmed or revised                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Corpus v1 with baseline WER/DER/latency numbers and a signed-off target table                                                                                                                               |
| **1 — MVP**                   | 4–6 wk          | Automatic meeting detection (Teams/Slack first), confirmed per-app + separate mic capture→VAD→streaming ASR→online diarization→live transcript UI; **SQLite persistence from day one** (sessions, session_streams, session_speakers, segments, words); session recorder; **recording indicator + consent flag**; **local-API token auth (first-message) + encryption at rest before real recordings accumulate**; crash-safe resume                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Meeting detection/capture acceptance in docs/MEETING_CAPTURE.md passes; live transcript usable on corpus v1: streaming WER ≤ 18% (provisional), partial p50 < 2 s; session survives app restart; 2–3 speakers labeled; token auth enforced; DB + audio encrypted                  |
| **2a — Finalization**         | 2 wk             | Offline pass, revision model, word alignment, `segment_links` + `speaker_remaps`, overlap-based (many-to-one) speaker reconciliation + remap UI, final summary, md export                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Offline beats live **in aggregate** on corpus v1 (mean WER/DER); offline pass ≥ 2× realtime (A) / ≥ 6× (B); final summary within §7 per-machine time                                             |
| **2b — Speakers & summaries** | 2 wk             | Speaker registry (**opt-in learning**, multi-exemplar, model-versioned), rename UX, threshold calibration, rolling + final hierarchical summaries, retention + hard-delete controls                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Registry match accuracy ≥ 90% on repeat-speaker test set; summaries hit §7 human-rating targets; forget-person purge verified (DB + vectors + plaintext-export reminder)                                  |
| **2c — Mic & mixed capture**  | 1–2 wk          | Advanced mic attribution and echo handling (basic separate-stream capture moved to Phase 1); both streams recorded **raw (pre-AEC) with a common timestamp reference** so alignment and AEC can be re-run offline (the two devices run on different clocks); **AEC via WebRTC AEC3/speexdsp using the loopback stream as the reference signal**; diarization stays available on the mic stream for in-room meetings (several people can be on one mic — "Me" cannot be assumed)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Own speech attributed as "Me" ≥ 98% on headphone tests; echo suppressed on speaker tests per AEC metrics; in-room mic diarization optional and functional                                                  |
| **2d — Second-OS parity**     | 1–2 wk          | Full second-OS capture, permissions, and packaging parity; **per-OS calendar backend decision** (EventKit on macOS; CalDAV/Evolution or Google Calendar on Linux)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Phase 1 + 2a + 2b + 2c exit criteria pass on **both** OSes                                                                                                                                            |
| **3 — Agent**                 | 3–4 wk          | Agent runtime + MCP servers (notes, calendar, tasks), confirmation UI, RAG chat over sessions; resource budgets enforced                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | "Post standup action items to notes+calendar" works end-to-end; 100% of mutating calls gated by confirmation; injection + second-order injection + exfiltration test suite passes                           |
| **4 — Polish**                | 2–3 wk          | Hotkeys, refine Phase 1 meeting-detection prompts, packaging (dmg/deb/AppImage), auto-update, **opt-in** telemetry                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | One-click daily-driver UX; cold start ≤ 5 s; steady-state resources within §7 budgets                                                                                                                     |

Total ≈ 17–24 weeks for one developer; if estimates slip, phases remain ordered but unscheduled — exit criteria, not dates, gate progression.

---

## 9. Platform specifics & risks

| Concern          | macOS                                                                                                                                                                                                                                                                                                                                     | Linux                                                                                                                            |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Loopback capture | **Core Audio process taps** (macOS 14.2+): `NSAudioCaptureUsageDescription` + a dedicated "system audio recording" permission — lighter than Screen Recording, supports per-process taps; preferred path. `ScreenCaptureKit` (13+) fallback: **Screen Recording** permission, which macOS 15 periodically re-prompts for | PipeWire `monitor` ports (default on modern distros); PulseAudio fallback; per-application capture via PipeWire stream capture |
| ML acceleration  | **faster-whisper (CTranslate2) is CPU-only on macOS — no Metal backend exists (the Metal PR was closed unmerged)**. Apple GPU acceleration: `whisper.cpp` (Metal/Core ML) or `mlx-whisper`. LLM via llama.cpp/Ollama (Metal) or MLX                                                                                            | CUDA (NVIDIA) primary; CPU fallback (faster-whisper fine)                                                                        |
| Capture scope    | Per-process taps (only the meeting app) or whole-system                                                                                                                                                                                                                                                                                   | Per-application (only the meeting app)                                                                                           |
| Permissions UX   | Guided first-run for the audio-capture permission; document macOS 15 re-prompt behavior for SCK users                                                                                                                                                                                                                                     | Typically none needed (monitor sources); Flatpak portal permissions if sandboxed                                                 |

Key risks & mitigations:

- **Overlapping speech** — hardest diarization case; offline pass + full session recording for manual replay.
- **Latency vs accuracy** — dual-path design (streaming for UX, batch for correctness); Phase 0 bake-off picks backends per OS.
- **Model licensing & gating** — audit the **full license chain** of every model in the pipeline (ASR, VAD, segmentation, embedding, LLM), including training-data terms. Moonshine: the streaming and English models are **MIT**; the **legacy non-streaming multilingual models** sit under the use-restricted Moonshine AI Community License — see §11. pyannote community-1 is CC-BY-4.0 but **HF-gated** (conditions acceptance + token to download; the maintainer has confirmed no token-less path is planned — see [discussion #1988](https://github.com/pyannote/pyannote-audio/discussions/1988)). Mitigations: one-time in-app HF-token flow, or redistribute the weights in the installer/mirror (CC-BY-4.0 permits redistribution with attribution). **Review LLM licenses too** — Llama's Community License differs from Apache-2.0 models (Qwen, Mistral); whisper.cpp/faster-whisper models are MIT/Apache. Decide **commercial vs personal distribution early** — it changes which models are usable. (§11 lists sources for every verified claim.)
- **Device changes** (Bluetooth headsets, sample-rate switches mid-session) — OS device notifications, source re-open + resample, session continuity markers (Flow 1).
- **Resource contention** — single GPU worker process, fixed priority order, hard budgets (§2.3), measured in CI (§7).
- **Mixed-language meetings** — per-segment language ID from ASR output stored on segments; summary prompts list detected languages; diarization/embeddings are language-agnostic.
- **Permissions friction** — in-app guided flow; prefer process taps (audio-only permission) over Screen Recording on macOS 14.2+.

---

## 10. Decision log (open questions → defaults)

| Question                                        | Default until challenged                                                                                                                                                                                                                                                                                                                           |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Shell: Tauri vs pure-Python? | Local v0: Python/FastAPI + browser. Tauri deferred; frozen installer and Linux dependency packaging remain unvalidated. |
| Native capture glue (macOS)                     | **Swift helper (default)** — taps deliver audio via an IOProc C callback on a realtime thread; Python's GIL/GC make that fragile, and pyobjc bindings for the 14.2 tap APIs may not exist. pyobjc is the experiment, not the default                                                                                                        |
| Linux capture library                           | GStreamer `pipewiresrc`/`pulsesrc` → `appsink` pulled from a Python thread (default); `pw-cat` subprocess / sounddevice Pulse-monitor = experiments                                                                                                                                                                                       |
| macOS capture API                               | Core Audio process taps (14.2+); ScreenCaptureKit fallback for 13.x                                                                                                                                                                                                                                                                                |
| Streaming ASR engine                            | Per-OS winner of the Phase 0 bake-off (Whisper+LocalAgreement vs sherpa-onnx streaming vs Moonshine — check Moonshine license terms)                                                                                                                                                                                                              |
| Online diarization | Initial Phase 1 implementation: near-online chunked community-1. Reuse the evaluated offline pipeline; stable cross-chunk identity and delay remain unvalidated. diart/DIY alternatives are deferred. |
| Diarization: two-pass (online + offline)?       | Yes — UX and accuracy both matter                                                                                                                                                                                                                                                                                                                 |
| Embedding model                                 | WeSpeaker embeddings (community-1's internal space) or SpeechBrain ECAPA — **one model online and offline**; Resemblyzer excluded (dormant)                                                                                                                                                                                                 |
| Speaker reconciliation                          | Maximum temporal overlap, many-to-one allowed; embeddings as tie-break (not Hungarian one-to-one)                                                                                                                                                                                                                                                  |
| pyannote HF gating                              | One-time in-app token flow; fallback: redistributable CC-BY-4.0 weights bundled in installer                                                                                                                                                                                                                                                       |
| LLM hosting                                     | **llama.cpp embedded in the GPU worker (default — single GPU owner)**; Ollama supported as an external provider (own process/VRAM); cloud optional                                                                                                                                                                                          |
| Agent framework                                 | Custom minimal loop + MCP (no LangChain/LangGraph initially)                                                                                                                                                                                                                                                                                       |
| Calendar attendees in `calendar.create_event` | Disallowed by default; each attendee address confirmed individually                                                                                                                                                                                                                                                                                |
| MCP exposure of `sessions.search`             | Internal by default (external MCP clients would send transcripts to their models)                                                                                                                                                                                                                                                                  |
| Vector store                                    | sqlite-vec — version pinned, behind a `VectorIndex` interface; migrate to Qdrant only if > 10⁷ chunks; **SQLCipher + sqlite-vec coexistence verified in Phase 0**                                                                                                                                                                        |
| Mixed-language handling                         | Per-segment LID + language-tagged segments; revisit if multilingual accuracy disappoints                                                                                                                                                                                                                                                           |

---

## 11. Sources (verified claims worth re-checking)

- CTranslate2 has **no Metal backend** — [Metal PR #1819, closed unmerged](https://github.com/OpenNMT/CTranslate2/pull/1819)
- Core Audio process taps: **macOS 14.2+**, `NSAudioCaptureUsageDescription`, dedicated "system audio recording" permission, per-process targeting — [Apple sample docs](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps)
- macOS 15 **periodically re-prompts** Screen Recording permission — [The Eclectic Light Company](https://eclecticlight.co/2024/09/20/sequoias-screen-recording-permissions-quarterly-re-approval/)
- pyannote community-1: **CC-BY-4.0, HF-gated**; maintainer confirmed no token-less path — [model card](https://huggingface.co/pyannote-community/speaker-diarization-community-1), [pyannote-audio discussion #1988](https://github.com/pyannote/pyannote-audio/discussions/1988)
- **Resemblyzer dormant** — [repo](https://github.com/resemble-ai/Resemblyzer)
- Moonshine: **MIT** for streaming + English models; legacy non-streaming multilingual models under the use-restricted **Moonshine AI Community License** — [moonshine LICENSE](https://github.com/moonshine-ai/moonshine/blob/main/LICENSE)
- "pyannote streaming" exists only as pyannoteAI's **commercial** Live-1 — [docs.pyannote.ai/streaming](https://docs.pyannote.ai/tutorials/streaming-real-time); diart↔pyannote 4 compatibility friction — [diart #278](https://github.com/juanmc2005/diart/issues/278)

Every link above was opened and checked against its primary source while drafting this plan (2026-10-20). Re-check before shipping: repositories move, licenses get relicensed, maintainers change policy.
