# Recognition accuracy controls and comparison — 30 October 2026

**Follow-up:** The original full Gemini provider schema caused HTTP 400 responses. See the [verified correction in 0.1.12](GEMINI_SUMMARY_400_FIX_2026-10-30.md).

## Delivered controls

Native Preferences now has **Recognition accuracy**:

- **Microphone audio for transcription:** processed audio, or the original microphone signal before ScribeLoop's gain/filter. Original audio is retained encrypted for new raw-ASR recordings even when processing is disabled. Playback and meeting-audio capture keep their existing behavior. Selecting original on an older recording without a retained raw track fails visibly; it never silently switches the uploaded audio.
- **Names and vocabulary:** one term per line. Terms are trimmed, deduplicated and validated (50 short terms, conservative combined UTF-8 bound). Both streaming and prerecorded Nova-3 requests use repeated `keyterm` parameters. Settings persist in the existing Keychain settings record.
- **Refine transcript after recording:** off by default. When explicitly enabled, Stop first drains capture/live recognition, then makes one prerecorded upload under the same durable audio budget. Notes follow the final result. A successful pass creates a replacement revision while keeping the live revision. A provider failure retains the live revision and persists a warning before notes proceed; storage commit errors remain visible failures. There is no automatic provider switch or network retry. Resuming saved streaming does not automatically run this additional pass again.

Existing microphone-source defaults and the opt-in final-pass setting are preserved. Our tests did not establish that changing the default audio source improves this call.

The summary prompt now carries literal Unicode instead of an ASCII-escaped Vietnamese transcript and explicitly forbids reconstructing unclear speech into responsibilities, names or decisions. The exact Gemini endpoint receives a JSON-schema response format; all responses still pass local schema/citation validation. This improves the request contract; it does not prove factual faithfulness or guarantee provider compliance. Other compatible endpoints retain their existing request format.

## Six authorized comparisons

The latest completed call's 00:30–01:30 excerpt was used. The original workspace, transcript revisions and saved summary were not changed.

| Trial | Outcome | Audio sent |
| --- | --- | --- |
| Deepgram processed microphone, no vocabulary | Completed | 60 s |
| Deepgram raw microphone, no vocabulary | Completed | 60 s |
| Deepgram raw microphone, vocabulary | Completed | 60 s |
| Deepgram meeting audio, vocabulary | Completed | 60 s |
| Soniox raw microphone, vocabulary | Early stream failure | 0.3 s |
| Soniox meeting audio, vocabulary | Early stream failure | 0.3 s |

All Deepgram requests acknowledged the full 60 seconds and used Nova-3 version `2026-10-16.2`, English + Vietnamese hints. Vocabulary was declared before the trials; it was not tuned to the subsequent human correction. Soniox used `stt-rt-v5`, the same two languages, best-effort strict hints, and vocabulary context. There were exactly six connection attempts and zero retries. Actual provider billing may differ from audio sent; 360 audio seconds was the authorized maximum.

The Soniox trials yielded no usable transcript or quality score. Their initial diagnostic captured only the failure class, so the current rejection reason is unconfirmed. The harness now retains allowlisted error codes/types while excluding upstream messages and credentials. No extra connection was opened to repeat the failed trials.

Raw audio changed the greeting recognition and omitted the later target utterance. Vocabulary did not recover that utterance; the meeting-audio output remained essentially unchanged from the previous test. These observations do not demonstrate an overall accuracy improvement or establish a winning provider.

## Human-verified phrase

The user supplied the correct four-word wording for a microphone phrase around 01:18–01:21. It is stored only inside the encrypted local diagnostic. Word selection uses provider word timestamps within this interval; the rest of the 60-second clip remains unverified.

| Version | Word errors / four reference words |
| --- | --- |
| Existing saved batch segment | 3 / 4 |
| New processed-microphone stream | 4 / 4 |
| New raw-microphone stream | 4 / 4 (omitted) |
| New raw-microphone stream with vocabulary | 4 / 4 (omitted) |

These are single-phrase results, not whole-call WER. Normalization is the existing Unicode normalization/lowercase/punctuation protocol; Vietnamese accents are retained. The reference is whitespace-delimited, so this is a word/syllable error measure rather than linguistic Vietnamese word segmentation. One phrase is insufficient for selecting a production recognizer.

## Reproducible evaluation

`scripts/compare_transcription_quality.py` runs the fixed six-trial comparison with explicit upload consent, an immutable expected latest-call timestamp, a bounded interval, durable attempt journal, and encrypted responses. It never plays audio, writes plaintext PCM, modifies the recording database, or retries. The existing Soniox diagnostic key remains a developer-only benchmark credential; Soniox is not selected as the app's production provider.

- A blank reference template marks every track unverified.
- Scoring requires `human_verified: true` plus nonempty reference text. Reference call/interval identity must match the report.
- A user-corrected phrase can be scored with `--score-only --verified-text ... --phrase-start-s ... --phrase-end-s ...`; no new provider requests occur. The phrase is sealed in the report, not copied to the aggregate score file.
- Decrypted text is shown locally only with `--score-only --review-local`.
- Private responses, the verified phrase, aggregate measurements and attempt journals are in ignored `build/macos/quality-ab-2026-10-30*` files. No private call content is committed in this document.

Further comparison needs more human-corrected excerpts and a functioning alternative-provider account. The production defaults should follow measured results on those excerpts.

## Validation and installed app

- Full Python suite: **618 passed**. This includes encrypted raw/processed selection, missing-raw rejection before batch upload, repeated vocabulary parameters, final-pass success/failure, durable failure warnings, storage-error propagation, settings persistence, structured Gemini request shape, verified-phrase boundaries and redacted single-attempt provider errors.
- Frontend regressions, native Stop/history regressions, Ruff and whitespace checks passed. The synthetic phrase-scoring fixture contains no private call wording.
- The single canonical app was rebuilt in place as **0.1.11 (12)** for Apple Silicon/macOS 13+. Bundle validation passed: 38 native binaries, no external native dependencies or bundled recordings/credentials.
- Preferences now waits for the connected workspace before loading editable settings; Done is disabled while disconnected. Hands-on startup confirmed the gear stays disabled until the saved workspace is ready.
- User-saved recognition preferences survived the final restart. No saved call transcript or summary was replaced by the comparison.
- After local authorization, only ScribeLoop's stale Screen & System Audio Recording entry was refreshed. The final silent in-app check confirms meeting audio allowed, microphone authorized and display available, with no audio recorded. Speakers were never enabled or used by the test workflow.
- Gemini's changed structured-output request was verified with a mock transport, not another billed Gemini request. Semantic summary quality still needs provider evaluation against corrected source text.

## Sources

- [Deepgram keyterms](https://developers.deepgram.com/docs/keyterm)
- [Deepgram audio preprocessing](https://developers.deepgram.com/guides/deep-dives/audio-preprocessing-barge-in)
- [Soniox current models](https://soniox.com/docs/stt/models), [language restrictions](https://soniox.com/docs/stt/concepts/language-restrictions), [context](https://soniox.com/docs/stt/concepts/context)
- [Gemini compatible structured output](https://ai.google.dev/gemini-api/docs/openai)
