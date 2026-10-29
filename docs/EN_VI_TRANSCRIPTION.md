# English and Vietnamese transcription

Updated October 29, 2026. The desktop language scope is English and Vietnamese.

Preferences offers **English + Tiếng Việt** (default), **English only**, and
**Tiếng Việt only**. Existing `auto` settings now mean the bilingual profile.
Older saved desktop choices such as French are migrated in memory to this
default; saved transcripts and audio are not rewritten.

For bilingual streaming and explicit saved-audio reruns, ScribeLoop sends:

```text
model=nova-3&language=multi&language_hint=en&language_hint=vi
```

The hints are repeated query parameters, not a comma-separated string or two
independent transcription requests. Single-language modes send `language=en` or
`language=vi` without multilingual hints. Vietnamese per-word language tags are
retained, including language changes by the same speaker.

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
single-language profile for calls that do not switch languages.

Generated/fake-provider tests verify the actual streaming URL and batch HTTP
parameters, English/Vietnamese word tags, fixed-language requests and settings
migration. These checks establish request correctness, not a measured accuracy
improvement on user audio. No new provider request is needed for those checks.
