# Email Sharing Feature Review & Roadmap Assessment (2026-11-04)

Review of what Share-by-email can do today (desktop 0.1.24+), followed by an
assessment of what it should do in the future, grounded in the app architecture.
Companion documents: [Email sharing implementation](EMAIL_SHARING.md),
[security & performance audit](SECURITY_PERFORMANCE_AUDIT_2026-11-04.md),
[calendar review](CALENDAR_REVIEW_AND_ROADMAP_2026-11-04.md).

## Part 1 — Current capabilities

### Entry and gating

- Open a saved conversation and click **Email**, or use **Meeting → Share by
  email…**. Recording and processing must finish first; the panel refuses
  preparation while audio is busy or a summary is running.
- Three independent checkboxes: **Summary** (default, enters the message body
  with timestamped [n] source references), **Transcript** (full text file,
  all pages, speaker labels + timestamps), **Audio** (retained mic + meeting
  audio mixed and compressed).
- Missing, stale or expired content is explained inline; sharing never
  generates, repairs or re-bills notes — no provider request, no cloud fallback.

### Content preparation (core: api/email_exports.py)

- Summary renders the grounded summary with numbered citations plus a Sources
  section listing segment timestamps and speaker labels.
- Transcript spans all revisions/pages with 0:00-style timestamps and
  Microphone/Meeting-audio stream labels; bounded at 2 MB text.
- Audio authenticates the entire retained SLPA container first, mixes tracks
  in bounded 60 s windows through the existing playback index (never plays),
  caps at ~3 h / 400 MB, writes a WAV, then compresses to M4A via
  /usr/bin/afconvert (fixed argv, 180 s timeout) with WAV fallback.

### One-draft handoff (shell: EmailSharing.swift)

- **Open email draft** once prepares every selected item sequentially, reusing
  already prepared files, and opens a single NSSharingService composeEmail
  draft with all attachments. ScribeLoop never sends, never fills recipients
  (calendar attendees are deliberately not auto-filled), never configures mail
  accounts, and requires no Gmail/Outlook permissions.
- Result-before-receipt ordering is enforced: the next export cannot overtake
  the previous item’s accepted receipt (a real handoff race fixed in 0.1.24).
- A failed item blocks handoff of a partial draft. Cancellation holds the
  reader lease until its worker finishes, removes unused output, and records
  a terminal rejected receipt for reconnecting clients.

### Bounds and fallbacks

- 2 MB text, 400 MB audio, 15 MB combined email limit (encoding headroom), 12 k
  preview truncation (full text stays in the attachment), 2 GB temporary cache
  with explicit space reservation before audio, max 30 prepared exports.
- **Save…** (NSSavePanel via temp-file + replaceItemAt) and **Copy text** work
  without any mail service; oversized files must be saved manually rather than
  auto-uploaded to hosted transfer services.

### Temporary-file security (defense in depth)

- Core side: user-private temp dir (tempfile dir + uid suffix), directory
  verified for uid, dir type and 0700 before use; files created 0600 with
  O_EXCL; symlink rejection on every traversal; 7-day TTL renewed at handoff;
  cleanup at app launch and before each export; bounded cache budget.
- Shell side: EmailAttachment re-verifies every path handed back by the core —
  containment under the expected scribeloop-email-<uid>/<export> folder,
  uid ownership, permissions & 0o077 == 0, regular-file type, size match with
  the announced byte count, and a name allowlist (Summary.txt, Transcript.txt,
  Conversation.m4a/.wav).
- Closing a preview discards unused exports (onDisappear discard command);
  files handed to the mail app are retained so it can consume the attachment.

### Privacy properties

- Only selected content is exported: no API keys, account tokens, calendar
  notes, internal metadata or encrypted containers enter the email
  (tests assert synthetic secrets and session IDs never appear).
- No provider calls: the Python test suite monkeypatches httpx to fail if a
  sharing request ever touches the network. Exporting does not call models.
- Explicitly saved copies are user-chosen destinations.

### Validation

- Python: ten sharing regressions (saved-summary-only content, complete
  transcript pagination, authenticated mixed audio, real synthetic M4A
  compression, missing/stale/expired content, corrupt-audio cleanup,
  cancellation, export limits, symlink traversal, protocol receipts,
  disconnected-client terminal receipt). Full suite: 698 passed.
- Native EmailSharingTests: combined handoff, summary body inclusion,
  unchecked-content exclusion, partial-preparation rejection, combined size
  limit, owned paths, private permissions, allowed names, size matching and
  symlink rejection. No mail service invoked by automated tests.
- Manual acceptance: real send through Apple Mail verified in Sent
  (summary body + Transcript.txt 4 KB + Conversation.m4a 983 KB).

