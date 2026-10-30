# ScribeLoop

**Your meeting notebook for Mac.**

ScribeLoop records your microphone and meeting audio, transcribes the conversation as it happens, and turns the transcript into notes with links back to their sources. Keep recordings, transcripts, and notes together so you can revisit a discussion without starting from scratch.

The app is currently available for **internal testing on Apple Silicon Macs running macOS 13 or later**. Python and the runtime are included—there is no terminal setup or additional app to install.

An **Ubuntu 22.04+ preview** is under development on a separate Linux packaging path.
See [Ubuntu installation, build instructions and limitations](packaging/linux/README.md).

## What you can do

- **Record your way.** Capture your microphone, a selected meeting app or browser plus your microphone, or system audio plus your microphone.
- **Follow a live transcript.** See text during the conversation, with anonymous speaker labels that you can name manually.
- **Use multiple languages.** Search and select expected conversation languages, including English and Vietnamese, to guide recognition.
- **Get notes with sources.** Generate an overview, key points, decisions, action items, open questions, and uncertainties where the transcript supports them. Follow citations back to the transcript.
- **Revisit and share.** Browse saved conversations, play retained audio, and export notes and transcripts as Markdown.
- **Control recognition settings.** Adjust microphone sensitivity and noise filtering, add vocabulary hints, and choose whether transcription uses original or processed microphone audio.

## Requirements

| Requirement | Details |
| --- | --- |
| Mac | Apple Silicon: M1 or newer |
| macOS | 13 Ventura or later; automatic call detection requires 14.2 or later |
| Transcription | Your own Deepgram API key and an internet connection |
| Conversation notes | A supported cloud provider/model and your own API key, or an existing compatible local model server |
| Permissions | Microphone access; Screen Recording access for meeting-app or system audio |

Intel Macs and macOS 11–12 are not supported by the current installer. Python, Homebrew, Xcode, and Ollama are not prerequisites for using cloud transcription and notes.

## Install

1. Download the ScribeLoop DMG shared by your team.
2. Open it and drag **ScribeLoop.app** into **Applications**.
3. Eject the disk image, then open ScribeLoop from Applications.

The current internal build is ad-hoc signed and **not notarized**. If macOS blocks it, verify the download with your distributor and follow Apple's app-specific [Open Anyway instructions](https://support.apple.com/102445). Company-managed Macs may require IT approval. Do not disable Gatekeeper.

See the [full installation guide](packaging/macos/INTERNAL_INSTALL.txt) for setup, updates, and permission troubleshooting.

## Your first recording

1. Open **Preferences** using the gear icon.
2. Enter your **Deepgram API key**. Choose a notes provider and model, then enter its API key. Save with **Done**.
3. Select the languages you expect to hear. English and Vietnamese can be selected together.
4. Grant microphone access and, when needed, macOS Screen Recording access. Use **Check capture access without recording** to verify permissions silently.
5. Review the processing agreement, choose **New recording**, select your audio source, and press **Record & take notes**.
6. Press **Stop recording** when finished. Transcription can continue processing after capture stops; notes follow when the transcript is ready.

On supported macOS versions, ScribeLoop can suggest recording when it detects a possible call. A suggestion waits for your approval before recording. Call-end detection may show a **60-second countdown**; choose **Keep recording** if the conversation is still in progress. Use Stop manually if the call has ended but the browser remains active.

On macOS 13–14.1, start and stop recordings manually.

## Make it fit your conversations

In Preferences, you can choose recording duration limits, adjust microphone gain and background-noise filtering, and customize the instructions used to generate notes.

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

The planned public price is **US$1 as a one-time app purchase**, with no recurring ScribeLoop subscription or usage fee. Transcription and language-model services are not included; you pay your providers directly using your own keys.

The current build uses Deepgram cloud transcription and configurable notes providers. Fully self-hosted ASR and bundled local transcript-correction models are not included. See [pricing and model access](docs/PRICING_AND_MODEL_ACCESS.md) for the product plan and current availability.

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
