# ScribeLoop

### Your meetings, remembered.

A native meeting notebook for Mac. Follow the conversation live, turn it into
useful notes, and find the decisions that matter later—with links back to what
was actually said.

**Record → Transcribe → Take notes → Search → Ask**

[Get started](#get-started) · [Features](#a-notebook-for-the-whole-conversation) ·
[Search & Ask](#find-it-or-ask-about-it) · [Privacy](#your-data-your-choices) ·
[What's new](CHANGELOG.md) · [Developer guides](#for-developers)

> **Internal preview:** Apple Silicon Macs, macOS 13 or later. Automatic call
> detection requires macOS 14.2+. The app includes its runtime; no Python,
> Homebrew or terminal server is needed to use it.

## A notebook for the whole conversation

| During the meeting | After the meeting |
| --- | --- |
| Detect likely calls and offer **Record / Not now** | Get an overview, decisions, action items and open questions |
| Capture your microphone, a meeting app/browser, or system audio | Follow note citations back to the transcript |
| Follow live transcription with anonymous speaker labels | Name speakers and edit conversation titles |
| Choose automatic or fixed-language recognition | Replay audio with seeking and playback-speed controls |
| Stop manually or let detected-call monitoring help | Search meetings, ask questions and share what matters |

Keep each conversation's **Notes, Transcript and Audio** together. Optional Calendar
integration shows upcoming events and lets you link them to saved conversations.
Export Markdown or prepare an email draft to review and send in your mail app.

## Get started

### 1. Install

Download the internal DMG shared by your team, open it, and drag
**ScribeLoop.app** into **Applications**. Launch it from there.

Current internal builds are ad-hoc signed and not notarized. Installation and
app-specific permission troubleshooting are covered in the
[installation guide](packaging/macos/INTERNAL_INSTALL.txt).

### 2. Choose your models

Open **Preferences → Models**:

- Choose **Deepgram** or **AssemblyAI** for cloud transcription and enter that
  provider's API key. Deepgram is the default.
- Choose a notes provider and model, then enter its key when required.
  **OpenAI** is the default; Gemini and compatible local/cloud services are options.
- Choose **Auto** or a fixed conversation language. Language support depends on
  the transcription provider; use the fixed Vietnamese option when needed.

Click **Done** to save. Cancel or dismiss Preferences to discard unsaved edits.
The app uses your provider accounts; AI credits are not included.

### 3. Record a conversation

Accept the first-use processing agreement and grant Microphone access.
Meeting-app or system audio also requires macOS Screen Recording permission.

When a likely call is detected, choose **Record**. Choose **Not now** to dismiss it.
You can also use **New recording** for a manual session, including microphone only.

Watch the transcript arrive, then press **Stop** when finished. Detected calls can
show an ending countdown; choose **Keep recording** if the conversation continues.
Notes and a topic-based title follow when configured.

**Opening ScribeLoop never starts a recording.** On macOS 13–14.1, use manual
recording and Stop. Browser capture can include audio from other tabs in that browser.

## Find it or ask about it

**Search** finds passages. **Ask** uses retrieved evidence to answer a question.

Search updates automatically when you pause typing for half a second. With cloud
Hybrid Search enabled, those searches use your configured embedding provider.

| Mode | What it does |
| --- | --- |
| **Keyword Search** | Searches saved text locally. Enabled by default; no embedding service needed. |
| **Hybrid Search** | Combines keyword matches with semantic retrieval to find related ideas across wording and languages. Enable it in Preferences and configure an embedding provider. |
| **Ask** | Answers questions about the current, chosen or all meetings using retrieved excerpts and source citations. Requires configured Hybrid Search and a notes provider. |

Try questions such as:

- “What did we decide about the launch?”
- “Which tasks did we leave open?”
- “How does the proposed architecture work?”

Use **Ask this meeting** inside a conversation, or **Ask across meetings** in the
sidebar. The context bar shows which meetings are included; **Change** lets you
choose specific meetings. Changing context starts a new chat, and History keeps
your saved threads. History also lets you delete individual chats or clear them all.
Open a citation to inspect the source. Ask sends a bounded evidence packet,
rather than falling back to uploading entire transcripts.

Embedding options include **Gemini** (default), **OpenAI**, **bundled local
Granite**, **Ollama**, **LM Studio** and **Custom** endpoints. Local embeddings
keep embedding processing on your device; a cloud notes provider still receives
the evidence used to generate an Ask answer.

Search and Ask are under active quality validation. Missing evidence can lead to
an unanswered question, and citations do not guarantee that every generated claim
is correct. Review important conclusions against their sources.

## Choose how your AI runs

| Task | Options | When it runs |
| --- | --- | --- |
| Live cloud transcription | Deepgram or AssemblyAI | While recording |
| Optional local transcription | Ollama | After recording ends |
| Notes and optional transcript cleanup | OpenAI, Gemini, Ollama, LM Studio or a compatible custom service | During supported notes flows and after recording |
| Semantic embeddings | Cloud provider, local server or bundled Granite | When Hybrid Search indexes or retrieves saved content |

Optional local transcription needs a compatible Ollama setup and model; it does
not provide the cloud engines' live transcript experience. See
[local transcription](docs/LOCAL_TRANSCRIPTION.md).

Customize notes instructions, vocabulary, microphone gain and noise filtering in
Preferences. Retained audio can be transcribed again through a batch route.
A cloud rerun uploads audio again and can incur another provider charge; stopping
a live recording does not automatically trigger a full-audio second pass.

## Your data, your choices

- **Encrypted on your Mac.** Meeting data and retained audio use encrypted local
  storage. Provider credentials use macOS Keychain.
- **Cloud processing is explicit.** Cloud transcription sends audio to your chosen
  ASR provider. Cloud notes/cleanup send transcript text; cloud Hybrid Search sends
  index excerpts and queries. Provider charges and policies apply.
- **You choose when to record.** First-use agreement does not enable silent recording.
  Detected calls still offer Record / Not now. Get agreement from people you record.
- **Keep audio when you need it.** Unarchived audio expires after **30 days**.
  Archive it to retain it longer. Transcripts and notes remain after audio expires.
- **Share deliberately.** Markdown exports and email attachments leave the encrypted
  workspace. Review them before sharing.

Each Mac has its own workspace. Account sync and workspace transfer are not included.
See [audio retention](docs/RETAINED_AUDIO.md) and
[storage details](docs/SECURE_STORAGE.md).

## Requirements and preview limits

| | |
| --- | --- |
| **Mac** | Apple Silicon, M1 or newer |
| **Operating system** | macOS 13+; automatic detection needs macOS 14.2+ |
| **Cloud AI** | Internet connection and your own provider credentials |
| **Local AI** | Compatible local server/models where selected; bundled Granite needs no model server |
| **Permissions** | Microphone; Screen Recording for app/system audio; Calendar only if enabled |

Intel Macs and macOS 11–12 are not supported by the current installer.
An [Ubuntu 22.04+ GTK preview](packaging/linux/README.md) is available separately;
it does not yet have accepted macOS feature/capture parity.

This is an internal preview, not a notarized public release. Transcription,
speaker labels, notes and answers can contain errors. Call detection uses activity
heuristics rather than official meeting APIs. Broader physical audio, device-change,
OS compatibility and distribution validation remain open.

## For developers

The macOS app uses a SwiftUI/AppKit shell, native capture helpers and a bundled
Python core. SQLCipher stores meeting state and search indexes; retained audio
uses authenticated encryption. The web UI is a development/demo client.

- [Architecture and major flow diagrams](ARCHITECTURE.md)
- [Build and validate the macOS app](packaging/macos/DESKTOP.md)
- [Documentation index](docs/README.md)
- [Meeting workspace delivery plan](docs/MEETING_WORKSPACE_DELIVERY_PLAN.md)
- [Search/Ask audit fixes and measured results](docs/SEARCH_ASK_AUDIT_REMEDIATION.md)
- [Ubuntu preview](packaging/linux/README.md)

## Feedback

Please report your app version, Mac model, macOS version, audio source, exact error
and steps to reproduce. Keep API keys, passwords and private recordings out of bug
reports.

**License:** Proprietary.
