# Native built-app UI test — 2026-10-26

Tested the production `dist/macos/ScribeLoop.app`, using its normal controls and
real provider configuration. No alternate test bundle/source injection was used.
The user explicitly authorized real API calls and microphone use.

| Check | Observed result |
| --- | --- |
| Microphone permission | Requested in app Preferences; authorized before capture. Rebuilt ad-hoc helper identity later reports not_determined again; signed permission continuity remains open. |
| Real recording | Started and stopped with app controls. Approximately 33 seconds saved. Existing public AMI 20-second speech clip played in QuickTime as controlled microphone input. Room audio may also be captured; no transcript/audio is copied into this report. |
| Deepgram transcription | Five confirmed segments and two anonymous speakers returned. |
| Live update latency | Confirmed text was observed after Stop. This run does **not** establish satisfactory live-update latency. |
| Saved playback | Real saved recording loaded with a 33-second duration; seek to 18 seconds, play/pause and 1.5× speed worked in UI. |
| Person naming before repair | Selecting Person 2 and saving a test name was rejected twice. |
| Naming root cause | Isolated streaming-path reproduction: `commit_window` omitted `transcript_revisions`, so person-link FK insertion failed. Preview seeding used `append`, which inserted provenance. |
| Repair | Streaming writes now add revision provenance atomically. Schema 11 repairs missing revision metadata in existing streams without changing transcript/audio or rerunning providers. |
| Person naming after repair | Restarted same app/store, reopened same recording, selected Person 2 and saved “UI Test Speaker”; success visible in UI. |
| OpenAI summary | User saved key in app Preferences. Generate summary was invoked once on saved transcript. App displayed generic summary failure; no automatic retry. Cause not yet established, notes/title/citations not accepted as working. |
| Model editing/focus | AX-directed typing worked, but user reports physical typing targets the app behind. Clearer textbox and explicit user-window activation implemented. User tested physical keyboard input in the rebuilt app and confirmed “Works now”. |

Regression checks: 429 Python tests pass, including SQLCipher streamed-provenance,
existing-store repair, person linking, cited-summary persistence and rollback.
Native rebuild and dependency/signature audit pass. The summary failure and live
latency remain open follow-ups; synthetic smoke evidence does not override these
physical UI findings. The older empty recording was preserved.

## Adaptive-detector build: real processing retest (October 26–27)

Used the production app's normal microphone recording controls and the existing
20-second public English AMI clip in QuickTime. Initial capture failed because the
rebuilt app's microphone access was not authorized. Preferences subsequently
reported authorized; the next recording completed and retained 33 seconds of audio.

- Deepgram returned one confirmed segment while Recording was still visible at
  elapsed 0:29, before Stop; playback began around elapsed 0:08. Final saved result
  contained three segments and one anonymous speaker. This establishes actual
  streaming before Stop, but **does not** establish low latency or diarization
  accuracy. Physical speaker-to-microphone playback is not a clean reference signal.
- Audio tab exposes the full retained 0:33 recording, seek bar and speed controls.
- Automatic summary failed. Saved configuration was Gemini with model
  `gemini-3.8-flash-lite`. One tiny synthetic request through the same SummaryProvider
  reproduced HTTP 404. An authenticated model-list request succeeded (HTTP 200):
  the configured model is absent; available models include `gemini-3.5-flash-lite`
  and `gemini-3.8-flash`. No keys, raw provider bodies or captured transcript were
  written to this report. Awaiting model selection before changing saved settings.
- This test used manual microphone recording. It does not validate real Teams call
  detection or notification suppression; those still need physical acceptance.

## Gemini model correction and JSON compatibility — October 27

User chose `gemini-3.5-flash-lite`. Saved it through the production Preferences UI;
“Settings saved securely” was visible. A notes request still failed. One synthetic
request with the saved settings reached Gemini successfully but returned fenced
JSON, which failed the app's raw-JSON validator. Added support for a single entire
JSON/unlabeled code block, retaining schema, citation and size checks. Surrounding
prose, multiple blocks and other code languages remain rejected.

