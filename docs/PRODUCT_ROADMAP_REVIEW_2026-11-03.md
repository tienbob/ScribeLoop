# ScribeLoop — implementation plan for review

> Next feature delivery plan (2026-11-10): [Meeting workspace delivery](MEETING_WORKSPACE_DELIVERY_PLAN.md), covering search, cited chat, templates, tasks, personal notes and follow-up integrations with per-feature finish gates and flows.


Updated: 3 November 2026. Latest decisions: remove persistent tasks and Brief summaries; prioritize offline Ollama; email shares Summary (default), Transcript or Audio; Calendar workflow approved after research. This revision changes the plan, not external accounts, messages or billing.

The baseline is the current working tree and installed 0.1.14 app, including
uncommitted work. “Implemented” below does not mean merged into `main`, included
in the older shared DMG, or accepted on every supported Mac.

**Implementation update:** the Google account/session/entitlement foundation and
native account UI are now implemented in the working tree for build 0.1.15.
The supplied client ID is configured. HTTPS account hosting/signing configuration
and live Google acceptance remain pending; checkout and paid enforcement belong
to the later billing phase. See [implementation and setup](GOOGLE_ACCOUNT_IMPLEMENTATION.md).

**Current scope:** account-service deployment is deferred at the user's request.
Follow-live transcript scrolling is implemented for build 0.1.16; see
[implementation and validation](TRANSCRIPT_FOLLOW_LIVE_2026-11-03.md).

**Latest implementation:** Ollama functional fixes are implemented for 0.1.17:
draft-model checks with inline feedback, live settings reconfiguration, text-only
transcript retention and local structured notes. The synthetic encrypted
capture → Stop → transcript → notes check passed with installed Ollama/models.
See [functional evidence and limits](OLLAMA_FLOW_2026-11-03.md). The user explicitly
excluded local performance benchmarking. The Calendar workflow is now approved
as Today/Upcoming → Open meeting → existing recording popup → event association;
ScribeLoop opens a meeting link and observes the call, rather than placing calls.

## 1. What is already present

| Requested capability | Current evidence | Work still needed |
| --- | --- | --- |
| Authentication | No ScribeLoop account/login or entitlement service found. | Add Google OAuth/OpenID Connect login to associate purchases and subscriptions with a user. |
| Calendar | EventKit Today/Upcoming, validated Open meeting links and explicit conversation association implemented for 0.1.18. | Real calendar permission/sync and supported-OS acceptance. |
| Popup when joining | Existing activity-based possible-call detection and recording review. Automatic detection requires macOS 14.2+. | Existing feature accepted; skip new popup work. |
| Online and offline meetings | Meeting-app/system audio + mic; mic-only recording for in-person meetings. | Clear meeting modes and setup. Internet-free processing is a separate requirement. |
| Auto-scroll | Follow-live mode follows partial/final speech, pauses while reading earlier, and resumes with Latest. | Live-call visual acceptance on supported Macs. |
| Multiple languages | Searchable language checkboxes, English/Vietnamese defaults, Deepgram hints and vocabulary. | Representative accuracy acceptance and reusable language presets. |
| Pause / Continue | Implemented; user reported a successful live test. | Retain regression coverage and verify device-switch interaction. |
| Chat bubbles | Transcript bubbles, source labels, timestamps and speaker naming implemented. | Partial/final consistency and accessibility polish. This is distinct from an agent conversation. |
| Email | Summary (default), Transcript and Audio previews, native email-compose handoff, and save/copy fallback implemented in 0.1.24 with combined selections. | Real mail-client/account compatibility and delivery acceptance. |
| To-do list | Existing notes can contain action items. | Removed from planned feature work; no persistent task manager. |
| Short summary | Existing overview and editable summary prompt. | Separate Brief view and length preference skipped; use the current prompt. |
| Agent | No app action-execution layer found. | Pending clarification of tool use; defer implementation. |
| Sharing | Markdown export exists. | Native Share flow, format/content selection; hosted links are a separate project. |
| Subscription / payment | No checkout/license backend found. New approved prices replace the previous $1 decision. | $3 once for all models on-device; $5/month for cloud use; Google-linked entitlements. |
| User customization | Providers/models, summary instructions, language hints, vocabulary and microphone processing exist. Local Ollama batch path is present. | New customization scope pending clarification; retain existing settings. |

