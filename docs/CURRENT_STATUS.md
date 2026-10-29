# Current status and acceptance checklist

**Recording Stop update:** internal build 0.1.3 (4) fixes late Stop falling into
the replay handler, reconciles current capture state, shows the actual call-end
countdown/unknown state, and displays saved failure diagnostics. 539 tests and
native status/bundle checks passed; successful physical call acceptance remains
open. See [Stop fix details](RECORDING_STOP_FIX_2026-10-29.md).

**Internal Mac packaging update:** version 0.1.2 (3) removes bundled operator
credentials and adds customer-owned Deepgram keys in Preferences/Keychain.
Apple Silicon DMG packaging and a Developer ID/notarization pipeline are added.
No Developer ID identity is installed; signed/notarized acceptance remains open.
The new deployment target is macOS 13 Ventura on Apple Silicon. Every native
dependency must pass the minimum-OS audit; actual Ventura runtime acceptance
remains open. Automatic detection needs macOS 14.2; earlier versions use manual
New recording. See [internal distribution](INTERNAL_MAC_DISTRIBUTION.md).
Earlier descriptions of a bundled transcription credential below are historical.
Version 0.1.2's rebuilt bundle passes the native compatibility audit: 34 binaries
require macOS 11 and four require macOS 13; no external library paths remain.
The final DMG mounted successfully and all 114 app files matched the built app.
539 development tests passed; portable-runtime tests passed 536 with the optional
local diarization module skipped. This confirms the build target, not physical
Ventura acceptance. No speaker output or provider request was used.

**Streaming performance fixes shipped:** duration-bounded reads, cancellable
provider writes, bounded recurring note/page work and native lag feedback are
implemented and re-audited. See the
[before/after closeout](STREAMING_PERFORMANCE_CLOSEOUT_2026-10-28.md): 521 tests,
seven audit checks, native build/bundle verification and silent status smoke passed.
Schema 12 adds source-change metadata; cold source validation and physical/network
acceptance retain their documented limits.

Updated 2026-10-28. Native macOS development app implemented; release acceptance
remains open. This checklist supersedes old proposed/pending statements in dated
plans. [Desktop instructions](../packaging/macos/DESKTOP.md) describe the current
workflow; the [October 27 audit](MACOS_APP_AUDIT_2026-10-27.md) records earlier
physical and real-provider evidence.

## Implemented

- SwiftUI notebook, menu bar and app-owned bundled Python core; no browser/server setup needed to run the app.
- First-use agreement/revocation and explicit Record / Not now choice; detection never starts capture.
- Microphone-only or app/system output plus microphone, encrypted recording, Deepgram live anonymous speakers, explicit saved-audio batch rerun, recovery/resume.
- History, manual titles, conversation deletion, speaker naming/merging, manual people links/forget, paged transcripts, cited notes and Markdown export.
- One workspace unlock for new/migrated audio keys (legacy keys import once on first access), bounded playback, 30-day audio expiry/archive, recording presets/cost guards, configurable notes providers and summary instructions, optional login launch.

## Checks completed October 28

| Check | Result and scope |
| --- | --- |
| Python suite | 487 passed in 47.59 s. |
| Browser client replay | All 15 groups passed; these do not establish native UI acceptance. |
| Static checks | Ruff across core/tests/packaging/scripts and `git diff --check` passed before documentation cleanup; doc diff/link checks repeated afterward. |
| Bundle inspection | Passed: 91 native binaries, no external library dependencies, no recordings/model weights; ad-hoc signed, minimum OS 27.2. |
| Native UI inspection | App connected and showed two saved conversations, Gemini revision-3 notes/source buttons and matching sidebar/detail title. Preferences showed saved Gemini configuration and default summary instructions. Closed without saving. No new provider/capture request. |
| Five-minute native transport/resampler | Generated in memory, no audio devices opened or sound played. 4,800,000 samples per track, 150 ms initial offset, 0.4375 ms offset change; passed. This is not physical alignment or a real-time test. |
| Metadata diagnostic | Ten seconds completed with discovery available and no candidates. Separate sandbox snapshot returned zero process objects; this cannot establish host visibility or absence of a call. |
| Device enumeration | Sandboxed helper reported zero microphones and denied permission. Native app separately displayed microphone access needed. Permission was not requested; these observations do not establish the global OS device state. |

Generated aggregate artifacts: `data/phase1/silent-acceptance-20261028/` (local,
gitignored). No recordings or private transcript excerpts were created for these
checks. Speaker output was not enabled, no playback was triggered, and no output
device/volume/mute settings were changed. The user chose silent checks and docs
only; no real-call acceptance was attempted.

## Remaining acceptance work

- [ ] Implement the [commercial model](PRICING_AND_MODEL_ACCESS.md): $1 one-time
  purchase, no recurring ScribeLoop usage fee, customer-configured ASR/LLM API
  keys and fully self-hosted ASR/LLM options. These are release requirements,
  not all implemented capabilities of the current bundle.
