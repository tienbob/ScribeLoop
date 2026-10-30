# Gemini summary HTTP 400 regression — 30 October 2026

## Cause and correction

Version 0.1.11 added the complete Pydantic `GroundedSummary` schema to Gemini's compatible API request. The provider rejected that request format with HTTP 400. The earlier mock transport check proved the request shape but did not establish provider compatibility.

A controlled, user-authorized synthetic comparison confirmed the regression with the same configured Gemini model:

| Provider grammar | HTTP result | Local validation |
| --- | --- | --- |
| Original full Pydantic schema | 400 | No summary |
| Simplified schema | 200 | Passed schema and source-ID validation |

Only the schema differed between the two requests. No private call audio or transcript was sent, and neither request was retried. The old rejection's detailed body was not retained by the diagnostic, so the specific unsupported schema keyword is not claimed.

The corrected provider grammar inlines the small acyclic definitions, removes references/defaults and decoding length/array bounds, and explicitly requires nullable optional fields. Google documents that overly complex schemas, including nested array bounds, can cause 400 responses. The app still applies the complete Pydantic model, length/list limits, source-ID validation and durable request budget after generation. It does not weaken local validation or retry HTTP failures automatically.

The fix is packaged as **0.1.12 (13)**, replacing the canonical app in place. The full Python suite passed **620 tests**, including tests for the simplified provider grammar and unchanged rejection of oversized lists, empty citations and unknown source IDs. Ruff and whitespace checks passed.

## Scope

This fixes request compatibility. It does not establish factual accuracy of a summary or correct recognition errors in the input transcript. The synthetic success still needs ordinary text review, as do summaries of real calls.

The latest completed call was observed with the persisted end reason indicating call inactivity. Its recording stopped; the HTTP 400 occurred later during notes generation. The recording and four saved transcript segments remain available. The user separately authorized one manual regeneration of that call's notes after installing the fix.

[Google structured-output limitations](https://ai.google.dev/gemini-api/docs/structured-output#limitations) and [compatible API structured output](https://ai.google.dev/gemini-api/docs/openai).
