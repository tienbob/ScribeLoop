# Transcription language hints

Updated October 29, 2026. Desktop Preferences offers the 55 language choices in
Deepgram's hosted Nova-3 language-prompting guide, with search, a selection count,
and a reset to English and Vietnamese. Examples include French, Spanish, German,
Chinese (Mandarin and Cantonese), Japanese, Korean, Thai, Hindi, and Arabic.

Select any combination of language checkboxes. At least one must remain selected.
English and Vietnamese are the defaults. Choices survive reopening Preferences,
and filtering the list does not clear selections. The recording confirmation
shows the first three selected language names and the count of any others.
Existing `auto` settings restore English and Vietnamese; `en` and `vi` restore
their respective checkbox. Previously migrated legacy settings retain the old
bilingual default until a new selection is saved.

For bilingual streaming and explicit saved-audio reruns, ScribeLoop sends:

```text
model=nova-3&language=multi&language_hint=en&language_hint=vi
```

The hints are repeated query parameters, not a comma-separated string or separate
transcription requests. A custom selection such as English, French and Japanese
sends `language=multi&language_hint=en&language_hint=fr&language_hint=ja`.
Even a single checkbox is sent as a multilingual hint. Legacy API requests
without a `language_hints` array retain their fixed-language behavior.
Per-word language tags are retained for all supported languages, including
language changes by the same speaker and Cantonese locale tags.

The validated `language_hints` array travels through recording, call acceptance,
resume and manual rerun commands. New recording metadata retains the selected
array. Empty or unsupported selections are rejected before provider calls.

Deepgram's [October 23 release](https://developers.deepgram.com/changelog/2026/10/23)
expanded hosted Nova-3 multilingual to 55 languages, including Vietnamese. Its
[language-prompting guide](https://developers.deepgram.com/docs/nova-3-language-prompting)
documents language hints for both streaming and prerecorded endpoints. Hints
bias recognition; they do not strictly limit output to those languages or
guarantee accuracy. This feature is not claimed for self-hosted Deepgram.

This replaces the app's previous broad, unhinted automatic mode and its older
assumption that Vietnamese required a fixed-language request. It does not
automatically rerun existing recordings, upload private audio, or change
microphone gain/filter settings. Recognition quality still depends on the
recording, device, noise, overlap, language mix and provider behavior. Choose a
small set of expected languages for useful hints rather than selecting everything.

Generated/fake-provider tests verify the actual streaming URL and batch HTTP
parameters, custom three-language and single-language hints, word tags,
fixed-language compatibility, settings persistence and command validation.
These checks establish request correctness, not a measured accuracy
improvement on user audio. No new provider request is needed for those checks.
