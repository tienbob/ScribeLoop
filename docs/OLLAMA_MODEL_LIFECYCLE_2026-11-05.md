# Ollama model lifecycle — 5 November 2026

Local Ollama keeps the most recently used model resident, which holds memory
after the app no longer needs it. New policy in `llm/ollama_lifecycle.py`:

- **Small models (below 8B parameters)** are unloaded as soon as their task
  completes: local transcription at the end of the local runner's finish stage,
  cleanup after a completed cleanup pass, notes after a completed notes
  generation. Each unload is one native `POST /api/generate` with
  `keep_alive: 0`.
- **Larger models — and models whose size is unknown** — stay resident with a
  15-minute keep-alive (`keep_alive: "15m"` on every native request and on the
  warm load) and are unloaded when the app quits. A crash still frees memory
  through the keep-alive.

Size comes from `/api/show`: the raw GGUF `general.parameter_count` when
present, else the human `details.parameter_size` string. Unknown size never
triggers a task-end unload, because reloading an unexpectedly large model is
the more expensive mistake.

## Guarantees and limits

- Unloads go only to loopback endpoints and are best-effort: the saved
  transcript, cleanup revisions and summaries never depend on a successful
  unload, and failures are swallowed. The quit-time release runs concurrently
  with one bounded deadline so shutdown never stalls on a stuck server.
- The quit-time release collects every loopback Ollama model the session may
  hold — notes provider, cleanup provider, local transcription model — and
  applies only to the encrypted production workspace, so test fixtures and
  in-memory stores cannot unload anything.
- Per-request keep-alive means a large local transcription model loaded during
  preparation may be evicted by the idle window during a long quiet meeting and
  reload when Stop transcription starts; that reload is the cost of the
  requested residency policy. A notes or cleanup task keeps its model warm
  across its own requests and unloads it once, at task end.
- Cleanup and notes providers must be on-device Ollama to be managed at all;
  cloud and custom remote endpoints are never sent unload requests.

## Verification

Focused lifecycle, local-transcription, grounded-summary and cleanup suites
pass with the unload paths mocked at the HTTP boundary (MockTransport); no
test touches a real Ollama server, and the quit path is gated to encrypted
storage so test fixtures stay isolated.
