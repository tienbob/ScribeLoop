# Saved-transcript scrolling — investigation and fix plan

Date: 16 November 2026. Code reviewed: main `71b6f59`.
Branch: `investigation/transcript-scroll-performance`.
Status: **rendering trial implemented and rebuilt; user assessment pending**.

## Approved implementation trial

The user approved trying the changes and will assess the rebuilt app. Start with
an equatable transcript-content boundary and cached render blocks. For small saved
pages (≤40 segments and ≤20,000 UTF-8 bytes), use a regular stack to avoid lazy
height estimation while scrolling the reported 27-segment conversation. Retain
lazy bounded blocks for larger/live pages. This is a bounded rendering trial,
not a claim that the exact reported hitch is already fixed. Keep source IDs,
selection, copy, disclosure and citation behavior. Animate only jump controls.

## Reported case and what was actually verified

The user identified saved conversation “Thảo luận về tính năng ghi âm và hệ thống
AI”. The running app showed 27 segments and no active recording. I scrolled the
record up and down using native UI controls, inspected its variable-height bubbles
and took a 15-second stack-only sample of the running production app.

The stack sample showed the main thread mostly waiting for events, with brief
SwiftUI/AppKit layout and accessibility work. Physical footprint was 87.3 MB,
peak 101.3 MB. Accessibility work is partly caused by the inspection tool itself;
it is not evidence that accessibility causes the user's lag. No continuous CPU
stall, memory exhaustion or measured frame-rate result was established.

Ordinary wheel scrolling does not request another transcript page or call an ASR,
notes or embedding model. The named record fits entirely in its 100-segment page.
It therefore cannot be explained simply by loading thousands of transcript rows.
No transcript was uploaded, changed, re-transcribed or played during this work.

An Instruments Animation Hitches trace failed to finalize/export usefully and its
temporary data grew excessively. The diagnostic process was stopped and the
unusable trace removed; it contributes **no** FPS/hitch conclusion to this report.

## Confirmed rendering weaknesses

1. `DesktopClient.swift:27–50` groups adjacent same-speaker segments without a
   duration, segment-count or text-size bound. A page can become one enormous turn.
2. `DesktopApp.swift:469–481` virtualizes turns with a `LazyVStack`, but
   `Preferences.swift:842–880` renders every segment inside a turn with a regular
   `VStack`/`ForEach`. Laziness ends at the turn boundary. Text selection, wrapping,
   help, highlights and cleanup disclosure views all belong to that eager unit.
3. The page limit bounds segment count, not text/layout complexity. It cannot bound
   a page containing a few very long segments.
4. The transcript is inside `Notebook`, which observes the entire `DesktopClient`.
   Existing row parsing/grouping is cached, but unrelated published changes still
   invalidate the parent. A typed/equatable presentation boundary would make render
   invalidation explicit. The stress measurement below did not prove this is the
   primary cause in the named saved record.
5. `DesktopApp.swift:500` attaches the follow-state animation to the whole scroll
   container, rather than only the floating buttons. Narrowing its scope avoids
   accidental text/layout animation during follow/top-boundary transitions.

Already implemented protections must be retained: 100-row paging, cached grouping,
deduplicated follow-state notifications, stable source IDs, protected citation
navigation and no automatic top-scroll on ordinary refresh. Repeating these old
fixes would not address the remaining layout complexity.

## Native layout experiment

The harness compiled the actual production `TranscriptRow` and synthetic
same-speaker text into an offscreen `NSHostingView`. It compared the current
single turn with experimental render blocks of at most eight segments/4,000
characters. This is a layout experiment, not a shipped implementation or display
frame-rate measurement. Each initial timing includes a fixed 100 ms settling
period; do not interpret it as exact input-to-first-frame latency.

| 100 segments, characters per segment | Current initial layout | Bounded experiment | Current warm scroll/layout p95 | Bounded warm p95 |
| --- | ---: | ---: | ---: | ---: |
| 120 | 228 ms | 142 ms | 1.72 ms | 7.30 ms |
| 1,200 | 513 ms | 126 ms | 1.56 ms | 2.08 ms |
| 4,000 | 1,428 ms | 123 ms | 1.50 ms | 4.10 ms |

The experiment confirms large initial-layout cost in unbounded bubbles. It does
**not** reproduce the user's sustained saved-record scroll hitch: the warmed
current view was fast offscreen. Bounded rows reduce eager initial work but
introduce materialization spikes: a separate stress pass with published status
updates reached 19.64 ms maximum in the bounded variant. Do not ship a group-size
cap alone or claim it improves warm scrolling without a real display comparison.

Small raw results and harness source are retained locally under ignored
`data/validation/transcript-scroll-2026-11-16/`. The real-record stack sample stays
local there as well. No user transcript text is embedded in these synthetic results.

## Proposed fix, in order

### 1. Isolate saved-transcript rendering

Introduce a dedicated transcript presentation model/view, independent from the
full notebook's provider/discovery/account state. Use immutable typed row values
and explicit equality for text, speaker, timestamp, cleanup and citation state.
Rebuild them only when the displayed page's content/presentation changes; an
identical page response must not rebuild text rows. Precompute speaker-menu choices
once per page instead of scanning page rows inside each row's menu.