- [ ] Real Teams/Slack/browser calls: start/mute/receive-only behavior, notification false positives, one prompt per episode, dismissal/repeated calls, call-end grace and manual fallback. Slack is absent from this host's detected installations; installed app identity alone is not compatibility evidence.
- [ ] Consented physical capture: intended source scope, microphone/output separation and alignment, device removal/change, interruptions, sleep/wake, network faults and multi-hour use. Do not enable speaker output for this work under the user's current constraint.
- [ ] Representative WER/speaker accuracy, streaming latency and human-reviewed summary quality/cost. Public AMI evidence is limited, not target sign-off. Gemini has successful real notes evidence; OpenAI live summary acceptance remains unresolved after the recorded HTTP 429, with no current retry.
- [ ] Native VoiceOver and complete keyboard-only acceptance; physical summary-instruction edit/save/clear and fresh generated-title refresh. Existing matching titles do not prove live refresh. Real permanent-deletion/Keychain prompt behavior has not been exercised on user records.
- [ ] Stable Developer ID signing, hardened runtime, notarization/stapling and installer; clean-Mac install, login approval, signed upgrades, TCC/Keychain continuity, cold-start/resource measurements and tested minimum OS. The macOS 13 build/dependency target does not establish physical Ventura acceptance.
- [x] Remove extractable shared transcription credentials: internal builds now use
  customer-owned Deepgram keys, with direct provider billing. No shared AI service
  or ScribeLoop-operated billing backend is included.

## Future scope

Automatic voice recognition needs a real embedding provider and consented
repeat-speaker calibration; manual names do not enable it. Advanced echo handling,
Linux capture/desktop parity, user-facing meeting search/chat, MCP agent actions
and auto-update remain future work. Original local-model bake-offs and Tauri plans
are historical rather than prerequisites to the current cloud macOS app.

## Workspace audio-key update — October 28

New recording keys are wrapped by a purpose-specific key derived from the already
unlocked database key, avoiding individual recording Keychain entries. Legacy keys
import on first explicit read; each may still require one final OS authorization.
Audio and transcripts are not rewritten or uploaded by migration. Record deletion
removes legacy and wrapped keys before ciphertext; copies/backups and recoverable
deleted envelopes remain outside that local deletion guarantee. See
[workspace key lifecycle](../packaging/macos/DESKTOP.md#one-workspace-unlock).

Validation: the full suite passed 495 tests before the final shutdown guard and
SQLCipher multi-record restart regression; 20 focused workspace/storage tests
passed on the final code, along with Ruff/diff checks. Production rebuild and
frozen-module inspection passed. No recording playback or audio output was used.
Bundle verification passed (91 native binaries, no external dependencies).
Reopened the rebuilt production app; both existing conversations and saved Gemini
notes loaded. Existing-recording migration was verified with isolated encrypted
fixtures; no user recording was unlocked or played by this verification.

## Playback waveform correction — October 28

[Quality investigation](MICROPHONE_PLAYBACK_QUALITY_2026-10-28.md) confirmed that
integer-ms packet placement introduced tiny gaps/overwrites in the latest saved
microphone recording. Sample-aligned reconstruction now applies to playback and
transcription assembly without rewriting saved audio. 500 Python tests, static
checks, production rebuild and bundle verification passed; native generated PCM
restores sample-for-sample. No playback or speaker output was used.

## Microphone controls — October 28

[Microphone settings](MICROPHONE_SETTINGS.md) now include software sensitivity,
Standard/Strong background filtering and adjustable noise rejection. New sessions
retain an encrypted original alongside processed mic audio; loopback is unchanged.
Latest-recording offline preview and UI inspection completed without audio output.
511 tests, production build/bundle audit and static checks passed. Physical
listening and slider save/cancel acceptance remain unclaimed.

## Real streaming API test — October 28

[Live API performance](STREAMING_LIVE_API_2026-10-28.md): one public 60-second
stream exposed a 20 ms closing word-end overrun; a bounded closing-only correction
was verified offline, then a fresh 20-second live stream completed. Local send
backlog p95 was 3.3 ms, first text 1.39 s after annotated speech, and completion
0.55 s after source end. A final fractional-ms sample-tail fix was validated offline.
529 tests and production rebuild/bundle checks passed. No private audio, capture,
playback or speaker output was used; two manual connections, 80 channel-seconds total.

## Shared 256 MB cache — October 28

[The process-memory cache](MEMORY_CACHE_256MB.md) now shares a 256 MiB budget
across authenticated audio blocks/indexes, transcript pages, rosters and validated
note/hash data. It fills on demand and evicts least-recently-used entries. Silent
generated seeks measured 1.6 ms warm versus 191.8 ms with caching disabled.
537 tests, all seven local streaming checks and production rebuild/bundle
verification passed. No new audio output or paid API request was used.
The rebuilt native app connected successfully after startup retries and showed
both saved conversations; the initial startup delay remains unexplained.
