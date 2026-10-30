# Desktop app audit — 2026-10-27

The current development app passes the automated and isolated native checks below.
Two defects were corrected: preview packaging replaced the production artifact,
and Preferences lacked the processing-revocation action promised by first-use copy.
This is a development acceptance audit, not release certification.

## Corrections

| Finding | Change | Evidence |
| --- | --- | --- |
| Preview builder called the production builder, replacing the production app and requiring its operator key. | Build fixture directly at its own destination, with a separate core/manifest, no transcription configuration, and a guard against the production path. | Regression test plus real preview rebuild; production executable, core and credential-configuration hashes stayed unchanged. |
| First-use text offered agreement revocation in Preferences, but no native control existed. | Added **Stop cloud processing…**, confirmation, existing backend revoke command and draft dismissal. Stop/revoke can bypass the uncertain-mutation guard. | Native smoke and actual preview UI: recording disabled, agreement displayed again, existing fixture conversation preserved. Production agreement unchanged. |
| Sidebar cards combine icon controls into a native accessibility row. | Added named Rename/Delete accessibility actions, retaining the context menu and visible buttons. | Compiles; physical button/dialog tests previously passed. Dedicated VoiceOver acceptance remains outstanding. |

## Automated and native validation

- **481 Python tests passed**, including encrypted storage, deletion, provider faults,
  consent/revocation, retention, citations, speaker corrections, call detection,
  receipt idempotence/reconnect and the new packaging regression.
- All **15 frontend replay groups** passed. These cover the development web UI;
  they do not substitute for native UI testing.
- Ruff and `git diff --check` passed.
- Production bundle audit passed: 91 native binaries, no external dylib dependencies,
  no bundled recordings/ML weights, valid ad-hoc signatures. Host-only minimum macOS
  is **27.2**; older systems are not validated.
- Native fixture smoke passed: 230-segment paging, speaker naming, encrypted audio
  duration/seek, playback/pause/speed/window continuation, settings receipts,
  agreement/revocation, reconnect, detection pause and owned shutdown.
- Actual preview UI: citation opened the transcript's first page; Person 1 was named
  “Audit speaker” and that label appeared in transcript rows; Play loaded 2:20 of
  generated encrypted audio; pause and Archive updated the UI. Cancelling detection
  and login draft toggles restored on/off respectively without saving/registering.
- Earlier same-day physical checks cover Rename/Delete dialogs (both cancelled),
  themed selection plus keyboard Up, field focus and Cancel/Escape/outside discard.

## Real API results

Only pinned public AMI English audio or its public transcript was sent. No microphone,
private recording or private meeting transcript was uploaded. Keys were never logged.

| Trial | Result |
| --- | --- |
| Five-minute saved rerun, production pipeline, Nova-3 Auto + diarization | One 19,200,044-byte multichannel upload; 4.55 s pipeline time; 44 segments; 4 anonymous speaker labels. |
| WER against the existing 763-word reference | **26.08%** (199 word errors); no DER/speaker-accuracy claim. |
| Gemini `gemini-3.5-flash-lite` grounded notes | One request; **3.82 s**; citations validated and notes exported. |
| 20-second paced Deepgram WebSocket trial | One connection; first provisional text 6.78 s after stream start, **0.40 s after the first reference speech** (clip begins with 6.38 s before annotated speech). |
| Streaming confirmed text / finish | First final text 10.82 s after stream start; stream finished **0.35 s after audio ended**; excerpt WER **23.08%**. |

Batch client peak RSS: **104.08 MiB**. Batch speed is a single observation, not a
latency guarantee. The live trial uses the provider protocol directly; integration
with the production recorder is covered by generated-PCM/fake-provider tests, not a
new physical Teams call. Gemini is the user's saved provider; this audit did not
replace it with OpenAI. The default OpenAI `gpt-5.4-mini` summary attempt failed with an HTTP status error;
one minimal diagnostic returned **HTTP 429**. Its error body was unavailable, so
quota versus rate limiting is unconfirmed. No further requests were made; live
OpenAI summary acceptance remains blocked by the account/provider response.

Usage: one five-minute two-channel batch request (600 channel-seconds), one
20-second streaming connection, one Gemini notes request, plus the OpenAI probes.
The batch app ledger reserved an estimated $0.10; this is not verified billing.
No automatic retries were used by these audit scripts.

Artifacts: [batch transcript, WER and notes](../data/phase1/full-app-audit-20261027/results.html),
[batch measurements](../data/phase1/full-app-audit-20261027/results.json),
[stream measurements](../data/phase1/full-app-audit-20261027/streaming/results.json),
[OpenAI probe](../data/phase1/full-app-audit-20261027/openai-summary.json).
These ignored local artifacts contain public benchmark text only.

## Remaining release and physical acceptance work

1. **Shared transcription credential is embedded in the distributable bundle.**
   Hiding its UI does not protect it from extraction, and local spending guards
   cannot enforce operator-wide billing. Before distributing to users, introduce
   authenticated server-side access/billing limits or provider-issued short-lived
   credentials. Current packaging intentionally follows the private-demo design.
2. **Developer ID signing/notarization remains deferred.** Ad-hoc rebuilds can change
   Keychain trust. Metadata-only deletion removes unnecessary secret reads but
   cannot promise zero OS authorization prompts for locked vaults or changed identities.
3. Revalidate physical Teams/Slack/browser detection, chat-notification suppression,
   call end timing, capture permission attribution, device changes, sleep/wake and
   long meetings on real hardware. Automated heuristic tests do not prove these.
4. Clean-Mac installation, older supported macOS, signed upgrades, login-item approval
   and VoiceOver/keyboard-only usability remain manual acceptance checks. Windows
   CI remains explicitly deferred from the earlier scope decision.
5. Anonymous speaker labels are connection/source scoped. They are not cross-channel
   person identification, and this audit does not establish overlap separation or DER.
6. Live Keychain access may wait for OS approval; playback exposes Loading while that
   request is pending. This audit observed eventual production audio readiness but
   did not permanently delete a user record to verify the reported password scenario.

Existing production conversations and provider preferences were not renamed, deleted,
revoked or overwritten during this audit. Native fixes were installed without
replacing the core after the final UI-only edits.