Evidence: [native UI](../shell/macos/Desktop/DesktopApp.swift),
[desktop state/commands](../shell/macos/Desktop/DesktopClient.swift),
[Preferences](../shell/macos/Desktop/Preferences.swift),
[settings](../core/scribeloop_core/api/desktop_settings.py),
[summary schema](../core/scribeloop_core/llm/schemas.py),
[server workflows](../core/scribeloop_core/api/server.py),
[local transcription](LOCAL_TRANSCRIPTION.md), and
[pricing decision](PRICING_AND_MODEL_ACCESS.md).

Live evidence from 2 November: a roughly 60-second microphone test ended as
Completed with `manual_stop`, 10 segments, and automatic Gemini notes. The user
separately confirmed Pause/Continue. A real default-input switch during recording
remains unverified. No claim of transcript accuracy follows from those checks.

## 2. Review decisions and remaining questions

| Decision | Review outcome | Scope / remaining question |
| --- | --- | --- |
| Authentication | **Added:** Google OAuth/OpenID Connect login. | Track purchased/subscribed users; sign-in grants no Gmail/Google Calendar access. |
| Calendar source | macOS Calendar through EventKit. Workflow approved and implemented. | No direct Google/Outlook connector or recording bot. |
| Offline processing | **Accepted:** existing Ollama path and separate runtime. | Both ASR and notes models must run on-device for the $3 local plan; first setup/download and purchase activation need internet. |
| Join popup | **Skip:** already implemented. | Preserve existing consent and OS behavior; no replacement popup project. |
| Scroll behavior | **Accepted:** recommended follow-live behavior. | Follow at the bottom; preserve position while reading earlier speech; Latest resumes following. |
| Email | **Accepted:** share Summary (default), Transcript or Audio through an editable mail-app draft. | Direct Gmail/Outlook sending remains deferred. |
| Agent scope / tool use | **Pending — defer.** | Clarify the allowed tools and actions before implementing the Assistant/tool registry. |
| Sharing | **Still proposed; no explicit review decision supplied.** | Keep native sharing proposal for review; hosted links remain outside committed scope. |
| Commercial model | **Accepted:** $3 one-time local / $5 per month cloud. | Replaces $1 pricing. Cloud usage allowance, included provider costs versus BYOK, and mixed-mode details still need definition. |
| Customization | **Pending — defer.** | Do not implement new profiles, preset import/export or workflow rules until clarified. Existing preferences remain available. |

Keep unresolved decisions visibly open. Approve each phase before starting it;
approving this roadmap should not implicitly authorize paid provider tests or
external sends.

## 3. Shared technical approach

- Extend the existing SwiftUI app and bundled Python core; preserve Apple Silicon/macOS 13 support. Gate newer Apple APIs by availability.
- Keep meeting content in the encrypted workspace and account tokens in Keychain. Add Google-backed ScribeLoop identity for purchase/restore and subscription tracking. Initial login/activation needs internet; a valid activated $3 local license must support offline processing afterward.
- Add versioned, atomic database migrations as each phase needs them. Do not reserve migration numbers in advance while other work is uncommitted.
- Add bounded commands/events to the existing authenticated desktop protocol. Extend the receipt/state recovery mechanism rather than introducing a second command channel.
- Keep integrations and model calls outside the capture path. A calendar, mail, model or license-service failure must not block Stop or corrupt saved audio.
- Snapshot existing meeting settings at recording startup. Later settings changes apply to future work or an explicit rerun. New named profiles remain deferred with customization.

### Account foundation — Google login and purchase tracking

**Approved feature; proposed implementation:** use Google OpenID Connect through
the desktop authorization-code flow with PKCE in the system browser. Validate
`state` and `nonce`; use a registered desktop client and a short-lived loopback
callback. Request only `openid`, `email`, and `profile` for sign-in. Google login
is separate from macOS Calendar permission and from email composition.

