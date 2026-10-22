# UX/UI review — 2026-10-22

Scope: the running desktop ScribeLoop notebook and its HTML/CSS/JS. Reviewed
against the current Vercel Web Interface Guidelines. No capture, permission,
transcription, or session data was changed during this review. Mobile findings
come from breakpoint/code inspection; a real mobile viewport was not verified.
This report records findings; product UI fixes are still pending.

## P1 — Recording control and scope clarity

1. **Stop is separated from Record and can be off-screen.**
   `core/scribeloop_core/api/static/index.html:26`: Stop follows the entire meeting
   setup and replay form. The large hero/setup already occupies the desktop
   viewport before transcript controls appear. Keep recording state, elapsed time,
   actual capture scope/mic, and Stop together in a persistent control bar.

2. **Source controls can describe a different scope from the active recording.**
   `core/scribeloop_core/api/static/app.js:90`: app/mic controls remain enabled
   while capture is active. Changing/confirming a preference does not change the
   running native source, yet the UI can show the new selection. Freeze those
   controls during capture and show immutable active-session scope separately.

3. **Discovery failure can falsely imply recording stopped.**
   `core/scribeloop_core/api/static/app.js:96`: unavailable discovery says
   “no audio is being captured,” although the observer is independent of the
   recorder. Describe discovery availability only; derive recording claims from
   the recording lifecycle. Microphone permission alone must not imply capture.

4. **Whole-system scope has browser-only explanatory copy.**
   `core/scribeloop_core/api/static/index.html:22`: the warning always refers to
   “the selected browser,” even when All system audio is selected. Show the
   confirmed scope: all apps for system scope; all tabs for browser scope; the
   named app for application scope. Repeat it beside Record and while recording.

## P2 — Workflow, feedback, and accessibility

5. **Meeting and replay setup are interleaved.**
   `core/scribeloop_core/api/static/index.html:16`: Record precedes language and
   consent. `index.html:24`: the shared session title lives inside the replay
   form, while the sidebar says New replay. Separate a primary meeting setup
   from a collapsed development-replay section. Order meeting controls as title,
   source/mic, language, consent, Record. Keep the notebook visual direction.

6. **Disabled Record gives no immediate next step.**
   `core/scribeloop_core/api/static/app.js:75`: multiple conditions disable it,
   with a generic description rather than the actual blocking reason. Show the
   next action beside the button: connect, select/confirm source, grant mic
   access, confirm consent, or wait for processing. Do not rely on dimming alone.

7. **Detection and transcription feedback are below the transcript.**
   `core/scribeloop_core/api/static/index.html:28`: language lock and reprocess
   action follow every transcript row. Move language status and reprocess controls
   into the session header, and label processing stages plainly. Distinguish
   recorded audio, transcription in progress, completed transcript, and failure.
   Do not present a guessed completion percentage.

8. **History loses keyboard focus; transcript updates replace old content.**
   `core/scribeloop_core/api/static/app.js:125`: every render removes all history
   buttons, including the button just selected. `app.js:161`: the entire polite
   transcript live region is rebuilt on unrelated events. Preserve keyed nodes
   and focus, update changed content only, and announce concise new results.
   Avoid re-announcing the complete existing transcript/notes.

9. **Small text contrast and narrow-layout status need attention.**
   `core/scribeloop_core/api/static/app.css:1`: muted #747b70 on #f5f3ec measures
   **3.93:1**, below 4.5:1 for normal text; many labels/help text are 8–11 px.
   The <=900 px rule hides #session-status. Darken secondary text, increase
   operational labels/help text, and keep status visible at narrow widths.
   Retain the existing visible focus ring and reduced-motion support.

10. **The elapsed timer is inside an announcing status region.**
    `core/scribeloop_core/api/static/index.html:13` and `app.js:79`: changing the
    role=status text every second may repeatedly announce the elapsed time.
    Separate a non-live timer from a status that announces state transitions.

11. **Notes repeat the transcript and history entries are hard to distinguish.**
    `core/scribeloop_core/api/static/index.html:30`: transcript excerpts appear
    directly after the same transcript. `app.js:131`: history metadata shows only
    date/status, so default-titled calls on the same date look identical. Label
    excerpts explicitly and place them in a separate view/collapsible section;
    add time/source/duration to history. Synthesized meeting notes remain a
    separate feature, not a UI label for duplicated transcript text.

12. **Recovery and navigation have accessibility gaps.**
    `core/scribeloop_core/api/static/index.html:6`: no skip link/main target.
    `app.js:242`: disconnection recovery only tells the user to reload/re-enter a
    key; no reconnect action is exposed. Add a visible reconnect path, keep
    saved content available, label the unlock dialog, and give form fields names
    and suitable autocomplete attributes. Never imply disconnection stops capture.

## Recommended implementation order

First make recording state/scope/Stop persistently visible and truthful (1–4).
Then consolidate the meeting setup and expose blocking reasons (5–7). Finish
with stable DOM/focus/live announcements, contrast/responsive checks, and
history/results organization (8–12).

Existing strengths: distinctive notebook typography/palette, native labeled
controls, literal text rendering, explicit consent, keyboard focus outlines,
reduced-motion handling, encrypted-storage status, and browser ambiguity labels.

Review criteria:
https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
