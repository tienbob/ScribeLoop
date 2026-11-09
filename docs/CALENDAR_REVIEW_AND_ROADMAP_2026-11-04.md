# Calendar Feature Review & Roadmap Assessment (2026-11-04)

Review of what the Calendar feature can do today (desktop 0.1.30/31), followed
by an assessment of what it should do in the future, grounded in the app
architecture. Companion documents:
[macOS Calendar implementation](MACOS_CALENDAR.md),
[calendar workflow research](CALENDAR_RESEARCH_2026-11-03.md),
[security &amp; performance audit](SECURITY_PERFORMANCE_AUDIT_2026-11-04.md).

## Part 1 — Current capabilities

### Configuration and access (Settings → Calendar)

- **Opt-in and centralized**: connect/disconnect, permission recovery, calendar
  selection and refresh all live in Settings → Calendar. No calendar is
  selected by default.
- **State-aware permission routing**: notDetermined/writeOnly → in-app
  request (full event access on macOS 14+, legacy on 13); denied/restricted →
  deep link to System Settings. A dev-build permission reset routes back to
  the request button, never to an empty Settings list.
- **Minimal persistence**: only calendar.enabled plus selected calendar IDs
  in UserDefaults. Event data never leaves memory on the shell side.

### Browsing meetings (“Your meetings” panel)

- **Today / Upcoming** views over a bounded 14-day window, max 500 events
  (with a truncation notice), grouped by day, local timezone shown.
- Filters out canceled events and events the current user declined; marks
  ended events; handles all-day events.
- **Meeting-link extraction with a strict allowlist**: NSDataDetector
  scans url/location/notes, but only HTTPS links to meet.google.com,
  teams.*, zoom.us, zoomgov.com, webex.com (including subdomains,
  port 443, no credentials) are accepted. Multiple distinct links → no
  button (forces manual review instead of guessing).
- **“Open meeting” never records** — it opens the link; recording stays
  behind the existing explicit popup (consent model §6 preserved).
- **Freshness**: refresh on EKEventStoreChanged, app activation, timezone
  change, midnight rollover (60 s timer), manual button. Bounded staleness
  is communicated honestly.
- **Performance discipline**: CalendarReadQueue allows at most one blocking
  EventKit read at a time, 150 ms debounce, latest-request-wins, generation
  tokens discard stale results, reads run detached at utility priority — the
  main thread never blocks on EventKit.

### Linking recordings to events

- **Fixed-target linking** (Meeting → Link calendar event…): the recording
  is bound when the view opens, so a background selection change cannot link
  the wrong conversation. Link / replace / remove are all supported, with
  protocol receipts (dispatching → accepted/rejected).
- **Minimal, validated snapshot**: title, calendar name, start/end, timezone,
  and a hashed per-occurrence identity (SHA-256 of calendar ID + external
  item ID + occurrence date — recurring occurrences get distinct keys).
  Deliberately excludes meeting links, attendees, notes and location.
- **Server-side validation** (api/calendar_links.py): strict Pydantic
  (extra=forbid, strict=True, fixed patterns, control-char rejection,
  end ≥ start); invalid snapshots are rejected atomically with no partial
  update.
- **Persistence properties** (verified by tests/test_calendar_links.py):
  survives restart, survives transcript revision changes, is excluded from
  summary/model inputs, and never rewrites the conversation title.

### Privacy posture

No calendar writes, no Reminders access, no Google Calendar OAuth scopes, no
cloud transport for event data. The macOS permission is broader than the
in-app calendar selection, and the docs say so honestly.

**Verdict**: as a local calendar organizer + recording association feature
(the approved Granola-style scope from the research doc), this is complete,
well-tested, and architecturally clean.

## Part 2 — Future assessment, grounded in the architecture

### Architecture constraints that shape the roadmap

1. **Local-first, privacy-preserving** — encrypted SQLCipher workspace,
   event-sourced events table, snapshots validated at the protocol boundary.
2. **Strict shell/core split** — SwiftUI shell + bundled Python core on a
   loopback WebSocket with typed, validated commands and receipts. The core
   is platform-independent (Linux is a stated target), so EventKit must stay
   a shell concern — the current snapshot-at-the-boundary pattern is right.
