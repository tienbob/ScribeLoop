  # Local transcription with Ollama

ScribeLoop can transcribe conversations entirely on your Mac using a locally running [Ollama](https://ollama.com) server instead of Deepgram cloud. Audio never leaves your machine for transcription.

## Choosing the local engine

1. Open **Preferences → Models** and pick **Ollama · Local** as the transcription engine.
2. Enter an audio-capable model. For a small model, use `hf.co/mradermacher/Qwen3-ASR-0.6B-GGUF:Q4_K_M` (verified locally on 5 November 2026). Ollama reports 752M decoder parameters and Q4_K_M, with a separate Q8_0 audio projector; the package is about 698 MB. This is mixed-precision 4-bit quantization, not every tensor at exactly four bits. Its installed metadata must report audio input support; a text-only model cannot transcribe. Model downloads are separate from the app.
3. Use **Check Ollama** to check the model currently typed in Preferences, without saving the draft. Progress and failures appear beside the button. The check starts Ollama if needed, prepares the model and sends a tiny synthetic silence request to verify the actual audio endpoint.
4. For the entire flow to stay local, choose **Use Ollama for notes too**, or select **Ollama · Local** under Conversation notes and enter an installed text model. The same audio/text-capable model can serve both roles. Click **Done** to save. Changing engines takes effect without restarting.

The standalone check verifies transcription, not notes quality. A cloud notes
provider still receives transcript text; the UI warns when it remains selected.
Set up and download models while online before expecting offline operation.

## What happens automatically

Preparation starts when a recording starts (and never blocks or interrupts capture):

1. **Probe** the local Ollama server on loopback.
2. **Open it for you** if it is not running — via the Ollama app if installed, otherwise `ollama serve` from the command-line installation. If Ollama is not installed at all, recording still proceeds and transcription fails closed with an install pointer ([ollama.com](https://ollama.com)).
3. **Download the named model once** if it is not installed yet, using Ollama's own downloader, with progress shown in the app. If the name is not in the Ollama library, this fails closed with a clear message and nothing is transcribed.
4. **Load the model** with a tiny silent check so load errors surface before the conversation ends instead of after it.

## When transcription runs

**Only after the conversation has ended.** There is no live transcript while recording; pressing Stop (or the end of a detected call) starts transcription. The transcript, notes, and playback are otherwise identical to a Deepgram recording — the audio stays encrypted at rest, and notes still require a configured summary provider.

## Local transcript cleanup

In Settings → Models, the **Transcript cleanup** section is enabled by default
for the local workflow. It runs after transcription and before notes, on its own
configured model — independent of the transcription engine and of the notes
provider. Any of the notes providers may be chosen: on-device (Ollama, LM
Studio) or cloud (OpenAI, Gemini, any OpenAI-compatible endpoint). Choosing a
cloud provider sends transcript text to that provider during cleanup; audio is
never uploaded by this step, and on-device providers keep the text on this Mac.
When the cleanup model field is empty, cleanup reuses the notes model only while
notes run on the same provider; otherwise it stays off until an explicit model
is chosen. Cleanup API keys and the cleanup model are remembered per provider,
exactly like notes keys, so switching cleanup providers needs no re-entry. Both
the text-model metadata and the endpoint are checked. It applies to
new recordings and **Transcribe again**, not to manually regenerating notes for
existing text.

The editor corrects punctuation, spacing and clear spelling/recognition mistakes.
It keeps the spoken language and leaves ambiguous wording unchanged. Requests
are bounded to four segments and 8 KB of text, with a maximum of 64 requests per
cleanup. JSON output must preserve every segment ID/order; validation rejects
large rewrites, changed digit sequences/signs and changed negations. Vietnamese
segments (language "vi") may only receive diacritics, casing, spacing and
punctuation changes: the word sequence must match after case/diacritic folding,
so any changed word is rejected outright — folding cannot distinguish tone
homographs, so wrong-tone fixes are possible and the model, not the validator,
is responsible for them. Other languages allow small wording fixes, gated by a
folded character-similarity check. Validation is per segment: a rejected
segment keeps its original wording and the batch's good segments are still
applied; the app reports which segments were left unchanged and why. These
checks limit edits but do not prove semantic correctness.

An experimental **Experience → Skip cleanup validation** toggle bypasses every
check: the model's raw output is saved as-is (changed numbers, negations and
rewrites included). Segments the model drops keep their original text. A warning
marks the resulting revision as unvalidated. This mode exists for debugging the
cleanup model and should be off for normal use.

All batches validate before any edits are committed. Changed text becomes a new
encrypted transcript revision, retaining original ASR text, timestamps, speaker
identities, manual labels and person links. An unchanged response creates no new
revision. The original is retained in stored revision history; the current
transcript UI shows the latest revision. If a batch request fails outright, the
app keeps the entire original and continues generating notes from it. No cloud
fallback or automatic retry is used. Turn the option off to use raw local ASR
text.

Live Gemma E2B checks corrected synthetic “tomorow” → “tomorrow” and “ứng dung” →
“ứng dụng”, preserving numbers. For the already tested real Vietnamese call,
all eight segments were left unchanged: the model did not guess at ambiguous
speech. This text-only stage cannot recover information missing from the audio
recognizer's output. A larger local notes model usually restores Vietnamese
diacritics more reliably than a small one; the model-size guidance in
Preferences says the same.

## Speaker labels (opt-in add-on)

The Ollama transcription API does not include speaker information. ScribeLoop can add **anonymous Speaker 1/2 labels** after Stop using its optional local diarization engine, controlled by the **Speaker labels · local diarization** toggle in Preferences:

- It requires the diarization add-on (Torch and pyannote) and its local model; both are separate installs.
- Labels come from voice-turn diarization over the meeting audio, with conservative overlap handling: segments with ambiguous or overlapping voices stay unknown.
- When the diarization model is unavailable, the transcript is still saved — without labels — and a warning explains it.

## Current limitations

- No live transcript during recording (the engine is batch-only by design).
- No word-level speaker separation from the transcription itself; labels come from the opt-in diarization add-on.
- Auto language detection depends on the selected model; you can pin English or Vietnamese explicitly.
- Consecutive windows can repeat a word at the boundary; review the transcript against playback before relying on it.
- Ollama can return text without segment timestamps. Those results are retained as one segment per audio window, up to 30 seconds, with approximate timing; precise word timing is not claimed.

## Privacy

Transcription stays on your Mac. Notes generation is separate: when a cloud summary provider is configured, transcript text (not audio) is sent to that provider after transcription, exactly as with Deepgram recordings.

The Ollama transcription endpoint must be loopback. Cloud model tags and model
metadata that point to a remote model are rejected before preparing audio. The
Ollama notes provider also checks for a downloaded local text model. No cloud
fallback is performed when local setup fails; encrypted audio remains saved.

Functional verification for 0.1.17 is recorded in
[Ollama flow fixes](OLLAMA_FLOW_2026-11-03.md).