24 focused summary tests pass, including fenced-response API persistence/export
and malformed/cross-transcript citation rejection. Production app rebuilt and bundle
audit passed. The final real saved-session retry is pending: rebuilt core startup
waits for macOS SecurityAgent/Keychain approval, then times out. Computer control
cannot access SecurityAgent; the user must approve existing credential access
locally. No successful real summary is claimed yet.

After the user approved Keychain access and the core reconnected, the real summary
retry **passed** using `gemini-3.5-flash-lite`. Reopened the saved test session:
Gemini notes, revision 1, and Source links are visible. Sidebar title changed to
“Discussion on posture”. Clicking Source 1 opened the original transcript.
The detail heading still displayed the old placeholder during this observation;
sidebar title propagation is confirmed, full heading refresh is a remaining UI issue.
The test did not rerun transcription or upload audio again.

Keychain UX follow-up: ad-hoc rebuilt executables do not retain a stable code identity.
Startup reads provider credentials and the database key separately, while retained
sound availability checks also retrieve each audio key. This is not a password per
SQL query. Stable signing and avoiding unnecessary secret reads in status rendering
need follow-up; no Keychain protection was disabled during this test.

## Record switching and prerecorded reruns — October 27

Production app now performs one prerecorded Deepgram HTTP upload for Transcribe
again, rather than replaying a finished file over live WebSockets. Tracks are aligned
as WAV channels, with diarization and source attribution retained. PCM is generated
incrementally in memory, with no plaintext disk staging or realtime pacing. Full
ciphertext authentication and the durable cost reservation precede dispatch; failed
requests preserve the selected transcript and are not retried automatically.

Real UI/API verification on the retained 33-second test recording:

- Transcribe again showed a Processing indicator, disabled conflicting recording/
  archive/rerun actions, and kept Play enabled. The old three segments remained
  visible until a complete batch result replaced them.
- The explicit upload required local Keychain approval for the recording key. After
  approval it completed, producing one same-speaker segment in revision 3. This
  segmentation difference is not an accuracy score. The microphone reference was
  physical playback and is unsuitable for WER/DER measurement.
- Automatic Gemini notes succeeded for revision 3 with a source link; sidebar title
  became “Meeting Discussion”. No automatic second transcription pass was made.
- Switched to another completed record and back without an audio-key/password
  prompt. Opening Audio showed unloaded playback controls; pressing Play loaded
  and played the retained 0:33 recording. Paused playback before leaving the app.
- Actual simultaneous playback during the short API request was not exercised by
  hand; the Play button stayed enabled, and integration tests read both metadata
  and PCM while an immutable rerun task was active. Capture/recovery conflicts
  still reject reads.

Validation: 458 Python tests, 15 frontend replay groups, Ruff, diff checks, native
playback smoke (generated silence, first-click load/seek/pause/speed), production
build and bundle audit pass. Stable signing remains deferred; explicit decryption
can still prompt after development rebuilds. No Keychain ACL was weakened.

## Five-minute batch API/pipeline benchmark — October 27

User requested a five-minute recording test. Ran the production batch runner,
SQLCipher SessionStore, encrypted audio and saved Gemini provider configuration in
an isolated temporary workspace, using pinned public AMI ES2004a seconds 300–600.
Meeting channel contained the public recording; microphone channel contained
silence. No user's meeting database was read/modified or private audio uploaded.
This test did not run the five-minute clip through native UI or physical capture.

The first two single-dispatch attempts exposed a strict timestamp failure: Deepgram
reported duration 300.0 but the final word ended at 300.08502 (starting 299.92502).
Added a prerecorded-only 100 ms end tolerance and clamped output to supplied audio.
Outside starts and >100 ms end overruns remain rejected; live tolerance is unchanged.
Captured public response validates offline after repair. No automatic retries ran.

Fresh verified run after repair:

| Measure | Observed |
| --- | --- |
| Audio / upload | 300 s; 2-channel PCM16 WAV, 19,200,044 bytes |
| ASR dispatches | One prerecorded POST, no live socket |
| Batch pipeline | 58.55 s including authentication, upload, provider and persistence |
| Wait after upload-body generation plus commit | 2.36 s; remaining time mostly upload/body transmission |
| Gemini notes | 3.66 s; one summary request, citations validated |
| Combined ASR + notes | 62.22 s |
| Transcript | 44 segments; 4 anonymous speaker labels |
| WER | 26.08%; 199 edits / 763 reference words, established normalization |
| Peak benchmark process RSS | 100.3 MiB on macOS |
| Summary topic | Animal Drawings and Pricing Discussion |

