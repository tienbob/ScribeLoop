# Ollama flow — functional fixes

3 November 2026, desktop 0.1.17 (18). Performance benchmarking is outside this
delivery at the user's request. Speakers stayed off.

## Fixed

- Check Ollama uses the model typed in the open Preferences draft, independently
  of the saved transcription engine. Checking does not save settings.
- Immediate checking/loading feedback, a spinner and inline failures replace the
  invisible-error path (`message` was sent but only `status` was rendered).
- Default example is an actual audio-capable model, `gemma4:e2b`. Preparation
  validates model capabilities and rejects remote/cloud model metadata.
- Text-only API responses now create transcript segments instead of silently
  dropping recognized speech. Windows are bounded to 30 seconds; timing is coarse
  when the provider supplies no timestamps.
- The desktop always has an engine router. Saving an idle engine/model change
  reconfigures it immediately; no restart or accidental router property assignment.
- Preferences warns if notes still use a cloud provider and offers an explicit
  switch to Ollama notes. Local notes request structured JSON, disable thinking,
  retain citation validation and inspect the model before sending transcript text.
- App shutdown cancels standalone preparation checks.

## Functional evidence

Installed Ollama 0.40.9 and its already downloaded `gemma4:e2b` were used. No
model downloads, saved calls or cloud provider requests were needed. The server
was started on loopback with `OLLAMA_NO_CLOUD=1` for this validation.

A synthetic sentence was generated directly into a file, without audio playback.
The real API returned correct sentence text apart from “Linh” becoming “Lin.” It
returned no segment array, reproducing the discarded-transcript defect.

The production LocalRecordingRunner then consumed that synthetic audio through a
test capture source. It emitted no transcript before Stop, completed after Stop,
saved a nonempty transcript, and generated schema-valid cited notes through the
production RollingNotes and SummaryProvider. SQLCipher database and SLPA audio
encryption were verified. All diagnostic HTTP requests were constrained to
loopback; the diagnostic rejected non-loopback HTTP destinations. Temporary test
storage was removed normally. No personal workspace results were changed.

This establishes the local processing path with the installed models, not
physical microphone/device switching, a new live call, multilingual accuracy or
performance on every supported Mac. It was not a system-wide network disconnect.

Regression tests cover draft checks without persistence, invalid-model feedback,
engine switching in both directions without restart, text-only responses,
non-audio/cloud model rejection and structured local notes requests. The full
Python suite's only initial failures were three sandbox-blocked OAuth callback
binds; all account tests passed when rerun outside that restriction.

The canonical app was replaced in place and opened as 0.1.17 (18). Actual
Preferences displayed “Ollama is ready with gemma4:e2b” beside Check Ollama, plus
the cloud-notes warning and local-notes switch. All nine saved conversations
remained available. The bundle audit passed with zero external dependencies.
Screen Recording permission was reset after rebuilding per the standing update
preference; no recording permission was needed for these local processing checks.
