# macOS Calendar

Implemented for desktop 0.1.18 (19); redesigned in 0.1.19 (20), with reset-permission recovery fixed in 0.1.20 (21), 3 November 2026.

Calendar configuration is centralized in **Settings → Calendar** (desktop
0.1.30, 4 November 2026). Connect Calendar there, approve the macOS prompt,
and choose calendars using the checkbox list. **Disconnect Calendar**, privacy
access and refresh are in the same category. Calendar changes apply immediately;
other settings retain their Save/Cancel workflow. No calendar is selected by
default. The meetings screen’s **Settings** button opens this category directly.
Accounts must already be available in Apple's Calendar app; Google purchase login
does not connect a calendar account.

After a development rebuild resets Calendar permission, use **Allow Calendar
access** in Settings → Calendar to request it again. ScribeLoop may be absent from the
macOS Settings list until that request is made. An undetermined or write-only
permission requests full event access; only denied/restricted states lead to
Settings. The app must not send a fresh permission state to an empty Settings list.

## Meeting flow

1. Today/Upcoming shows selected calendars over a bounded 14-day range, up to 500
   events. Canceled events and invitations declined by the current user are hidden.
   Upcoming hides ended events; Today includes earlier events for finding the
   event corresponding to a recent recording. Events are grouped by day, with
   time and meeting provider shown beside a single primary action.
2. **Open meeting** opens a recognized HTTPS meeting link in its normal app or
   browser. Supported hosts cover Google Meet, Teams, Zoom/Zoom Government and
   Webex. Multiple distinct meeting links require manual review rather than an
   arbitrary choice. Events without supported links remain visible for in-person use.
3. ScribeLoop continues observing calls using the existing recording popup. Opening
   a meeting link neither starts recording nor answers/places a call.
4. To connect an existing recording to an event, open that conversation and choose
   **Meeting → Link calendar event…**. This opens a separate linking view naming
   that recording; choose **Link this event**. The target is fixed when opened,
   so a background selection change cannot link a different recording by mistake.
   Linking an event never starts capture.

The conversation header displays its linked event title. Click it or use the
Meeting menu to replace or remove the link. General Calendar browsing does not
show or modify the previously selected recording. The saved snapshot includes title, calendar name, start/end time,
time zone and a hashed occurrence identity. It excludes meeting links, attendees,
event notes and location. It survives restart and transcript revision changes.

Calendar changes do not rewrite that snapshot. EventKit identifiers can change
after account resync; if an association is no longer appropriate, explicitly unlink
or replace it. Recurring occurrences have separate keys derived from the calendar,
item identity and original occurrence date, rather than one key for the whole series.

## Privacy and access

Calendar is opt-in. macOS 14+ requires full-event authorization to read events;
macOS 13 uses the legacy event-access request. The macOS permission is broader than
the calendars selected in ScribeLoop. ScribeLoop performs no calendar writes and
requests no Reminders or Google Calendar OAuth scopes.

The current list is held in memory. Only calendar selection IDs and the enable
preference are stored in UserDefaults; explicitly linked snapshots live in the
encrypted meeting database. They are excluded from transcription/summary requests
and the existing Markdown export. No automatic attendee-to-speaker assignment or
calendar-based recording start/stop is introduced.

Disconnect clears the displayed event cache and stops reads. It preserves existing
conversation links and the prior calendar selection. OS permission can be revoked
in Privacy & Security. Denial/revocation leaves manual recording available.

Local canonical development rebuilds reset ScribeLoop's Calendar and Screen
Recording permissions after build verification. Reapprove access locally for the
new build as needed. Preview/custom builds and other apps are excluded. This is a
development update workflow, not an automatic reset on testers' Macs.

Refresh rereads EventKit; event-store changes, returning to the app while the
panel is open, and time-zone changes refresh the list. A read timestamp is shown.
The open panel updates its time filters each minute and refreshes across midnight.
EventKit reflects what macOS has synced; ScribeLoop cannot guarantee provider-side
freshness while calendar accounts are offline. Missing selected calendars are
reported so the selection can be reviewed after account changes.

## Validation

- Native tests: provider URL validation, spoofed/unsafe URL rejection, ambiguous
  links, per-occurrence recurrence identity and minimal snapshot fields.
- Python tests: link/replace/unlink, persistence on reopen, rejected invalid
  snapshots without partial updates, desktop receipts/page reads and exclusion
  from model messages. Focused Calendar/desktop/summary/packaging checks: 47 passed.
- Native Calendar regression executable and macOS 13 deployment-target typecheck passed.
- Permission-route regression covers undetermined, denied, restricted, write-only
  and full-access states. A reset must route to the request button, not Settings.
- Redesign: offscreen visual review used synthetic data for setup, meeting-list
  and linking layouts. Permission failure has its own recovery screen. No real
  calendar selection or event association was changed during visual checks.
- 0.1.19 was rebuilt in place and reopened. The real Calendar screen displays
  “Your meetings”; on this build macOS Calendar access was unavailable, and the
  explicit “Allow Calendar access” recovery screen and “Open privacy settings”
  action were verified. No permission was granted automatically. The bundle audit
  and seven focused Calendar/packaging checks passed.
- Rebuilt the canonical app in place as 0.1.18 (19). The actual sidebar Calendar
  button opens the panel; Today/Upcoming, calendar selection, Refresh, Disconnect
  and the target conversation are visible. With zero calendars selected, the
  panel correctly requests selection and shows no event content. The workspace
  still contains all nine conversations. Bundle audit passed with no external
  dependencies. Screen Recording permission was reset per the update preference.

Real account sync, permission prompts and minimum-macOS runtime acceptance require
local user approval and suitable calendar data; synthetic checks do not establish
those outcomes. No meeting was opened, recorded or played in these checks.

Apple references:

- [EventKit access](https://developer.apple.com/documentation/eventkit/accessing-the-event-store)
- [Full event access](https://developer.apple.com/documentation/eventkit/ekeventstore/requestfullaccesstoevents(completion:))
- [External identifiers and recurrence](https://developer.apple.com/documentation/eventkit/ekcalendaritem/calendaritemexternalidentifier)
- [Original occurrence date](https://developer.apple.com/documentation/eventkit/ekevent/occurrencedate)

## Settings consolidation validation (4 November 2026)

Installed 0.1.30 (31) opens Calendar’s Settings shortcut directly on the Calendar
category and displays Disconnect Calendar and permission recovery there. The
notebook, settings sheet and native Settings scene share one coordinator so
connection and selection changes stay in sync. The old meetings-screen selection
sheet and disconnect menu are removed. Calendar and packaging regressions pass.
In-app notification banners now expire after five seconds; replacing a message
restarts its timer. Manual Dismiss remains available. The pending-action review
control is independent of transient banners.
