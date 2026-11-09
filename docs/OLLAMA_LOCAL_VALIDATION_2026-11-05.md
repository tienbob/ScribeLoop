# Local Ollama validation — 5 November 2026

The real local recorder-to-transcript-to-grounded-notes path completed for English
and Vietnamese using synthetic macOS voices. No physical microphone, call app,
speaker playback, user workspace, cloud inference or paid provider was used.

ASR: `hf.co/mradermacher/Qwen3-ASR-0.6B-GGUF:Q4_K_M`, Ollama 0.40.9.
The local `/api/show` result reports 751,632,384 decoder parameters, Q4_K_M,
audio capability, and no remote model. The audio projector is a separate Q8_0
component, so “4-bit” describes the mixed-precision decoder quantization rather
than every tensor. Downloaded layers are approximately 484 MB + 214 MB.
Notes used the already installed local `gemma4:e2b`; the under-2B limit applies
to ASR. Gemma's E2B tag is not a sub-2B total-parameter model.

Sources: [quantized model](https://huggingface.co/mradermacher/Qwen3-ASR-0.6B-GGUF),
[base model and supported languages](https://github.com/QwenLM/Qwen3-ASR).
The 1.7B-named conversion reports over two billion decoder parameters, so it was
excluded under the user's strict size limit.

## Reproduction

After downloading the models, run:

```sh
.venv/bin/python scripts/check_ollama_local.py --output build/ollama-local-check-clean
```

The script generates audio to files without playback, starts the actual
LocalRecordingRunner with a synthetic source, stops it, waits for ASR, then runs
RollingNotes and saves a source-validated summary. Database and retained audio
use isolated encrypted storage with ephemeral keys. It asserts no transcript is
emitted before Stop. HTTP request hooks reject non-loopback inference and model
downloads during the test. The user's settings and saved calls are not opened.

Artifacts: `build/ollama-local-check-clean/results.json`, English/Vietnamese
transcripts and Markdown notes. Initial results with the protocol-prefix defect
are retained separately in `build/ollama-local-check/` for comparison.

## Results and limits

| Clip | Duration | Transcript | Notes |
| --- | --- | --- | --- |
| English | 8.84 s | Exact normalized word match | Valid JSON/citations; retained Friday test, Alex's report tomorrow, $50 budget and undecided launch |
| Vietnamese | 10.47 s | 2.94% normalized word error; “tính năng” became “tiềm năng” | Valid JSON/citations; retained Nam/report, tomorrow, $50 and undecided launch; propagated the ASR topic error |

The Vietnamese notes were in English despite the conversation-language
instruction. This is a quality limitation of the tested notes-model/prompt
combination. Citation/schema validation establishes structural grounding only;
it does not prove semantic correctness or language compliance. An undecided
launch also appeared in the Decisions section as a statement of non-decision.
Do not describe this small synthetic test as real-call accuracy validation.

Informal warm-run ASR elapsed times were 0.67/0.79 s and notes 19.08/25.55 s.
These include test orchestration and are not performance benchmarks.

## Fix discovered by the run

Ollama exposed Qwen's `language English<asr_text>` / Vietnamese header inside
the transcript. The adapter now strips only an anchored language protocol
header from response text and segment text, and maps English/Vietnamese to
their language codes when a separate language field is absent. Ordinary speech
containing similar words or literal markers elsewhere is preserved.

Three added regression cases cover header cleanup with/without segments and
ordinary-text preservation. Local transcription and summary regressions:
**46 passed**. Full suite: 718 passed in the sandbox; four loopback/audio-encoder
checks failed under sandbox restrictions and all four passed when rerun with
the required local access (722 tests verified across those runs). Lint and
whitespace checks passed.

After the user authorized updating the Settings draft, the canonical app was
rebuilt in place as **0.1.34 (35)** and reopened. Signature and bundle validation
passed; ScreenCapture and Calendar permission caches were reset per the user's
development workflow. All nine saved conversations loaded. Settings persisted
Qwen Q4_K_M ASR and Ollama `gemma4:e2b` notes across restart. The installed app's
silent **Check Ollama** displayed “Ollama is ready” for the Qwen model.
Local transcription and packaging checks: **34 passed**. No recording or
playback was started during installed-app verification. Recording/Calendar
permission prompts may need fresh local approval before use.

## Saved-audio rerun boundary fix

Long saved audio exposed an additional bug absent from the short fixtures:
accumulating whole native packets could exceed the 30-second adapter bound.
Stored timestamp gaps also expanded a request's timeline beyond that bound.
Three regression cases reproduced a failed reprocess before the fix (contiguous
512 ms packets, a seven-second gap, and a sixty-second gap).

The adapter now splits packets at the exact sample boundary and bounds the
timeline span. A gap beyond the current window flushes it and starts at the next
stored sample, avoiding an allocation proportional to a long gap. Tests assert
every request stays bounded, every non-silent input sample is retained, the
replacement revision commits successfully, and no cloud request occurs.
Local transcription/summary checks: 49 passed. Local transcription/audit/
packaging checks: 41 passed. Canonical 0.1.35 (36) rebuilt and signature-verified.

Installed real-call retest: the previously failed Vietnamese conversation
“Thảo luận về tính năng hệ thống và kiến trúc AI” was retried through Audio →
Transcribe again → Transcribe locally. It completed with eight coarse segments
covering approximately four minutes; the earlier transcript revision remained
in history. No playback, fresh capture or cloud inference was initiated.
Unlike the synthetic samples, its transcript contains substantial errors and
repetitive text; this model should not be described as proven accurate for real
Vietnamese calls. The following automatic local notes attempt failed with
`summary_invalid_output`, a separate issue from the fixed ASR chunk rejection.

The retest also exposed a stale transcription warning after success. A committed
replacement's provider event now supersedes earlier transcription failures in
the current view, while historical events remain intact and a later failure is
still shown. Capture-stage failures are not hidden by this rule. Local/DB/
recovery/provenance checks: 55 passed. Final canonical build: 0.1.36 (37).
Full regression suite: 725 passed; the additional capture-failure visibility
regression also passed separately. Lint and whitespace checks passed. The final
Calendar cache reset required re-registering the canonical bundle with macOS;
both ScreenCapture and Calendar resets then succeeded.
