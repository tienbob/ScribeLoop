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
capture independently of transcription. Transcribe authenticated encrypted audio in memory after Stop using local
MOSS float16 + SDPA, with a user-selected language hint. Show stream labels and timestamped transcript
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
ciphertext in place. The original preview used faster-whisper tiny (CPU int8) as a development
baseline. The Vietnamese quality correction below replaces it for actual meetings;
public-fixture replay retains its separate smoke backend.
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

## Vietnamese quality correction — 2026-10-22

The first real Vietnamese test exposed a smoke-backend limitation: Whisper tiny
CPU int8 with per-window automatic language detection produced poor text and
switched to Chinese. This result does not evaluate MOSS. Do not treat successful
recording/transport checks as transcription-quality acceptance.

Correction implemented: make the real-call path use the locally pinned MOSS model
in float16 + SDPA, with an explicit Vietnamese/English/automatic language choice.
Keep all decrypted audio in memory, preserve original encrypted recordings and
prior transcript revisions, and allow reprocessing after Stop. Check per-stream
sample/energy/clipping metadata before attributing all errors to ASR. Evaluate
against user-provided reference sentences; fluent text alone is not proof of accuracy.


The meeting path now uses an isolated local MOSS worker, float16 + SDPA, and
Vietnamese by default (English/automatic are explicit alternatives). Audio crosses
a bounded in-memory pipe; no decrypted WAV or transcript file is created. The
upstream timestamp/speaker-format prompt is retained with a language hint. There
is no automatic fallback to Whisper tiny or float32 if MOSS fails.

The saved Vietnamese call was reprocessed for comparison: both tracks contained
roughly 37.5 seconds of audio, with no clipped samples; loopback overall RMS was
lower than microphone RMS. This confirms available PCM, not routing fidelity or
speech intelligibility. The successful MOSS comparison took 12.44 seconds for
both tracks. Its text remained Vietnamese but did not exactly recover the user's
reference sentence, so Vietnamese quality is **not accepted** and no WER improvement
is claimed without a fuller ground-truth transcript.

The UI offers **Transcribe again with selected language** for saved authorized
meetings. It opens no capture source and preserves the previous transcript as a
prior revision. Only a successful complete result becomes current; failures retain
the old transcript. Reprocessing updates transcript excerpts, not a generated
summary. Model/runtime status is kept separate from transcript output.

Correction verification: **221 tests passed**, frontend regressions passed,
49 application modules passed import smoke, and changed Python files passed Ruff.
The authenticated UI successfully reprocessed the existing saved call and showed
the replacement Vietnamese transcript with capture off. Tests cover large pipe
input with a slow reader, cancellation cleanup, authenticated-container completion,
clock gaps/bounds, saved-call reprocessing without capture, prior-revision retention,
failure preservation, and unchanged recording end timestamps on reprocessing.

## Detect once, then lock — requested 2026-10-22

Automatic becomes the default. Use only the first few seconds of detected speech
from the opening 30 seconds of each track (up to six speech seconds per track),
not leading silence. Run local language identification once, lock the result for
both tracks and the entire MOSS pass, and persist/report that session choice.
A pinned local multilingual Whisper small model identifies language only; it
must not transcribe meetings. The tiny detector was rejected after it confused
Vietnamese with Chinese on the saved call.
MOSS remains float16 + SDPA. Manual Vietnamese/English overrides detection.
Require at least two voiced seconds, a top language probability of 0.70, and a
0.20 margin over the runner-up. These are initial conservative thresholds.
Insufficient/uncertain or unsupported language evidence must request a manual
choice rather than silently guessing. Auto reprocessing reuses an existing auto
lock; manually changing language is an explicit override. In the current
record-first implementation this step runs after Stop, before transcription;
live first-seconds detection during recording is not delivered by this change.


Install the language detector explicitly with
`.venv/bin/python scripts/download_language_model.py`. The pinned Systran small
revision is `536b0662742c02347bc0e980a01041f333bce120`; its required files include
`vocabulary.txt`. Recording never triggers a model download. The language-only
CPU int8 detector is released after selection; MOSS transcription stays float16.

Saved-call check: the detector selected Vietnamese with probability 0.7262 from
7.78 seconds of opening speech, in 1.83 seconds including encrypted input loading.
This model probability is not a calibrated accuracy score. No new capture or
upload occurred. Silence and uncertain/unsupported evidence require a manual
choice. Automatic mode currently supports Vietnamese and English only.

Detect-once validation: **228 tests passed**, frontend regressions and 50-module
import smoke passed, and changed Python files passed Ruff. The corrected detector
loaded successfully after installing its required vocabulary. Tests cover one
shared detection, silence/sample bounds, uncertain/unsupported results, fixed
worker language, reuse of a persisted auto lock, and UI language-lock reporting.
