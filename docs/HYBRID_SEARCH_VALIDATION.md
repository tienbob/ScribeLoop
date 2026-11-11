# Hybrid meeting search

Development validation, 2026-11-11. Branch: `feature/f2-hybrid-search`.

Settings → Models → Meeting search offers Keyword only (default) and Hybrid.
Hybrid supports bundled local multilingual embeddings, OpenAI, Ollama, LM Studio,
and Custom OpenAI-compatible endpoints. Notes generation uses its separate provider.
Search and Ask share the selected retrieval configuration.

The bundled local model is IBM Granite 97M multilingual R2, official quantized ONNX,
384 dimensions. Source and license:
https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2
Model/tokenizer assets occupy approximately 118 MiB. They are fetched by an explicit
developer script, pinned by revision and SHA-256, and bundled with Apache-2.0 notices.
The installed app makes no model download request.

## Data lifecycle and provider contract

- Provider, endpoint, model, dimensions, L2 normalization and implementation version
  identify one index generation. Keys are excluded from its identity and public state.
- A changed identity builds a separate vector table. The old vectors are never
  compared with the new model, even if both output the same dimensions.
- A meeting's current search fingerprint binds semantic chunks to the selected
  revision, title, summary and speaker labels. Stale meetings rebuild on submission.
- Remote indexing sends transcript/summary chunks; each semantic query sends query
  text. The UI identifies cloud processing. Hybrid requires Submit, avoiding a paid
  request on each keystroke. HTTP redirects and automatic retries are disabled.
- Scope is enforced before results return; exact scoped distance avoids losing a
  permitted result behind global top-k hits. Titles cannot fill Ask evidence slots.
- Shape drift, nonfinite values and incomplete batches are rejected. A replacement
  meeting index commits atomically. Failed semantic requests disclose keyword mode.
- Keys are restored only for their saved destination. A changed Custom endpoint
  does not inherit a prior key. Keys are stored by the existing Keychain settings flow.
- Conversation deletion and person-forgetting purge all vector generations and
  their indexing state. Audio retention does not delete preserved transcript text.

## Recorded checks

The final full suite passed all 856 tests. A real bundled-model
regression checks that an English question retrieves the intended Vietnamese passage.
Other checks cover provider response ordering, shape drift, scope isolation, fixed
local dimensions, destination-bound keys and deletion across multiple generations.

The packaging runtime was tested directly with the official model. ONNX Runtime
1.18.1 runs it and declares a macOS 11 native floor, allowing the app's existing
macOS 13 target. Newer wheels tested required macOS 13.3 or 14 and were excluded.

The final app passed signature/deployment/dependency verification: 40 native
binaries, 300,051,745 bundle bytes (about 286 MiB), no external native dependency,
no bundled recording or operator credential, macOS 13 minimum, ad-hoc development
signature. Its isolated `--embedding-smoke` command passed from the frozen app:
384 dimensions, relevant English/Vietnamese similarity 0.7853 versus unrelated
similarity 0.6979. This path opens neither user settings nor the workspace.

Installed-app UI check: the rebuilt app opened, but encrypted-workspace startup
timed out before Settings became accessible. It remains at Retry core; local
Keychain approval is needed to finish the saved-workspace settings check. Native
controls compiled, but the new provider picker has not yet received a completed
hands-on check in the user's workspace.

Small synthetic development measurement (arm64, macOS 27.2.1, Python 3.12,
ONNX Runtime 1.31 development environment): 30 meetings, 240 segments, 30 chunks;
first indexing/query 438.66 ms, 30 warm queries p50 3.10 ms / p95 3.32 ms, process
peak RSS 575.8 MiB including Python and model. This is an in-memory synthetic smoke
measurement, not SQLCipher production-scale acceptance. The packaged runtime uses
1.18.1 and must not inherit those latency numbers as a guarantee.

## Remaining release acceptance

The existing independent 40-query / 40-question evaluation and human grounding
review remain open. The tiny cross-language regression does not prove general
retrieval accuracy. Private meetings were not uploaded to a new embedding provider
for this validation. Cloud provider quota/account access requires the user's own
configured key and an explicit submitted search.
