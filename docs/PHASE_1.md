# Phase 1 — development closed

Updated 2026-10-23. The macOS development scope is finalized: confirmed native
sources feed encrypted capture, Deepgram streaming and anonymous speaker labels,
with Auto/fixed languages, history/notes, recovery and explicit saved actions.
Each meeting uses one provider with no automatic alternative or second pass.
The explicit [Local MOSS Q6_K option](LOCAL_MOSS.md) added afterward runs English/
Chinese transcription and speaker labels after Stop. The UI labels processing
location and timing; Deepgram remains the live default.
Microphone-only recording and the keyless local demo are delivered. Local live
windowing is a benchmark, not a shipped mode. The next increment is the
[Phase 2 revision/summary/export plan](PHASE_2_IMPLEMENTATION_PLAN.md).

The [authoritative closeout](PHASE_1_CLOSEOUT.md) contains verified results,
current launch instructions, the support matrix and later real-call checklist.
The [implementation matrix](PHASE_1_IMPLEMENTATION_PLAN.md) maps M1–M7 to delivered
code and outstanding acceptance. [Streaming details](DEEPGRAM_STREAMING.md) explain
speaker/language behavior and cloud limits.

**Full MVP/release acceptance remains open.** Real-meeting testing and signing
were deferred by the user. Required speaker quality, representative WER/latency,
physical timing and real app coverage have not passed. Linux is not delivered;
packaged desktop work remains deferred. These are not marked done by closing the
local development phase.
