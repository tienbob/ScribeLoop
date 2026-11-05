# Follow-live transcript scrolling

Implemented 3 November 2026 for desktop 0.1.16 (17).

The transcript follows the bottom while speech arrives, including growing partial
text. Scrolling away pauses following and shows “Reading earlier.” New speech
shows “New speech · Latest”; Latest resumes following and targets a stable anchor
after partial text. Completed recordings show “At latest.”

Earlier/Later and citations are explicit navigation. Refreshes keep the selected
page while reading earlier. An automatic response cannot replace a pending manual
navigation or jump to another page after the user has scrolled up. Selecting a
meeting or replacing its transcript resets navigation to Latest.

The macOS 13-compatible observer tracks user scroll events on the existing
NSScrollView. Layout/programmatic movement does not disable following. Bottom
updates are coalesced without per-token animation; the existing 100-segment page
limit stays in place. Reading position is preserved by retaining the current page
and stable row identities rather than issuing a scroll on every refresh.

## Validation

- DesktopAuditTests: follow/read/unread transitions; partial updates; preserved
  reading page; 100/101 and 200/201 boundaries; late response after scrolling up;
  pending citation navigation protected from automatic refresh.
- TranscriptScrollTests: offscreen AppKit scroll views in flipped and unflipped
  coordinates; appended content follows; paused position stays fixed; Latest
  resumes; observers detach cleanly.
- Production SwiftUI typecheck and desktop packaging regression checks passed.
- Rebuilt the canonical app in place as 0.1.16 (17). In its real SwiftUI transcript,
  scrolling the saved Weather Discussion upward changed “At latest” to “Reading
  earlier”; Latest restored “At latest” and the final line. All nine saved
  conversations remained available.
- Bundle audit passed: 38 native binaries, zero external dependencies, no bundled
  recordings or operator transcription credentials. Screen Recording permission
  was reset after rebuilding, per the standing update preference.

These tests do not establish live-call visual smoothness or macOS 13 runtime
acceptance. No recording, audio playback or provider requests were used.

Account-service hosting remains deferred. This change does not deploy login,
activate billing, or introduce paid access restrictions.

## Jump buttons in the transcript box (4 November 2026)

The toolbar “Latest” button moved into the transcript box as a floating
chat-style pair (`TranscriptJumpButton` in DesktopApp.swift), shown only while
reading away from the live bottom. ↑ loads the first page and shows its top
row; ↓ resumes following at the latest speech and carries a dot badge while
unread speech is pending. Both buttons fade out when following resumes.
Earlier/Later pagination, the position counter and the “Following live /
Reading earlier” caption stay in the toolbar. Buttons reuse the existing
loadPage paths (offset 0 and -1), so citations, pending-navigation race
guards and refresh offsets are unchanged. Panel-colored circular buttons with
the theme border match the existing card styling; bottom-trailing padding
keeps them clear of the vertical scrollbar.

Adaptive visibility: ↓ hides when the scroll is at the latest speech (follow
resumed); ↑ hides when the scroll is at the top of the beginning — page zero
with the view near the top. `TranscriptScrollTracking` now reports
near-top alongside near-bottom on the same user-scroll path (56 pt threshold,
programmatic movement still never changes state), and `loadPage(0)` sets the
near-top state optimistically so the button disappears the moment it is used.

