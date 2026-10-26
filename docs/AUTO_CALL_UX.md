# Call notebook UX review and revised flow

Reviewed 2026-10-26 using the web-interface-guidelines and frontend-design skills.
The product direction is an unobtrusive notebook: open it, answer a call permission
prompt, receive saved notes. This supersedes setup-first interaction as the default.

## Findings addressed

- `api/static/index.html`: default source/model/title forms and repeated consent
  checkboxes interrupted meeting entry. The first screen now shows monitoring;
  advanced configuration and manual recording are inside a collapsed disclosure.
- `api/static/app.js`: detected activity required choosing a source and separately
  starting capture. Fresh candidates now open one scope-aware permission prompt;
  decline/Escape dismisses the episode. Focus starts on decline, not recording.
- `meetings/service.py`: discovery had no recording lifecycle binding. An approved
  session now has a backend inactivity monitor; incomplete/old observations cannot
  claim a call ended. Countdown and Stop/Keep recording communicate the heuristic.
- `api/server.py`: successful sessions still required manual LLM generation.
  A displayed endpoint/model grant now authorizes one automatic attempt after
  completion; no grant means no upload. Browser disconnect does not own completion.
- `storage/sessions.py`: app/date placeholders obscured the content of history.
  Automatically created names now become cited topic titles after summary success,
  with original session time and source retained.

The recording/processing boundary remains visible. A browser source means **all
its tabs**, not a recognized meeting tab. The prompt discloses audio and text
processing destinations. Participant authorization remains an affirmative action,
with no pre-recording buffer or capture before approval. Browser permission prompts
and native macOS permissions are separate first-use requirements.

## Current flow

1. Launch the backend and open the notebook; the native observer watches metadata.
2. Activity suggests a call. The user chooses Record & take notes or Not now.
3. Approval creates a dated record and begins capture with the selected ASR path.
4. App inactivity starts a 60-second end countdown; active/unknown evidence resets
   it. Keep recording restarts the grace period; Stop ends capture immediately.
5. Successful transcription completion triggers the approved summary and topic name.
6. The user can review source citations, fix speakers, regenerate notes or export.

The five-minute development cap remains visible. Without summary configuration,
the prompt offers Record conversation and explains that notes are unavailable.
Native background notifications, accurate provider-specific call-state integration,
long-call support and physical acceptance remain open. Do not advertise seamless
call end detection across every Teams/Slack/browser configuration yet.