**Verdict**: a complete, disciplined v1 of the local email handoff scope —
no send automation, no new permissions, no provider surface, with an
unusually strong temporary-file security design.

## Part 2 — Future assessment, grounded in the architecture

### Architecture constraints that shape the roadmap

1. **Local-first, privacy-preserving** — encrypted workspace; exports are the
   one sanctioned bridge where meeting content deliberately leaves it as
   plaintext, user-initiated, bounded and audited.
2. **Strict shell/core split** — content assembly (summary rendering,
   transcript pagination, audio mixing) lives in the Python core, so it stays
   platform-portable for the Linux target; the shell only validates paths and
   hands files to NSSharingService. Keep it that way.
3. **Consent model** — the app never sends mail and never auto-fills
   recipients; the user composes and presses Send.
4. **Accounts substrate exists but is deliberately unused here** — Google
   sign-in/entitlements could gate features, but no mail OAuth scopes are
   requested.
5. **Phase 3 roadmap** — an agent layer (MCP tools) turning summaries into
   actions; drafts (not sends) are the natural email-side expression of that.
6. **Calendar feature is adjacent** — linked-event snapshots already live in
   the same session DB that email exports read.

### Recommended priorities

**Near term — low-risk polish (builds on existing paths):**

1. **Calendar-linked context in the draft.** If the conversation has a linked
   event snapshot, prepend its title/time to the subject or body (“Q3
   Planning · 2:00–3:00 PM”). The snapshot is already in the session DB and
   contains no attendees — privacy posture unchanged.
2. **Post-send cleanup.** The shell already learns didShareItems success via
   NSSharingServiceDelegate; trigger an explicit discard_email_exports at that
   point (instead of waiting out the 7-day TTL). Plaintext lifetime shrinks
   from days to minutes after a confirmed handoff.
3. **Fix the read-path couplings first** (audit L1/L4): public wrappers for
   RetainedAudio export support, and a purge-on-quit decision for retained
   exports. Both sit directly under this feature’s hot path.

**Mid term — generalize sharing into an export framework:**

4. **Generalize EmailExports into export targets.** The core already produces
   three kinds; the natural next targets are Notes/Reminders drafts and a
   calendar .ics follow-up draft — all still user-confirmed, no writes,
   reusing the same prepare/validate/receipt pipeline and the same temp-file
   security. This is also the lowest-risk rehearsal for Phase 3 agent drafts.
5. **Body formats.** Offer Markdown or simple HTML rendering for the summary
   body (Mail renders HTML); keep the plain-text default.
6. **Multi-conversation sharing.** Today one draft = one conversation. A
   series recap (per-occurrence conversations, already keyed per occurrence)
   could compose one combined draft — needs explicit per-conversation
   boundaries in the body, still bounded by the 15 MB guard.

**Long term — Phase 3 agent alignment (strategic):**

7. **Agent-drafted follow-up emails.** With explicit per-conversation consent,
   the agent could draft a recap/follow-up email from action items (owners,
   due dates, citations) — draft only, user reviews recipients and sends.
   Recipient identity is the hard question: attendees are deliberately not
   stored, so any “email the participants” feature needs a conscious consent
   design or stays “draft to myself” only. Draft-to-myself is the safe v1.
8. **Entitlement gating.** If sharing becomes a paid capability (e.g.
   audio export for cloud-tier accounts), the Ed25519 entitlement check
   (local_owned / cloud_until) is the natural gate — enforced in the core at
   prepare time, with the UI explaining availability, mirroring how cloud
   transcription is gated.

### What it should NOT do (anti-goals derived from the architecture)

- **Never send automatically.** The compose-and-you-press-Send contract is
  load-bearing for trust and for the consent model; do not add scheduled or
  triggered sends.
- **No Gmail/Outlook OAuth scopes.** Requesting mail OAuth would duplicate the
  account service, expand the cloud surface, and contradict the local-first
  promise; NSSharingService already reaches any configured mail account.
- **No recipient history or address book storage.** Calendar attendees are
  excluded from snapshots for a reason; building recipient memory would
  create a new sensitive data class for marginal benefit.
- **No bypass of the 15 MB guard** by auto-uploading to hosted transfer
  services — that silently moves meeting content to a third party.
- **No export assembly in the shell.** Keep rendering/mixing in the Python
  core (platform portability, single validation point, testable without AppKit).

### Summary

The current delivery is a tight, privacy-first v1 with unusually strong
temporary-file security (dual validation on both sides of the protocol) and
honest bounds. The highest-value next steps are cheap: calendar-linked
context in drafts (item 1) and post-send cleanup (item 2) — both improve the
privacy story rather than expand it. Generalizing to an export framework
(item 4) is the right bridge to Phase 3 agent-drafted actions, provided
draft-only and consent-first semantics are kept.
