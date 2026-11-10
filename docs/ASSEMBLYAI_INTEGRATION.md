# AssemblyAI transcription option — 2026-11-09

Plan recorded before implementation.

- Preserve capture, encrypted audio, cleanup and notes stages; add AssemblyAI alongside Deepgram and Ollama.
- Keep separate API keys in desktop Keychain settings; never expose them in public settings or bundle them.
- Live: v3 WebSocket, universal-3-6-pro, PCM16 16 kHz, speaker labels and language steering. One connection per source; speaker identities stay scoped to source/session.
- Saved audio: authenticated encrypted cursors → bounded WAV upload → one prerecorded job per source with diarization. No streaming replay or automatic fallback. Keep existing transcript until replacement succeeds.
- Keep cancellation, explicit app agreement, provider provenance, cost reservations and cleanup/notes integration. Session-time streaming estimates are conservative, not billing guarantees.
- UI: AssemblyAI · Cloud, separate key, accurate provider names on upload review. Deepgram-only options remain Deepgram-only.
- Validate protocol mapping, speaker/timestamp checks, key isolation and router selection with injected transports, then rebuild and inspect the native app. A real API smoke test requires a configured AssemblyAI key.

References: https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket and https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/transcripts/submit

Status: implementation and real API validation complete; final app installed at `dist/macos/ScribeLoop.app`.

Real-API validation found that short utterances can carry the documented
`PENDING` speaker label. Treat it as unattributed speech until a later correction,
not as malformed output or an invented person. Add a regression before retrying.

## Implementation and validation

Implemented provider settings and native UI, live v3 streaming with word-level
speaker labels (including PENDING), final speaker corrections, manual full-file
reruns, provider-scoped resume, transcript provenance, and the existing cleanup /
notes sequence. Deepgram and AssemblyAI retain separate credentials. AssemblyAI
uses universal-3-6-pro live and universal-2 for prerecorded reruns; the latter is
saved accurately in revision metadata. No automatic provider fallback is added.

Real API check: 20 seconds of pinned public AMI English audio, no private meetings,
cleanup or summary requests. Live and batch both completed; two live speakers.
First saved text 8.551 s from capture start; live completed 21.921 s (capture ended
20.022 s). Batch completed in 5.365 s. WER 23.08% live and 19.23% batch against the
existing literal reference; omitted repetitions/backchannels count as errors.
This short clip is an integration check, not a general accuracy or diarization
benchmark. Estimated successful-run spend $0.0167, not a provider invoice.

Results: `data/phase1/assemblyai-inspection/results.json` (local generated artifact).
Reproduce explicitly with `python scripts/check_assemblyai_meeting.py`; it reads
the saved AssemblyAI Keychain setting and makes a bounded paid public-audio test.
The native app's Models UI was checked with AssemblyAI selected and its key saved.

Final regression suite: **827 passed**. Ruff and whitespace checks passed.
macOS build: **0.1.46 (47)**, ARM64, macOS 13+, development/ad-hoc signed.
