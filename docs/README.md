# Documentation index

Start with the [current status and acceptance checklist](CURRENT_STATUS.md) and
[native macOS app instructions](../packaging/macos/DESKTOP.md).
See [Phase 2b implementation and acceptance](PHASE_2B_CLOSEOUT.md), [Phase 2a closeout](PHASE_2A_CLOSEOUT.md), [Phase 2 progress](PHASE_2_IMPLEMENTATION_PLAN.md) for core delivery history,
[summary setup and export](MEETING_SUMMARIES.md) for text-provider configuration,
and [Phase 1 closeout](PHASE_1_CLOSEOUT.md) for launch commands and remaining
capture/release acceptance gates.

## Current development

- [Gemini summary HTTP 400 regression fix — October 30](GEMINI_SUMMARY_400_FIX_2026-10-30.md)

- [Recognition controls and six-way accuracy comparison — October 30](TRANSCRIPTION_ACCURACY_CONTROLS_2026-10-30.md)

- [Transcription and summary quality test — October 30](QUALITY_TEST_2026-10-30.md)

- [Code audit fixes and re-audit — October 30](CODE_AUDIT_FIXES_2026-10-30.md)

- [Transcription languages and hints](EN_VI_TRANSCRIPTION.md)
- [Latest-call transcription quality review — October 29](TRANSCRIPTION_QUALITY_REVIEW_2026-10-29.md)
- [Call stop and automatic notes repair — October 29](CALL_WORKFLOW_FIX_2026-10-29.md)

- [Failed-call retry and capture authorization — October 29](FAILED_CALL_RETRY_2026-10-29.md)

- [Recording Stop and call-end status fix — October 29](RECORDING_STOP_FIX_2026-10-29.md)

- [Internal Mac distribution and installation](INTERNAL_MAC_DISTRIBUTION.md)

- [Pricing: $1 app, self-hosted models or customer API keys](PRICING_AND_MODEL_ACCESS.md)

- [Shared 256 MB memory cache](MEMORY_CACHE_256MB.md)

- [Real streaming API performance — October 28](STREAMING_LIVE_API_2026-10-28.md)

- [Streaming fixes and before/after re-audit — October 28](STREAMING_PERFORMANCE_CLOSEOUT_2026-10-28.md)

- [Streaming performance audit — October 28](STREAMING_PERFORMANCE_AUDIT_2026-10-28.md)
- [Microphone sensitivity and noise filtering](MICROPHONE_SETTINGS.md)

- [Current status, silent checks and remaining acceptance](CURRENT_STATUS.md)
- [Desktop audit and real API evidence — October 27](MACOS_APP_AUDIT_2026-10-27.md)
- [macOS desktop app — audited implementation plan](MACOS_APP_IMPLEMENTATION_PLAN.md)
- [macOS desktop plan audit and resolved gaps](MACOS_APP_PLAN_AUDIT.md)

- [Implementation plan](PHASE_1_IMPLEMENTATION_PLAN.md)
- [Phase 2 progress and implementation order](PHASE_2_IMPLEMENTATION_PLAN.md)
- [Phase 2b implementation and acceptance](PHASE_2B_CLOSEOUT.md)
- [People, voice learning and calibration](PEOPLE_AND_CALIBRATION.md)
- [Phase 2b: long meetings, speakers and summaries](PHASE_2B_IMPLEMENTATION_PLAN.md)
- [Grounded summaries and Markdown export](MEETING_SUMMARIES.md)
- [Automatic call notebook UX](AUTO_CALL_UX.md)
- [Deepgram streaming and speaker labels](DEEPGRAM_STREAMING.md)
- [Retained audio, archive, playback and manual reruns](RETAINED_AUDIO.md)
- [Browser call test](BROWSER_CALL_TEST.md)
- [Meeting capture requirements](MEETING_CAPTURE.md)
- [Native meeting capture](NATIVE_MEETING_CAPTURE.md)
- [Meeting detection](MEETING_DETECTION.md)
- [Secure storage](SECURE_STORAGE.md)
- [Phase 1 scope](PHASE_1.md)

## Historical evidence and reviews

These files preserve decisions and reproducible measurements. Their old provider
defaults, proposed work and recorded prices do not override the current closeout.

- [Cloud provider investigation](CLOUD_TRANSCRIPTION.md)
- [Initial ASR investigation](ASR_INVESTIGATION.md)
- [Historical local MOSS setup and experiments](LOCAL_MOSS.md)
- [MOSS evaluation](MOSS_EVALUATION.md)
- [Diarization review](DIARIZATION_REVIEW.md)
- [Codec evaluation](CODEC_EVALUATION.md)
- [Phase 0a](PHASE_0A.md), [Phase 0b](PHASE_0B.md), [Phase 0 closeout](PHASE_0_CLOSEOUT.md)
- [Phase 1 plan audit](PHASE_1_PLAN_AUDIT.md)
- [Native capture audit](NATIVE_CAPTURE_AUDIT.md)
- [UX/UI review](UX_UI_REVIEW.md)
- [M5/M6 implementation checkpoint](M5_M6.md)

The repository [architecture](../ARCHITECTURE.md) includes longer-term design
and deferred work. Optional evaluation scripts remain available for reproducing
historical results; the default app does not run local models or multiple providers.