3. **Consent model (§6)** — detection never silently records; capture is
   always an explicit user action.
4. **Two disconnected subsystems**: the discovery service (app-metadata
   meeting detection → Record/Not-now popup) and the calendar panel do not
   talk to each other today.
5. **Phase 3 roadmap**: an agent layer (MCP tools) explicitly targets
   “notes, calendar events, todos” from summaries.
6. **New commercial substrate**: Google accounts + Ed25519 entitlements
   (local_owned / cloud_until) can gate advanced features.

### Recommended priorities

**Near term — bridge the two silos (highest value, lowest risk):**

1. **Enrich discovery candidates with calendar context.** When the observer
   detects a candidate meeting, the shell can match it against selected
   calendars (title + time-window proximity, entirely local) and show
   “Looks like your 2:00 Teams sync” in the Record/Not-now prompt. Uses both
   existing subsystems, adds no permissions, no writes, and no new capture
   semantics — pure metadata enrichment of an existing UI.
2. **Post-recording link suggestion.** When a recording finalizes, suggest
   the overlapping calendar event (from the already-read bounded window) as
   a one-tap link candidate — suggestion only, user confirms, preserving
   the consent model.
3. **Include event context in email exports.** The snapshot already lives in
   the session DB; EmailExports can prepend the meeting title/time to the
   subject/body. Trivial, purely additive.
4. **Opt-in summary context.** The snapshot is deliberately excluded from
   model input today (tested). A per-conversation, explicit “use event
   title/time as context” toggle would improve summary grounding (“Q3
   planning” vs. “a meeting”); it must stay opt-in and visible in prompt
   provenance.

**Mid term — quality and depth (still local, still read-only):**

5. **Identifier-change reconciliation.** EventKit IDs change after account
   resync; today a stale link requires manual unlink/replace. A fuzzy
   rematch suggestion (title + time proximity) presented for confirmation
   would reduce silent breakage — keep the per-occurrence hashing, add a
   repair suggestion.
6. **Pre-meeting readiness (Granola pattern, already researched).** A local
   notification N minutes before an event with a validated join link. A
   lightweight scheduler in the shell does this without touching the Python
   core. Still no auto-recording.
7. **Recurring-series semantics (Otter lesson).** Per-occurrence keys exist
   already. If any scheduled/per-event behavior is added, define series vs.
   occurrence semantics explicitly before building.

**Long term — Phase 3 agent alignment (strategic):**

8. **Agent-drafted calendar actions.** Action items with due dates → agent
   drafts a follow-up event → explicit user confirmation → shell performs
   the EventKit write. This flips the feature to bidirectional and requires:
   (a) an explicit write-permission consent step (the “ScribeLoop does not
   change your calendar” promise must be consciously revised), (b) new
   validated write commands + receipts in the core protocol, (c) entitlement
   gating if it becomes a paid capability.

### What it should NOT do (anti-goals derived from the architecture)

- **No Google Calendar OAuth / direct provider sync.** It would duplicate
  the account service, expand the cloud surface, and contradict the
  “events stay on this Mac” promise. EventKit is the only calendar interface.
- **No auto-join / scheduled recording** (Fireflies/Otter-style). The
  research doc explicitly defers it; §6 “detection never silently records”
  makes it a separate product decision, contingent on real-call validation
  of the current flow first.
- **No attendee/notes storage** without a specific consented feature
  needing it — snapshot minimalism is deliberate and regression-tested.
- **No EventKit in the Python core.** The core must stay Linux-portable;
  keep the snapshot-at-the-boundary pattern exactly as designed.

### Carry-over from the security audit

calendar_links.read_link (audit finding L2) should tolerate/re-validate
stored JSON and stop using private store._now() — fix before any feature
builds on the read path (roadmap items 2, 3, and 5 all extend it).

### Summary

The current delivery is a disciplined, privacy-first v1 that fully implements
the approved scope. The architecture’s biggest untapped synergy is
**discovery ↔ calendar** (items 1–2), which delivers real user value with
zero new privacy surface. Bidirectional calendar (agent actions) is the right
long-term direction per ARCHITECTURE.md Phase 3, but it must be staged behind
explicit write consent.
