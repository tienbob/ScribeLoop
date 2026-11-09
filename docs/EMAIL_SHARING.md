# Share a conversation by email

Desktop 0.1.33 (34), 4 November 2026.

Open a saved conversation and click **Email**, or choose **Meeting → Share by
email…**. Recording and processing must finish before preparing an export.

1. Check any combination of **Summary** (the default), **Transcript** and **Audio**.
2. **Include source references** is off by default. Enable it to add citation
   numbers and the Sources section to the summary. Review the subject; **Preview** is optional.
3. Choose **Open email draft** once. ScribeLoop prepares every selected item and
   opens one draft with all attachments automatically. Continue in the mail app to edit the message,
   choose recipients and press Send yourself. ScribeLoop never sends automatically.

Summary email bodies use a prominent title, bold section headings, readable
bullets. Source references are optional and off by default. When enabled, citation
groups are spaced (for example, [1, 11, 42]) and styled smaller; the Sources section
uses smaller text with tight spacing. When disabled, generated citation markers
and the Sources section are both omitted. Literal bracketed summary text is
preserved. Saved notes and references inside ScribeLoop are unchanged. Plain text is available for clients that
do not accept the rich body. Transcript and audio attachment selection is unchanged.

The core supplies literal semantic text blocks, and the shell applies native text
attributes. No HTML is imported, no images are loaded and no remote resources are
fetched. The in-app rich preview is bounded to 12,000 characters; the full body
and saved summary remain complete.

## The three choices

| Choice | Draft content | Availability |
| --- | --- | --- |
| Summary | Saved notes in the message body, with optional timestamped source references. | Needs a saved, up-to-date summary. Sharing does not generate or repair notes. |
| Transcript | A complete text attachment containing saved speech, speaker labels and timestamps, across all transcript pages. | Needs saved transcript text. |
| Audio | Retained microphone and meeting audio mixed into one playable attachment. macOS creates compressed M4A when possible; WAV is the fallback. | Needs retained, authenticated audio. Expired audio cannot be recreated from text. |

Selections are independent checkboxes. Summary enters the body; selected Transcript
and Audio files enter the same draft. No manual export or attachment picker is
needed. Preparation is sequential and reuses already prepared items. A failed item
prevents handoff of a partial draft. Calendar attendees are not filled in as recipients. Google purchase login
and Gmail/Outlook permissions are not required.

Outdated/missing summaries explain why sharing is unavailable; generate notes
separately from the Meeting menu if desired. There is no provider request,
regeneration charge or automatic cloud fallback from sharing.

## Mail apps and fallback

Draft handoff uses the macOS email-compose sharing service. Mail-client/account
support varies; a service may ask the user to configure a mail account. ScribeLoop
does not configure accounts or automate Send. The preview remains usable if a
mail service is unavailable or reports a handoff failure.

Use **Save…** beside a prepared item to save a copy, or **Copy text** for Summary and
Transcript. The subject and actual selected content remain visible before handoff.
Text preview is shortened after 12,000 characters, while the full text file is
retained. The app supports text exports up to 2 MB.

Attachments above **15 MB combined** cannot be passed through the email button; save them
instead and use the mail app's attachment options. This conservative limit leaves
room for email encoding overhead but does not guarantee every provider's limit.
No oversized file is uploaded to a hosted sharing service automatically.

## Temporary copies

Exports are readable files outside the encrypted workspace, in a private,
user-owned temporary directory with private file permissions. Only the selected
content is exported: no API keys, account tokens, calendar notes, internal metadata
or encrypted recording containers enter the email.

Closing a preview removes its unused prepared files. Files handed to the mail app
are retained so it can consume the attachment; handoff renews their seven-day
lifetime. Expired files are cleaned on the next app launch or export preparation.
Explicitly saved copies are kept at the user's selected destination. The export
cache has a bounded storage budget and reserves space before preparing audio.

Audio is authenticated in full before export and mixed in bounded windows, using
the existing playback index. Exporting never plays audio. Cancellation holds the
reader lease until its worker finishes, removes unused output, and records a
terminal request receipt for reconnecting clients.

## Validation

- Full Python suite after optional-source update: **719 passed**.
- Fourteen sharing regressions cover saved-summary-only content, complete transcript
  pagination, authenticated mixed audio, real synthetic M4A compression, missing/
  stale/expired content, corrupt audio cleanup, cancellation, export limits,
  symlinks, protocol receipts and compact semantic summary formatting.
- Native tests cover combined transcript/audio handoff, summary body inclusion,
  unchecked content exclusion, partial preparation rejection, combined size limits,
  owned paths, private permissions, allowed names, size matching and symlink rejection.
  No mail service was invoked by automated tests.
- Export regression rerun after checkbox rework: **10 passed**.
- SwiftUI typecheck and production compilation target macOS 13+ on Apple Silicon.

Manual acceptance: the earlier font/display sample transcript was sent through
Apple Mail to the user-authorized recipient and verified in Sent. Installed 0.1.24 acceptance: checking all three options and clicking Open email
draft once produced the saved summary in Apple Mail’s body plus Transcript.txt
(4 KB) and Conversation.m4a (983 KB), with no Save or attachment-picker steps.
The live check exposed a receipt-ordering race; preparation now waits for both
the export result and its accepted receipt before starting the next selected item.
The corrected installed build completed all three exports and opened one draft.
The user-authorized combined email was sent through Apple Mail and verified in
Sent on 4 November 2026. Recipient delivery/read status is not claimed. Speakers
remained off throughout. Automated
tests use synthetic audio; exporting and composing do not play audio or call models.

Apple reference: [NSSharingService](https://developer.apple.com/documentation/appkit/nssharingservice).

Formatting acceptance (4 November 2026): an unsent Apple Mail draft visibly
preserved the native rich title, bold headings, grouped smaller citations and
compact Sources paragraphs. No Send action was performed by the agent. Native
checks cover font/paragraph hierarchy, literal markup text, bounded preview and
RTF serialization; all original references remain in the recap. Summary and
transcript exports now use current-revision person labels, preserving custom
names after re-transcription. Save/Copy remain available after handoff and a
failed handoff permits an explicit retry.

Optional source references (0.1.33): each sharing panel starts with references off.
Changing the checkbox invalidates its prepared summary before preview or handoff;
a cited payload cannot be reused for a clean draft or vice versa. The option
applies to the summary body, preview, Copy text and saved Summary.txt; transcript
and audio attachments retain their normal content. Tests cover default-off,
explicit enable/disable, strict boolean validation, preservation of literal
brackets and unchanged stored grounded notes.