The account service validates Google's ID-token signature, issuer, audience and
expiry using a maintained verifier. Map the verified Google `sub` to an internal
user ID; email is display/contact data, not the purchase identity. Exchange for
ScribeLoop sessions and keep refresh credentials in Keychain. A client-side
Google login alone does not prove payment. See [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
and [Google identity validation](https://developers.google.com/identity/openid-connect/openid-connect).

Proposed server records: `users`, `auth_identities`, `purchases`, `subscriptions`,
`entitlements`, and deduplicated `billing_events`. Link checkout to the internal
user through a server-created checkout session, never a client-provided paid
flag or an unverified email match. Payment webhooks are the authority for paid
state. Minimal API: sign-in/session exchange, current account/entitlements,
create checkout, customer portal, purchase restore, and verified webhook receipt.

Keep purchase/account metadata in this service; recordings, transcripts and
notes stay local by default. Login on another Mac restores entitlements, not
meeting content. Account switching/logout must preserve local data and keep
different users' paid entitlements separate. Google login is not a replacement
for workspace encryption or macOS recording permissions.

**Acceptance:**

- [ ] First login, returning login, cancellation, invalid state/nonce, expired/wrong-audience tokens and unavailable Google login are covered.
- [ ] A new login is unpaid until a verified purchase/subscription grants access.
- [ ] Email changes do not lose a purchase; switching Google accounts does not transfer one.
- [ ] Purchase restore works on a fresh Mac after login without uploading recordings.
- [ ] Tokens stay out of logs/exports; logout preserves the encrypted workspace.
- [ ] Previously activated local use works without Google or billing-service availability.

## 4. Phase A — finish the meeting experience

### A1. Follow-live scrolling

**Implemented:** native viewport observation, explicit follow state, unread speech
indicator, stable bottom anchor and page-boundary/request-race handling. Native
state and offscreen AppKit regression tests pass. Full live-call visual acceptance
remains separate from these silent checks.

**Solution:** add `Following live` / `Reading earlier` state and an unobtrusive
“New speech — jump to latest” control. Detect whether the viewport is near the
bottom using geometry/scroll-view observation compatible with macOS 13. Follow
appended final segments and growing partial text only while following is enabled.
User scrolling upward or opening a citation disables following; Latest enables it.

Preserve the visible segment and its intra-row offset during layout changes.
Keep manual Earlier/Later pages stable. In follow mode, handle the 100-row page
boundary explicitly so a new page does not replace the visible content and jump
to its top. Use a stable bottom anchor after partial speech. Coalesce updates
instead of animating every token; bound the displayed transcript window.

**Acceptance:**

- [ ] New speech remains visible at the bottom, including partial-to-final transitions.
- [ ] Reading an older line stays in place; a new-speech indicator appears.
- [ ] Latest reaches the bottom, including at 100/101 and 200/201 segments.
- [ ] Citations, page changes, meeting changes and transcript replacement have predictable reset rules.
- [ ] Rapid updates do not cause visible flicker or unbounded view/memory growth.

**Touch points:** `DesktopApp.swift`, `DesktopClient.swift`, native regression harness.

### A2. Meeting modes, bubbles, language validation and regression acceptance

**Solution:** label setup as **Online meeting** (selected app + mic) and
**In-person meeting** (mic only), retaining an advanced system-audio option.
Keep the selected capture scope explicit. Show processing as Cloud live or Local
after Stop; an in-person meeting can still use a cloud provider.

Retain the existing Pause/Continue implementation. Give provisional speech the
same bubble structure as final speech, with a provisional indicator. Align by
audio source and show speaker labels without claiming that every mic segment is
the same person. Keep existing language selection, including English + Vietnamese;
provider/model support stays separate from the available hint list. New named
language presets are deferred with customization.

**Acceptance:**

- [ ] Online and in-person recordings both Stop, save, and generate authorized notes.
- [ ] Pause excludes both processed/raw microphone audio and meeting audio; Continue keeps the same session.
- [ ] Pause, device changes, network loss, Stop and notes generation are tested together.
- [ ] Headset/default-input changes continue within the supported recovery window; explicit device selection stays pinned.
- [ ] English, Vietnamese and mixed-language reference clips are human-corrected before word-error comparisons. Additional languages are labeled by tested support, not assumed accuracy.
- [ ] Bubbles and controls pass keyboard/VoiceOver review on supported macOS versions.

**Accepted internet-free path — Ollama:** download/setup happens in advance;
disconnect the network and complete capture → local ASR → local notes. No cloud
fallback, model download or hidden upload is permitted during that test. If the
local runtime/model is unavailable, explain the setup requirement and retain the
recording. The user accepts the separate Ollama installation; a tiny bundled
model is not required. Local batch transcription still begins after Stop.

## 5. Removed scope; customization deferred

### B1. Brief summary — SKIP

Do not add a separate Brief view, length preference or extra generation flow.
Users can control summary style and length through the existing editable prompt.
Keep the current overview, citations and full notes.

### B2. Persistent to-do list — REMOVED

Do not add task tables, checkboxes, task editing, cross-meeting task views or
suggestion acceptance. Existing action items inside generated notes remain.

### B3. User customization — PENDING, excluded from implementation order

The user needs clarification before selecting this scope. Keep the design below
as a discussion proposal only. Do not build named profiles, import/export or
workflow automation yet; retain the current preferences.

**Solution:** create named **Meeting profiles** containing language hints,
vocabulary, microphone processing, selected ASR/notes provider, summary length,
instructions and section preferences. Include defaults for a bilingual meeting,
interview and in-person notes. Let users override a profile in recording review.

Store credential references rather than keys in profiles. Export/import a
versioned, validated profile JSON containing no credentials. Keep factual and
citation constraints mandatory. Later workflow rules choose from implemented
actions selected in a later scope review, with external actions remaining
drafts until approved. No arbitrary shell/Python execution in the first release.

**Acceptance:**

- [ ] Create/duplicate/edit/delete profiles, restore defaults and validate imports.
- [ ] A running meeting keeps its original profile snapshot.
- [ ] Export contains no keys, account tokens, recordings or meeting content.
- [ ] Malformed/unsupported profiles fail clearly; customization cannot disable source validation.

## 6. Phase C — macOS Calendar

### C1. Calendar read integration

**Status:** the user approved Today/Upcoming → Open meeting → existing popup → event association. Implemented for 0.1.18; see [usage, validation and limits](MACOS_CALENDAR.md). Real permission/sync and supported-OS acceptance remain open. [Competitor research](CALENDAR_RESEARCH_2026-11-03.md) informed the scope.

**Selected source:** macOS Calendar, with a proposed app-owned Swift `CalendarCoordinator` using EventKit.
Start with selected calendars already available in macOS Calendar. Request access
only when the user enables calendar integration; filter to selected calendars and
a bounded upcoming range. EventKit's OS permission can cover more calendars than
the app selection, so explain the distinction and retain only required fields.

On macOS 14+, use the full-event access API for reading; use the compatible
legacy authorization path on macOS 13. Follow [Apple's EventKit migration guidance](https://developer.apple.com/documentation/technotes/tn3153-adopting-api-changes-for-eventkit-in-ios-macos-and-watchos).

Show Today/Upcoming, time zone, title and a validated Join link. Associate a
recording with a calendar occurrence only when the match is unambiguous or the
user chooses it. Model recurring occurrences separately; identifiers can change
after resync, so reconcile rather than treating provider IDs as permanent truth.
Keep event details local unless the user chooses them as model context.

**Acceptance:**

- [ ] Denied/revoked calendar access leaves manual recording usable.
- [ ] Recurrence, cancellations, updates, overlapping meetings, time-zone changes and DST are covered.
- [ ] Users can select calendars and see which event a recording is linked to.
- [ ] Cached events are marked as cached when sync is unavailable.
- [ ] Calendar access uses macOS Calendar; Google account login adds no Google Calendar scopes or connector.

### C2. Join → recording popup — SKIP

The user accepts the existing popup and asks to skip further work on it. Preserve
its explicit Record/Not now consent flow and OS limitations. Calendar integration
may display existing meeting links and associate an event with a recording, but
new reminder/snooze triggers, popup redesign and trigger deduplication are removed
from this delivery. Validate Join URLs and avoid regressing the existing popup.

## 7. Phase D — sharing and email

### D1. Native sharing

**Status:** proposal retained for review; the latest user decisions did not
explicitly approve sharing or hosted links.

**Solution:** a broader Share flow remains proposed. Align any later content
selection with Summary, Transcript and Audio; no Brief view or task export. Start with copy, Markdown/text and
PDF export using native rendering, then the macOS share picker. Reuse existing
Markdown escaping and citations. Audio is excluded by default. Explain that an
exported file is a readable copy outside the encrypted workspace.

Use [AppKit sharing services](https://developer.apple.com/documentation/appkit/nssharingservice)
rather than creating a hosted account/storage requirement for this phase. Bound
temporary export lifetime and clean abandoned temporary files on restart.

**Acceptance:**

- [ ] Preview matches the delivered content; speaker labels, Unicode and source timestamps survive export.
- [ ] Canceling creates no external delivery; recipient selection stays under user control.
- [ ] Long transcripts/PDFs paginate correctly and temporary files are cleaned up.
- [ ] Credentials/internal metadata never enter exports; retained-audio rules are respected.

**Hosted sharing, optional later:** a separate backend with authenticated uploads,
per-share access checks, explicit recipient/link settings, expiry, revocation,
deletion and retention. Start without uploading raw audio by default. Revoking a
link cannot revoke copies already downloaded by recipients. Cost and encryption
design require approval before implementation.

### D2. Email

**Implemented in 0.1.24:** [usage, retention and validation](EMAIL_SHARING.md). Mail-client/account acceptance remains separate; no direct sending or provider scopes were added.

**Accepted scope:** email is conversation sharing. Present three content choices:
**Summary** (selected by default), **Transcript**, **Audio**. Independent checkboxes
allow any combination in one draft; selected files are attached automatically. Open an editable draft in the
user's configured mail app. Opening the draft never sends it.

Summary uses saved notes without regeneration; Transcript exports the saved
transcript with speaker labels and timestamps; Audio attaches a playable export
of retained conversation audio, never the encrypted workspace file. Disable an
unavailable option with a reason. Show attachment size and provide save/copy
fallback when no mail app is available or an attachment cannot be handed off.
Do not upload oversized audio to hosted storage automatically.

Preview the selected content and attachments. Recipients and subject remain
editable; attendees are suggestions only. Readable audio exports need bounded
temporary-file cleanup that does not remove attachments before the mail app has
finished consuming them. Google purchase login adds no Gmail scopes. Use a native
mail composition/share service that supports attachments; `mailto:` alone cannot
implement the Audio option reliably.

If direct Gmail/Outlook sending is approved later, keep drafts local and request
send-only delegated access where supported. Use system-browser OAuth with PKCE,
registered native redirects and Keychain tokens; embed no client secret. Do not
request inbox-reading access merely to send a summary. Provider-side draft APIs
can need broader permissions, so they are a separate scope decision.

Before sending, show the exact account, To/CC/BCC, subject, body and attachments.
Persist operation state. A timeout after a possible send becomes “delivery
uncertain,” with no automatic resend; the user checks Sent before retrying.

**Acceptance:**

- [ ] Exactly three choices are offered; Summary is the initial default.
- [ ] Summary and Transcript use saved results without provider calls; Audio is playable and uses retained audio only.
- [ ] Missing notes/transcript/audio and oversized attachments have clear fallback paths.
- [ ] Draft content and recipients remain editable; opening a draft does not send it.
- [ ] Cancel, mail-app handoff and restart clean up temporary exports without breaking attachments.
- [ ] Direct sending requires approval of the exact message and recipients.
- [ ] Expired/revoked tokens, denied organization consent and ambiguous delivery fail clearly.
- [ ] Duplicate clicks/reconnection do not silently send duplicates.

References: [native-app OAuth](https://www.rfc-editor.org/rfc/rfc8252.html),
[Microsoft delegated permissions](https://learn.microsoft.com/en-us/graph/permissions-reference),
[Gmail send API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send).
Provider verification and organization-admin restrictions are external dependencies.

## 8. Phase E — agent/tool use PENDING

**Decision:** leave this feature pending until the user clarifies tool use.
The following is a design proposal for that discussion, not approved work.
Do not implement the Assistant, tool registry or external action execution yet.

**Solution:** add an **Assistant** panel separate from transcript bubbles. Start
with questions over the selected meeting: “What did we decide?”, “Find the source,”
and “Draft a follow-up.” Retrieve a bounded set of local notes/segments and cite
answers. Expanding to other meetings is explicit. Cloud models receive only the
reviewed/selected scope, following the app's processing agreement.

Build a small Python tool registry around the existing provider layer. Each tool
has a validated input/output schema and is classified as local read, local
change, external draft or external write. Initial tools: search this meeting,
read cited notes, draft email and draft calendar event. Persistent tasks are excluded. No shell,
browser automation, arbitrary URL fetching or user code execution in version one.

After a separate scope approval and integration acceptance, consider approval-gated
email sending and calendar writes. An approval binds to the displayed
arguments, account, recipients and source revision; changing them invalidates it.
Transcript/calendar/email content is untrusted data and never grants tool access.
Use bounded tool steps/time/tokens, cancellation, an encrypted action journal and
the existing request recovery pattern. External side effects cannot always be
rolled back; use explicit uncertainty states rather than claiming exactly-once
delivery from a provider that does not guarantee it.

**Acceptance:**

- [ ] Answers cite current meeting sources and admit missing information.
- [ ] Instructions embedded in a transcript cannot send email or alter calendars.
- [ ] Drafts have zero external side effects; writes use the exact approved values.
- [ ] Stale approvals, tool loops, provider failure, cancellation and uncertain results are covered.
- [ ] The agent cannot broaden account scopes, exceed its budget, or interfere with recording Stop.

## 9. Phase F — $3 one-time Local / $5-per-month Cloud

**Approved pricing replaces the previous $1 model:**

| Plan | Price | Processing requirement |
| --- | --- | --- |
| Local | **US$3 one-time purchase**, with no recurring Local-plan fee | Every processing model, including ASR and notes/LLM, runs on the user's device. The Ollama path is accepted. |
| Cloud | **US$5 per month** | Cloud-model use. Exact included usage/provider funding is still to be clarified. |

Treat these as alternative purchase options; no additional $3 upfront charge is
assumed for the Cloud subscription. A user may own a Local purchase alongside a
Cloud subscription; ending that subscription keeps the purchased Local access.
Subscription ownership alone does not establish a permanent Local purchase.

**Entitlement implementation:** use Google login/account identity from the account
foundation. A server-created hosted checkout binds the purchase to that user.
Evaluate a merchant-of-record checkout such as Paddle after seller eligibility
and economics are checked. Payment webhooks verify, deduplicate and reconcile
purchase/subscription state. Issue signed entitlement records; the app verifies
them with a public key. Payment secrets/private signing keys stay server-side.

The $3 Local license is perpetual and cached for offline verification after
initial online login/purchase/activation. No repeated Google login or monthly
check is required to keep that activated Local purchase usable. Restoring on a
new Mac requires internet and login. Account/purchase metadata can reach Google
and the billing service; local-plan audio, transcripts and model processing stay
on-device. “On-device processing” does not mean payment activation is offline.

Show the active ASR and notes model locations before recording. Proposed mode
rule: any cloud ASR or cloud notes component requires Cloud entitlement, including
mixed local/cloud processing. A Local-only setup must never silently fall back
to cloud. Remote self-hosted servers do not satisfy “on-device only”; confirm a
separate rule if they should be offered. Enforce the approved rule before model
dispatch, not just in the UI. A license refresh must not abruptly terminate a
recording that already started with valid authorization.

Implement Cloud renewal, cancellation, receipts, customer portal, refund/dispute
handling and ordered reconciliation of duplicate/replayed/out-of-order webhooks.
Specify a bounded subscription entitlement cache/grace policy. Expired Cloud
access prevents new cloud work while keeping saved content readable/exportable;
users with a Local purchase can continue local work.

**Still open before completing Cloud billing/processing:**

- Does $5/month include ScribeLoop-funded ASR/LLM usage, or do users still supply/pay their own API keys?
- If provider usage is included: which models, minutes/usage caps, metering, overage behavior and cost ceiling? Unlimited usage is not assumed.
- Confirm mixed-mode/remote self-hosting eligibility, subscription grace, device/activation limits and purchase/refund rules.

If ScribeLoop funds models, add a separately designed authenticated model gateway
and durable per-account usage limits; no shared provider secret belongs in the
desktop bundle. This gateway is conditional scope, not an approved dependency
for BYOK. The Cloud offer must explain these terms before checkout.

**Acceptance:**

- [x] Local price is $3 **once**; Cloud price is $5 **per month**.
- [ ] Provider funding/usage policy and seller eligibility are settled before Cloud checkout goes live.
- [ ] Google-linked purchase/restore, subscription expiry and account switching grant the correct plan.
- [ ] Sandbox checkout, duplicate/out-of-order webhooks, cancellation, refund and service outage are tested.
- [ ] Checkout redirects/client flags cannot grant a paid entitlement.
- [ ] Activated $3 Local processing completes ASR and notes offline without cloud fallback.
- [ ] Cloud expiry preserves owned Local access and saved-content access/export.

Reference: [Paddle webhook lifecycle and verification](https://developer.paddle.com/webhooks/).
Seller onboarding and payment sandbox accounts are prerequisites; no vendor is
selected or account created by this draft.

## 10. Suggested implementation order and review gates

| Priority | Delivery | Status / dependency |
| --- | --- | --- |
| 1 | Ollama offline capture → local ASR → local notes | Next priority. Verify real audio-model/API support first; setup models in advance; prove no network or cloud fallback. |
| 2 | Meeting modes, bubbles/languages and pause/device/Stop regression acceptance | Follow-live scrolling is finished; keep remaining acceptance explicit. |
| Implemented | Email conversation sharing | Summary default with Transcript and Audio checkboxes; combined attachments, optional preview, native draft handoff and save/copy fallback. |
| Implemented | macOS Calendar | Today/Upcoming, Open meeting and explicit event link; real-calendar acceptance remains. |
| Deferred | Live Google login/account hosting | Foundation exists; HTTPS deployment explicitly deferred. |
| Deferred | $3 one-time Local / $5 monthly Cloud checkout | Account hosting, seller setup and Cloud usage/BYOK policy unresolved. |
| Removed | Persistent to-do list | No task manager work. |
| Skip | Separate Brief summary | Existing prompt provides summary customization. |
| Pending | Agent tools and new customization | Clarify scope first. |
| Proposed | Broader native sharing / hosted links | Email sharing is accepted; other sharing remains unapproved. |

The offline acceptance gate is functional as well as private: demonstrate that
installed Ollama and the selected model actually accept audio through the API
used by ScribeLoop. Existing code/docs and mocked tests are not proof. If this
fails, record the exact incompatibility and review a local ASR runtime solution
before claiming the Local path works. Measure processing time, memory and usable
English/Vietnamese output. Keep speakers off; no fresh provider billing is needed.

Every delivery should include a small review package: mockup/flow, data or
permission changes, implementation diff, focused tests, actual acceptance results
and remaining limits. Use synthetic data first. Real microphone/cloud/provider
tests need the appropriate authorization; speakers remain off under the current
user constraint. Do not mark physical device switching or untested languages as
accepted from mocks alone.

Before broader distribution, separately finish Developer ID/notarization when an
account is available, minimum-macOS/clean-Mac acceptance, upgrade/migration checks
and installer regeneration. The app-scoped permission reset is a local ad-hoc
development workflow, not an operation to run automatically on customers' Macs.

## 11. Decision record and remaining review

- [x] Add Google OAuth/OpenID Connect login to track purchases and subscriptions.
- [x] Use macOS Calendar through EventKit.
- [x] Accept Ollama and its separate runtime for on-device processing.
- [x] Skip new join-popup work; preserve the existing feature.
- [x] Accept follow-live scrolling with position preservation while reading earlier speech.
- [x] Email shares Summary (default), Transcript or Audio through an editable mail-app draft.
- [x] Remove the persistent to-do list.
- [x] Skip separate Brief summary; retain the editable summary prompt.
- [x] Prioritize real offline Ollama validation.
- [x] Research Calendar workflows, then accept Today/Upcoming → Open meeting → existing recording popup → event association.
- [x] Set Local to **US$3 one time**, with no recurring Local-plan fee.
- [x] Set Cloud to **US$5 per month**.
- [ ] Clarify Cloud provider funding versus BYOK, usage limits and mixed/remote processing rules.
- [ ] Review account-service deployment, seller eligibility, activation limits and billing grace/refund behavior.
- [ ] Decide sharing scope; no explicit decision was supplied in this review.
- [ ] **Pending:** clarify agent tools before implementation.
- [ ] **Pending:** clarify new customization before implementation.
- [ ] Complete real Calendar permission/sync and supported-macOS acceptance.