Four returned speaker labels do not establish DER or correct speaker attribution.
The 26.08% WER is a public overlapping-meeting result, not a clean-speech acceptance
claim. Notes were inspected against their exported transcript, but citation validity
does not prove factual entailment. The successful run reserved a conservative $0.10
for 600 channel-seconds, not actual provider billing. Three ASR requests were made
across validation (two rejected results and the successful run), plus one summary.

Inspectable artifacts:
`data/phase1/deepgram-batch-300-20261027-verified/results.html` (filename/text/WER),
`meeting-notes.md` (cited notes and timestamped transcript), `results.json`,
`segments.json`, and `public-provider-response.json` in the same folder. Failed
reports remain in the two earlier attempt folders. Rebuilt production app includes
the boundary fix. 462 Python tests pass; bundle audit and Ruff/diff checks pass.

## Native visual refresh — October 27

Kept the sidebar and Notes/Transcript/Audio organization. Updated adaptive neutral/
charcoal-green surfaces, green accent, header metadata, conversation status badges,
people/source labels, notes overview card, source buttons, and audio controls.
Recording review no longer exposes the unnecessary provider endpoint URL. A vector
speech-loop/waveform icon is packaged for Finder/Dock and rendered in app branding.

Verification: production native compile, 17 focused desktop regressions, icon-aware
bundle audit, Ruff/diff checks, and palette contrast calculations pass. Actual dark
startup and Preferences screenshots show the icon, coherent surfaces and readable
controls. Provider settings were left intact; user was editing the secure-key field,
so remaining main-tab interaction checks were deferred rather than closing that
sheet. No new capture or paid provider requests. Light palette contrast was checked
numerically; no global macOS appearance setting was changed.

## Preferences simplification — October 27

Removed Encrypted workspace, separate Save settings button and key-removal switch.
Done saves changed settings and waits for an accepted receipt before closing;
failed/uncertain saves retain the draft. Unchanged Done avoids a Keychain write.
Saved-key UI uses first-four-plus-fixed-mask metadata, with fully masked short keys;
the actual secret stays in Keychain and the placeholder is never sent as a key.
Permissions now use microphone/meeting-audio rows with trailing actions and a call-
detection switch. Provider defaults, existing workspace and recording policy remain.

Native build and bundle audit pass; 19 desktop/fixture tests include short/long-key
masking and verifying edits never replace the actual key with its preview. Actual
AX state confirms the simplified form and saved-key indicator. User interaction
prevented an attempted Done click; current form was left intact. No paid requests,
permission grants, provider-value edits or workspace changes were made for this test.

## Conversation rename/delete — October 27

Sidebar context menus and Meeting menu now expose Rename conversation and Delete
conversation. Rename waits for a save receipt and manual-title provenance prevents
future generated notes from replacing that title. Renaming publishes a metadata-only
event, avoiding implicit automatic summaries. Delete reuses key-first deletion,
names the target in its confirmation and moves selection to an adjacent record.

477 Python tests, native build, Ruff/diff checks and bundle audit pass. Synthetic
API tests verify rename persistence across reconnect, idempotent requests and no
provider work; storage tests verify title precedence and invalid-input rejection.
Real UI inspection is pending Keychain approval after rebuilding (Retry core).
No existing user conversation was changed or deleted, and no paid calls were made.

Conversation-management UI follow-up: after local Keychain approval, right-clicking
an existing conversation exposed Rename/Delete. Rename opened with the original
name selected; Cancel preserved it. Delete confirmation named the correct record;
Cancel left all four conversations present. No saved title or audio was changed.

## Preferences outside-click focus — October 27

Replaced the model-only focus flag with shared optional endpoint/model/key focus.
Added a Preferences-window local mouse-down observer that resigns the active native
field editor on outside clicks, clears SwiftUI focus and returns the event unchanged
so buttons/pickers still act. No key text or global input is observed; the monitor
is removed when its view leaves the window or is dismantled. Keyboard activation
behavior remains in place. Native compilation and bundle audit pass. Updated only
the generated UI executable and re-signed the outer app, preserving the core bundle.
Actual focus checks are in progress; concurrent user navigation caused desktop
control to cancel stale actions. Existing values have not been edited for the test.

