# ScribeLoop

**Your meeting notebook for Mac.**

ScribeLoop records your microphone and meeting audio, transcribes the conversation as it happens, and turns the transcript into notes with links back to their sources. Keep recordings, transcripts, and notes together so you can revisit a discussion without starting from scratch.

The app is currently available for **internal testing on Apple Silicon Macs running macOS 13 or later**. Python and the runtime are included—there is no terminal setup or additional app to install.

## What you can do

- **Record your way.** Capture your microphone, a selected meeting app or browser plus your microphone, or system audio plus your microphone.
- **Follow a live transcript.** See text during the conversation, with anonymous speaker labels that you can name manually.
- **Use multiple languages.** Search and select expected conversation languages, including English and Vietnamese, to guide recognition.
- **Get notes with sources.** Generate an overview, key points, decisions, action items, open questions, and uncertainties where the transcript supports them. Follow citations back to the transcript.
- **See upcoming meetings.** Opt into macOS Calendar, choose calendars, open a meeting link, and explicitly link an event to a conversation. The existing recording popup still asks before capture starts. See [Calendar setup](docs/MACOS_CALENDAR.md).
- **Revisit and share.** Browse saved conversations, play retained audio, export Markdown, or prepare an email draft with a summary, transcript or audio attachment. Summary is selected by default; you review and send from your mail app. See [email sharing](docs/EMAIL_SHARING.md).
- **Control recognition settings.** Adjust microphone sensitivity and noise filtering, add vocabulary hints, and choose whether transcription uses original or processed microphone audio.

## Requirements

| Requirement | Details |
| --- | --- |
| Mac | Apple Silicon: M1 or newer |
| macOS | 13 Ventura or later; automatic call detection requires 14.2 or later |
| Transcription | Your own Deepgram API key and an internet connection, or a local Ollama server (optional) |
| Conversation notes | A supported cloud provider/model and your own API key, or an existing compatible local model server |
| Permissions | Microphone access; Screen Recording access for meeting-app or system audio; optional Calendar access for upcoming events |

Intel Macs and macOS 11–12 are not supported by the current installer. Python, Homebrew, Xcode, and Ollama are not prerequisites for using cloud transcription and notes.

## Transcription engines

- **Deepgram (cloud, default)** — live transcript while recording, word-level speaker labels, and an optional extra refinement pass. Audio is sent to Deepgram.
- **Ollama (local)** — transcription runs on your Mac **after the conversation has ended**: there is no live transcript while recording. ScribeLoop opens Ollama automatically, downloads the model name you type once if it is available in the Ollama library, and never uploads audio. Anonymous speaker labels are available as an opt-in add-on when the local diarization model is installed. See [local transcription](docs/LOCAL_TRANSCRIPTION.md).

## Install

1. Download the ScribeLoop DMG shared by your team.
2. Open it and drag **ScribeLoop.app** into **Applications**.
3. Eject the disk image, then open ScribeLoop from Applications.

The current internal build is ad-hoc signed and **not notarized**. If macOS blocks it, verify the download with your distributor and follow Apple's app-specific [Open Anyway instructions](https://support.apple.com/102445). Company-managed Macs may require IT approval. Do not disable Gatekeeper.

See the [full installation guide](packaging/macos/INTERNAL_INSTALL.txt) for setup, updates, and permission troubleshooting.

## Your first recording

1. Open **Preferences** using the gear icon.
2. In **Models**, enter your **Deepgram API key**. Choose a notes provider and model, then enter its API key. Use **Save changes** when finished.
3. In **Recording**, select the languages you expect to hear. English and Vietnamese can be selected together.
4. Grant microphone access and, when needed, macOS Screen Recording access. Use **Check capture access without recording** to verify permissions silently.
5. Review the processing agreement, choose **New recording**, select your audio source, and press **Record & take notes**.
6. Press **Stop recording** when finished. Transcription can continue processing after capture stops; notes follow when the transcript is ready.

On supported macOS versions, ScribeLoop can suggest recording when it detects a possible call. A suggestion waits for your approval before recording. Call-end detection may show a **60-second countdown**; choose **Keep recording** if the conversation is still in progress. Use Stop manually if the call has ended but the browser remains active.

On macOS 13–14.1, start and stop recordings manually.

## Make it fit your conversations

In Preferences, you can choose recording duration limits, adjust microphone gain and background-noise filtering, and customize the instructions used to generate notes. You can also choose the transcription engine: Deepgram cloud or local Ollama transcription that runs after the conversation ends.

Under **Recognition accuracy**, you can:

- Choose **processed** microphone audio or the **original** signal before ScribeLoop's gain and noise filter. Original-audio transcription requires a retained original track; playback keeps its existing audio settings.
- Add names and vocabulary **one phrase per line**. Enter the words as spoken—for example, `what's up`. Do not append language labels such as `-English` or separate terms with semicolons.
- Enable **Refine transcript after recording** for an additional Deepgram pass before notes. This adds processing time and provider charges. The live transcript revision is retained if refinement fails.

Language and vocabulary hints can help recognition, but they do not guarantee correct wording. Review important names, numbers, decisions, and action items against their sources.

## Your data and privacy

Recordings and meeting data are stored encrypted on your Mac. API keys are kept in the macOS Keychain. Each Mac has its own workspace; installing the app does not copy another person's recordings or credentials. Markdown exports are readable plaintext files outside that encrypted workspace.

When you record or rerun transcription, audio is sent to Deepgram. When you generate cloud notes, transcript text and speaker labels are sent to your configured notes provider. Provider charges apply to your account. Encryption on your Mac does not make cloud processing offline.

The default audio-retention period is **30 days**. Archive audio to keep it beyond that period. Transcripts and notes remain available after audio expires. See [audio retention and playback](docs/RETAINED_AUDIO.md) for details.

Opening the app does not start recording or play audio. Recording a selected browser can include sound from its other tabs—get permission from everyone whose audio you capture.

## Pricing

The planned **Local** price is **US$3 as a one-time purchase**, with no recurring Local-plan fee, when all transcription and notes models run on your device. The planned **Cloud** price is **US$5 per month**. Cloud usage allowances and whether provider costs are included or paid through your own API keys are still being defined.

The Google sign-in/account foundation is implemented, with live sign-in awaiting account-service deployment. Checkout and paid-feature enforcement remain planned. The current internal app supports Deepgram cloud transcription and the local Ollama path. See [account setup](docs/GOOGLE_ACCOUNT_IMPLEMENTATION.md) and [pricing and model access](docs/PRICING_AND_MODEL_ACCESS.md).

## Internal testing status

ScribeLoop is an internal preview. Transcription, speaker labels, and generated notes can contain errors. Call detection and automatic stopping depend on the meeting app and available macOS activity information; manual recording and Stop remain available.

The installer targets macOS 13+, but testing on additional Macs and macOS versions is still needed. Account sync and workspace transfer are not included. Ad-hoc app updates can require recording permissions to be refreshed.

To report a problem, include your app version, Mac model, macOS version, call app/audio source, exact error, and steps to reproduce. Do not include API keys, passwords, or private recordings in a bug report.

## More information

- [Installation and troubleshooting](packaging/macos/INTERNAL_INSTALL.txt)
- [Internal distribution guide](docs/INTERNAL_MAC_DISTRIBUTION.md)
- [Documentation and developer guides](docs/README.md)
- [Architecture](ARCHITECTURE.md)

License: Proprietary.