Keep saved transcript scrolling local. Do not throttle user input, disable text
selection, remove accessibility or reduce text quality to hide the lag.

### 2. Bound layout units without changing speaker/source semantics

Separate logical speaker turns from render blocks. Long turns can continue across
bounded blocks with restrained continuation styling, unchanged alignment and the
same speaker identity. Oversized individual segments need Unicode-safe render
fragments too; never truncate or rewrite stored transcript text.

Keep one canonical citation anchor per source segment and stable fragment IDs.
Highlight the complete cited passage, retain its timestamp and preserve complete
copy/export text. Keep original-ASR disclosure content deferred until expanded.

Prototype a native virtualized row container (`List` with suitable styling or
`NSTableView` reuse) against the current stack. Use cached row heights keyed by
content, width, font and disclosure state where appropriate. Choose based on
on-screen results for the named record; do not rewrite the container merely
because a different framework sounds faster. If capped lazy blocks still hitch,
prefer actual reusable rows rather than increasing pre-rendered work indefinitely.

### 3. Preserve viewport and simplify geometry effects

Save the top visible source/fragment and its pixel offset while reading. Restore
that anchor after a genuine content, width or disclosure change. Keep existing
Earlier/Later, Jump to top/latest and citation two-step navigation behavior.
Animate the floating controls only; manual scrolling and transcript layout should
not inherit follow-state animations. Following live remains a separate mode.

## Finish conditions before calling it fixed

- Compare the exact named saved record before/after with the same window/font,
  repeated upward/downward traversal and no provider requests.
- Capture valid on-screen frame/hitch evidence. Target the display's frame budget
  (16.7 ms at 60 Hz), report p95/max and >50 ms stalls; offscreen timing alone is
  insufficient. Investigate display/render-server behavior if the main thread
  remains idle during observed stutter.
- Also test 27 alternating-speaker segments, 100 same-speaker segments with long
  text, one oversized segment, cleanup expansion and page boundaries in a large
  conversation. Measure first-open latency separately from warmed scrolling.
- Preserve text selection/copy, Vietnamese combining characters, speaker naming,
  citations/highlighting, resize behavior, scroll position and keyboard access.
- Confirm discovery/status updates and identical page refreshes do not rebuild
  unchanged transcript rows; no network work occurs from wheel scrolling.
- Retain existing scroll/follow/citation regressions, compile/rebuild the app and
  run the native UI test against the user's record before acceptance.

The exact sustained hitch is not yet conclusively attributed. The confirmed
unbounded-layout weakness and explicit render boundary are the first fix targets;
the before/after display trace decides whether they resolve this particular case.

## Trial implementation

Branch `fix/transcript-scroll-rendering` adds an equatable `TranscriptContent`
boundary with explicit page version, source/revision, citation, partial-text,
busy and live-state inputs. The client only rebuilds logical/render turns when
visible transcript fields or capture scope change; identical pages and notes-only
updates retain their render identity. Speaker menu choices are cached per page.

Small saved pages use a regular stack to avoid lazy estimated-height shifts. Larger
and live pages use lazy render blocks, each bounded to eight segments or about
4,000 UTF-8 bytes across multiple segments. A single oversized segment is retained
whole; fragmenting individual sources remains follow-up work. No stored text or
speaker identity changes, truncation, text-selection removal or provider work was
introduced. Citations locate their containing render block before the original
segment anchor. Whole-scroll-container follow animations were removed.

Local native TranscriptBubbleTests pass for render identity reuse, actual edit
invalidation, bounded multi-segment blocks, source/order preservation and speaker
alignment. Existing DesktopAuditTests pass for paging, citation navigation, follow
mode and reading-position transitions. The production macOS app rebuilt successfully;
the initial on-screen trial verified scrolling and an existing architecture citation
opening its 3:12 source. The user's smoothness assessment is still required.
The rendering regression suite is added to macOS CI. This is not a claimed FPS
improvement or a completed native reusable-table migration.

### Quick-scroll visibility finding

During the initial trial the user observed that the hitch coincides with the two
quick-scroll controls changing visibility. Previously these controls were inserted
and removed from the overlay at follow/top thresholds. The trial now keeps both
controls mounted with stable geometry and toggles opacity, hit testing and
accessibility visibility, without animation. This removes that hierarchy change
during a scroll gesture. The equatable transcript boundary also isolates text rows
from follow-state updates. The revised build still needs the user's scrolling check;
no measured frame-rate improvement is claimed.

The up control is independent of bottom-follow mode: it remains available at the
bottom and hides only near the top of the first page. Both controls retain stable
mounted geometry.

### Follow-up: isolate navigation publication

The user reported that the stable-control build still hitches. Visibility changes
still mutated `DesktopClient`'s published follow state, notifying the notebook even
with an equatable transcript child. Follow state now lives in a separate observable
`TranscriptNavigationState`. Only the position label, quick-scroll controls and
scroll-tracking bridge observe it; scrolling thresholds no longer publish notebook
changes. Core paging/follow semantics retain their existing interface. A native
regression asserts threshold changes notify navigation observers but do not notify
notebook observers. This addresses a concrete invalidation path; subjective scrolling
acceptance remains pending and the previous attempt did not resolve the reported lag.
