# Brave Teams call test — development test path

Requested 2026-10-22: launch ScribeLoop, manually start a short Teams call in
Brave, explicitly start/stop capture, and obtain local conversation notes.

The encrypted recording path is implemented; permission and physical routing
still need validation on this host.
Automatic discovery is a suggestion only and never starts recording. Browser
scope includes all Brave tabs; close unrelated audio tabs and use headphones to
reduce duplicate speaker audio in the microphone track. Confirm authorization
from participants in the Record consent control.

The first development test is limited to five minutes. Record both native
streams directly into separate SLPA encrypted files, with metadata/transcript
stored only in SQLCipher. Prepare keys/writers before opening sources. A bounded
recorder queue must stop visibly on overflow or storage failure. Stop drains
capture independently of transcription. Transcribe authenticated encrypted audio
in memory after Stop using the cached local development ASR baseline; do not
change MOSS float16 defaults. Show stream labels and timestamped transcript
excerpts as notes, without claiming speaker identities or inferred decisions.
No cloud transcription or summary requests are authorized by this test.

Required checks: no capture before explicit consent, denied permission, exclusive
replay/recording slot, prepare/start/stop failures, queue overflow, encrypted
stream completion, separate transcript stream identities, frontend control
states, and native generated-signal regression. Physical Brave routing and
microphone clock behavior remain unverified until the user's actual test.


Implemented: authenticated Record/Stop, explicit consent, source revalidation,
exclusive capture/replay slot, bounded native-reader/recorder queue, separate
SLPA files, SQLCipher metadata/transcript/notes, and startup interruption recovery.
Capture closes before local inference starts. A failed model leaves audio
ciphertext in place. The preview uses cached faster-whisper tiny (CPU int8),
a separate development baseline; MOSS float16 + SDPA defaults are unchanged.
These notes are transcript excerpts, not a synthesized summary.

Verification: generated lifecycle tests cover denied consent, key/writer/source
failure, missing stream, queue overflow, simultaneous start rejection, Stop with
slow inference, stream identities, and restart state. Authenticated API tests
cover plaintext-mode rejection and encrypted start/stop/notes/reconnect. A real
cached-ASR smoke check decrypted only the existing public sample in memory and
produced transcript rows for both streams; temporary encrypted fixtures were
removed. No microphone or live meeting was captured during these checks.

Run the encrypted server normally. In the UI select Brave and a microphone,
confirm the source, and grant microphone access if needed. If permission is
already denied, enable the responsible launching app under macOS System Settings
→ Privacy & Security → Microphone; Screen Recording access is also needed for
browser output. Start the Teams call, confirm participant authorization, check
the recording consent box, and press Record meeting. Stop after a short exchange;
local transcription and timestamped transcript notes appear after Stop. Do not
assume capture worked until the UI shows RECORDING. Source errors stop visibly.

This delivers a limited M4 test path, not all M4/M5/M6 acceptance gates. Remaining:
real-device routing/clock validation, durable interrupted-prefix recovery and
catch-up, broader disk/blocked-source fault injection, calibrated drift handling,
production ASR/diarization, and synthesized summaries. Signing remains deferred.

Host update: microphone authorization is now verified **authorized** after enabling
Visual Studio Code access. The encrypted preview was restarted and Brave +
system-default microphone selection was confirmed. Recording remains off until
the user checks consent and presses Record. Validation: **213 tests passed**,
frontend regressions passed, import smoke passed (47 modules), and changed-file
Ruff passed. No live-call routing success is claimed yet.