Outside-click focus verification completed after coordinated user pause: focused
Model ID, clicked explanatory text and observed focus move from the field to the
sheet; repeated successfully for the secure API-key field. A physical coordinate
click on the Provider picker cleared text focus and opened the menu; Escape closed
only that menu and focus stayed on the sheet. Done closed the unchanged form, with
all four conversations still present. No values were typed/saved, no credentials
were rewritten, and no provider/capture requests were made for these checks.
### 2026-10-27 — Card buttons and Preferences discard

Installed native UI only, preserving the frozen core. Inspected the running app:
each of four conversation cards shows pencil/trash actions. Physical pencil click
opened Rename with the card title; Cancel preserved it. Trash click opened the
permanent-delete confirmation naming that card; Cancel kept all four records.
Deleted the final Model ID character in a temporary Preferences draft, then verified
Cancel, Escape and a physical click outside the sheet discard it. Reopened settings
restored `gemini-3.5-flash-lite`. No API calls, saved settings edits or deletions.

### 2026-10-27 — Themed conversation selection

Replaced the native blue list selection with an adaptive muted-green card fill and
subtle green outline. The native table still owns selection and keyboard navigation;
only its system highlight drawing is suppressed within these rows. Installed the
native-only update, clicked the second card and pressed Up: the first record became
selected with the themed background, with no system blue. Native build, bundle audit
and diff checks pass. No saved record or settings were changed.

### 2026-10-27 — Deletion Keychain reads

Removed secret reads from macOS deletion existence/verification checks and added
retryable delete-if-present. Regression tests verify zero secret reads, denied
metadata queries, absent-key retry, failed removal verification and key-before-file
ordering. 480 tests and build/audit pass. Rebuilt and reopened the development app;
no actual user conversation was deleted, so no claim of real deletion prompt-free
acceptance is made. Keychain may still request authorization for a locked vault or
changed development binary identity.

### 2026-10-27 — Full app audit

See [full audit](MACOS_APP_AUDIT_2026-10-27.md) for real API measurements, findings,
fixes and remaining physical/release checks. Preview UI verified source navigation,
manual Person 1 naming, generated encrypted playback, archive feedback, cancellation
of detection/login drafts and confirmed processing revocation. Existing production
records/settings were preserved. The corrected preview rebuild was hash-checked
against production executable/core/configuration to verify isolation.

### 2026-10-27 — Audio key reuse

Rebuilt/reopened production with process-lifetime audio key caching. 485 automated
tests and bundle audit pass. Regression checks prove one acquisition across cold
metadata/seek/track/windows, none for newly created recordings, concurrent unlock
serialization and key eviction on delete. Actual OS first-unlock/persistent trust
is not claimed: an ad-hoc rebuilt core can still prompt on first existing-key access.
No user audio was deleted, decrypted to disk or uploaded during this change.

### 2026-10-27 — Business notes and summary instructions

Real batch/notes example generated from a fictional 4:16 multi-voice launch-planning
script. Inspected HTML in Brave: audio duration, decisions, actions, conditional
launch and budget figures visible. Known ASR owner-name error remains documented
in the linked review. One transcription and one Gemini summary request; no private
meeting content. 487 tests and native/bundle checks pass. Summary-instruction setting
roundtrip, Unicode limits, section/reduction propagation and changed-cache behavior
are covered by regression tests.

Installed production UI inspection confirms Preferences shows the multiline
Summary instructions editor, 0/2,000 counter and guidance beside the saved Gemini
configuration. A typing check was interrupted by user navigation closing the sheet;
no successful physical edit/save is claimed. Secure persistence/clearing and request
propagation are verified by the tests above. No draft was saved by the audit.

### 2026-10-27 — Default prompt displayed

Reopened installed production Preferences. Summary instructions contains the
525-character default editorial guidance rather than a blank editor. Saved Gemini
configuration remains intact; no settings save or provider request was made.
