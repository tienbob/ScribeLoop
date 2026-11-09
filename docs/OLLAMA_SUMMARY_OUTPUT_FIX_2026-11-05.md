# Local notes output diagnosis — 5 November 2026

The failed real call was reproduced using a read-only encrypted-workspace
connection and local Ollama. Saved transcript, audio and notes were unchanged
during diagnostics. Raw responses were captured only in a private temporary
directory at the user's explicit request; production does not log raw responses.
The original failing app response had not been retained, so the evidence below
is a fresh reproduction of the same saved transcript/model/prompt.

## Observed failures

The OpenAI-compatible request returned `finish_reason: length`, with 2,266 prompt
tokens, 1,830 completion tokens and 4,096 total tokens. Visible output was only
790 characters of incomplete JSON, ending midway through `key_points`. The
response also contained 5,381 characters of hidden reasoning despite the
top-level `think: false` in the compatible request. Pydantic correctly rejected
an EOF while parsing a string. This was not an ASR, audio-encryption or API-key
failure.

Switching the same request to the native API with explicit context and thinking
options returned complete, schema-valid JSON. A subsequent tighter-prompt test
exposed a different failure: an invented transcript citation ID. Both issues
are addressed, without accepting fabricated or incomplete output.

## Changes

- Ollama notes use native `/api/chat`, `stream: false`, `think: false`,
  `num_ctx: 16384`, `num_predict: 4000`, temperature 0.2.
- Native `format` receives the full Pydantic JSON schema, including array/text
  bounds. Citation strings are additionally constrained to the original source
  IDs in that request; reduction requests use their explicit original-ID list.
- Independent Pydantic and citation validation remains mandatory. No brace
  appending, substring JSON extraction, citation guessing or extra repair library
  is introduced.
- Native incomplete responses and compatible `finish_reason: length` responses
  are identified as `summary_incomplete_output`. They are never silently passed
  through as complete JSON or automatically retried after unknown delivery.
- Prompt includes transcript language metadata and a Vietnamese default when all
  supplied language labels are `vi`. An explicit output-language preference still
  takes precedence. Capabilities and possible features are not action items;
  unsupported decisions/actions should be empty arrays.

Official API references: [native chat](https://docs.ollama.com/api/chat),
[context length](https://docs.ollama.com/context-length).

## Same-call verification

The final request returned `done: true`, `done_reason: stop`, 2,412 prompt tokens
and 980 generated tokens, in approximately 16.2 seconds, with no hidden thinking.
JSON, schema and source-ID validation passed. Notes were in Vietnamese and
decisions/action items were empty rather than treating possible architecture
features as commitments. This verifies the observed output failures, not factual
accuracy: ASR errors in the transcript can still affect notes, and a valid source
ID does not establish that every claim is semantically supported.

Focused summary/rolling tests: 36 passed. Full suite: **731 passed**. Regression coverage includes native
request options, full schema bounds, source-ID enums for section and reduction
requests, untrusted text not adding IDs, local-model/cloud-alias validation,
truncation detection without retries, and language-preference handling.

Canonical app version: 0.1.37 (38). Signature verification passed, and the requested
ScreenCapture/Calendar permission cache resets succeeded. After local Keychain
approval and reconnect, all nine saved conversations loaded. Saved-note
regeneration was then started through the installed app's Meeting menu.
Installed acceptance passed: notes saved as **Ollama · Revision 3** in Vietnamese,
the conversation remained Completed, and the prior `summary_invalid_output`
warning disappeared. The visible result contains a cited overview, key points,
open questions and uncertainty; unsupported action/decision sections are absent.
The app also updated its automatic conversation title. No recording, transcript
replacement, playback, cloud call or email send was performed during this notes
regeneration check.
