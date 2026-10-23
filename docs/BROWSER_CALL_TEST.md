# Browser meeting test — current Deepgram path

Updated 2026-10-23. This test remains deferred until the user is ready for a real
meeting. The app does not automatically start a recording or upload saved audio.
See [launch instructions and acceptance matrix](PHASE_1_CLOSEOUT.md).

## Microphone-only recording

For a voice note or in-room conversation, select **Microphone only · no app audio**
under Recording source, choose the microphone, then Confirm source. No meeting
app needs to be running. This scope uses microphone permission only; it does not
open app/system audio capture or require Screen Recording permission.

Choose **Cloud · Deepgram Nova-3 — live**, confirm permission to record and the
separate microphone upload consent, then **Record microphone**. Text and speaker
labels appear while recording; Stop finalizes the encrypted audio. For
**Local · MOSS Q6_K — after recording**, no upload consent is needed and the
transcript appears after Stop. The five-minute development limit still applies.
The microphone records all voices it can hear, including loudspeaker playback;
this is not acoustic isolation. Saved recovery, Resume and Transcribe again work
with this scope, preserving the selected model's behavior.

## Teams in Brave, with app audio and microphone

1. Start the encrypted server and open `http://127.0.0.1:8757/`; the demo requires no launch key. No public WAV is
   required for the cloud app. If the database is in use, stop the older server
   before restarting; do not delete lock files or start a second owner.
2. Start a consented short Teams call in Brave. Choose/confirm Brave and the
   intended microphone in ScribeLoop. Browser scope includes every Brave tab;
   use headphones and close unrelated audio tabs.
3. Select **Cloud · Deepgram Nova-3 — live**. Keep Auto for its ten supported languages, or choose a fixed language. Select
   Tiếng Việt for Vietnamese. Auto follows supported language changes; it does
   not apply hints or lock the first language.
4. Confirm participant permission and the separate Deepgram upload consent,
   then Record. Discovery only suggests a source; Record is explicit.
5. Expect provisional text while speaking, followed by confirmed timestamped
   rows with anonymous speaker badges, source labels and reported language.
   Speaker labels are not verified names. Overlap can cause recognition and
   identity errors; fresh connections do not establish prior person identity.
6. Stop finalizes encrypted capture while remaining text completes. This test
   stops automatically after five minutes. Provider errors preserve healthy
   encrypted capture and never trigger another provider or a second pass.
7. Inspect audio routing, known reference sentences, speaker changes and latency.
   Transcript excerpts are available; generated summaries are later-phase work.

Saved recovery authenticates incomplete audio into fresh encrypted containers
without capture or upload. Resume/Reprocess requires separate saved-audio cloud
consent. Resume continues checkpoints; Reprocess retains the previous transcript
until the replacement succeeds. Permanent deletion and retention cleanup require
their explicit controls. See [storage](SECURE_STORAGE.md).

Real Teams/Slack automatic detection reliability, physical alignment, current
cloud call quality, Linux and packaged desktop distribution remain unverified or
undelivered as listed in the closeout. Earlier MOSS/Groq and permission-debugging
notes are historical and remain available in Git history and evaluation docs.
