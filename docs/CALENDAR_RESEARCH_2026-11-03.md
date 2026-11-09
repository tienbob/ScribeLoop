# Calendar workflow research

Researched 3 November 2026 from official product documentation. The user subsequently approved Today/Upcoming → Open meeting → existing recording
popup → event association. This is implemented for 0.1.18; see [implementation](MACOS_CALENDAR.md).

## What similar apps do

| App | Calendar workflow | Relevant lesson |
| --- | --- | --- |
| Granola | Upcoming list, pre-meeting notification, event title/time/attendees as note context, selectable calendars. Transcription starts when the user opens an event note; no background recording or auto-joining. | Calendar can organize notes without introducing a meeting bot. |
| Otter | Calendar events have individual auto-join controls alongside a default policy. Manual event choices survive default-policy changes; recurring events have series behavior. | Per-event choices and recurring-event rules need explicit semantics if scheduling recording is ever added. |
| Fireflies | Auto-join policies cover linked meetings, owned meetings, teammates, or explicit invitations. Recording rules further filter events; capture depends on admission to the meeting. | Joining and recording are separate states. A calendar event alone does not prove recording started. |

Sources:

- [Granola calendar integration](https://www.granola.ai/integrations/google-calendar)
- [Otter: choose which meetings are recorded](https://help.otter.ai/hc/en-us/articles/26010355877911-Choose-which-meetings-Otter-Notetaker-records)
- [Fireflies auto-join settings and recording rules](https://guide.fireflies.ai/articles/5074225515-learn-about-fireflies-auto-join-settings)

## Recommendation for review

Use a local calendar organizer workflow, drawing on Granola's upcoming-meeting
and contextual-note pattern. The user approved this core workflow after review:

1. Opt-in macOS Calendar access and calendar selection; keep manual recording usable.
2. Today/Upcoming list showing event title, time and a validated Join link.
3. Link a recording to a selected event occurrence, with title/time context.
   Attendees can be optional context; do not treat invitees as confirmed speakers.
4. Keep the existing explicit recording popup. Opening Join launches the meeting
   link; it does not itself authorize capture.
5. Keep calendar context local unless selected for model processing or email.

Defer reminders, pre-meeting note creation, calendar writes and automatic
recording triggers until their value and interaction with the existing popup are
reviewed. ScribeLoop currently captures locally, so a bot scheduling system would
be a separate product change rather than a prerequisite for Calendar.

Before implementation, settle whether the first delivery needs only Today/
Upcoming + Join, or also event-to-recording association. Validate revoked access,
recurring exceptions, canceled/declined events, overlapping events, time zones,
stale caches and identifier changes after sync. Do not promise that provider
calendar features transfer directly to EventKit without technical validation.

## Current delivery priority

Offline Ollama validation comes first, followed by remaining meeting regression
checks and email sharing (Summary default, Transcript or Audio). The approved Calendar core is implemented; real calendar acceptance remains. No calendar permissions were requested during this research.
