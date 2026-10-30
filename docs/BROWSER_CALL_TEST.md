# Controlled call acceptance — current native app

Updated 2026-10-28. Real Teams/Slack/browser acceptance remains open. Use the
[native app instructions](../packaging/macos/DESKTOP.md) and
[current checklist](CURRENT_STATUS.md); the browser UI is a development client.
Local MOSS selectors and separate per-upload checkboxes in older tests no longer
represent the current app. Summaries and configurable duration presets are implemented.

## Silent checks completed

The October 28 generated transport and metadata checks opened no capture streams
or playback devices. No real call, provider upload or physical alignment test was
performed. The user explicitly chose silent checks/docs only. Speaker output must
remain off; do not play test clips or change volume, mute, or output routing.

## Later coordinated real-call test

1. Have the user join a short controlled call with consenting participants and speaker output kept off. Do not launch media or change output devices. Observe metadata first; no recording is needed to check whether one useful prompt appears.
2. Check that idle app/notification activity does not prompt, Not now suppresses the episode, mute/brief silence does not create another prompt, and a later call can prompt. Browser candidates must identify only the browser, not a tab/service.
3. For a separately coordinated recording check, confirm the intended source and microphone. Browser scope includes all tabs. Microphone-only captures no app audio and needs no Screen Recording permission. Obtain missing OS access through the app's explicit controls.
4. Under the accepted first-use agreement, choose Record explicitly. Verify visible recording/Stop, provisional and confirmed words, anonymous speaker labels, and bounded duration. Stop finalizes the current stream without a second full transcription pass.
5. Inspect transcript/notes and citations visually. Do not use Play to verify audio under the speaker-off constraint. Remote/local source and physical alignment acceptance remain open unless measured through a permitted silent method.
6. Record detection delay, misses/false prompts, call-end grace, selected scope, capture faults and recovery. Reconnect/unknown metadata must not be counted as inactivity. Do not infer cross-source identity or overlap separation from anonymous labels.

Manual saved reruns explicitly upload retained audio again and can incur charges;
they are not needed to test detection. The app retains the previous transcript
until replacement succeeds. Saved playback, reruns and deletion are separate
workflows; this plan does not require altering existing conversations.

Older controlled microphone/public-audio and real-provider evidence is in
[the native UI log](MACOS_REAL_UI_TEST.md) and
[October 27 audit](MACOS_APP_AUDIT_2026-10-27.md). Those tests do not establish
Teams/Slack/browser compatibility, long-call stability, Linux parity or signed
release acceptance.
