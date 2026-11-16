# Ask chat experience

## Intended flow

Open Ask → choose meeting scope → send a question → read the cited answer →
continue with a follow-up, inspect a source, or start a new chat.

Use the notebook palette, a chronological message timeline, an anchored multiline
composer, starter questions and a separate saved-answer history panel. Keep sources
and processing details collapsed beneath each answer. Never make provider calls on
opening Ask, choosing a starter, changing scope or reopening a saved answer.

Scope changes start a fresh visible chat to prevent mixing evidence from different
meetings. Saved history restores an individual question/answer; this does not claim
to restore a durable multi-turn thread. Follow-ups pass the previous saved answer
ID to the existing guarded RAG flow; the backend uses that previous turn and newly
retrieved evidence, not unlimited conversation memory.

## Finish conditions

- Questions and answers remain visible in chronological order during the chat.
- Follow-ups work without a separate Follow up button.
- Send rejects blank, oversized or scope-less input; loading supports cancellation.
- Failed requests retain editable question text for explicit retry.
- New chat and scope changes clear follow-up context; in-flight requests cannot
  be accidentally attached to another visible conversation.
- Saved answers, stale-state warnings, source links, caveats and usage remain
  accessible without dominating the message timeline.
- Native compilation succeeds; permission and provider authorization stay in the
  existing core. UI-only checks need no paid API requests.

## Verification — 12 November 2026

- Native Swift typecheck passed. The existing macOS 13-compatible change handlers
  produce SDK deprecation warnings on newer SDKs.
- An isolated native preview with synthetic data and a fake dispatch adapter
  verified starter selection, sending two chronological turns, follow-up composer,
  expanded source text, New chat, scope switching and the history panel. It did
  not start the core, open the user's workspace or make provider requests.
- The preview exposed retained scroll position after New chat; the production
  view now explicitly scrolls to the top when the timeline is cleared.
- All 29 existing Ask/remediation regression tests passed. The RAG backend,
  provider contracts, authorization guards and schema are unchanged.
- The macOS app was rebuilt at `dist/macos/ScribeLoop.app`; bundle verification
  passed (40 native binaries, no external dependencies, no bundled recordings or
  operator transcription credential, macOS 13 target, internal ad-hoc signing).

The redesigned composer supports multiple lines, a 500-character question limit,
Send and Command-Return. While processing it disables scope changes and offers
Stop; failure restores the submitted text for editing. Cancellation/failure UI
behavior is implemented but was not exercised in the synthetic desktop walkthrough.
History continues to use backend source-freshness validation; changed sources
offer the saved question for explicit resubmission instead of an outdated follow-up.
