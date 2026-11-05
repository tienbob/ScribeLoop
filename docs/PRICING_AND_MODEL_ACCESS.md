# Pricing, Google login, and model access

Product decision updated 3 November 2026. This replaces the earlier $1 one-time
BYOK/no-subscription plan. These are approved target prices, not implemented
checkout or entitlements in the current internal build.

## Plans

| Plan | Price | Processing |
| --- | --- | --- |
| Local | **US$3 one-time purchase**; no recurring Local-plan fee | All ASR and notes/LLM models run on the user’s device. The existing Ollama path and its separate runtime are accepted. |
| Cloud | **US$5 per month** | Cloud-model processing. Included services/usage allowances and provider funding versus BYOK remain to be defined. |

Treat these as alternative options; do not assume an additional $3 upfront charge
for the Cloud subscription. A separately owned Local purchase survives Cloud
subscription cancellation. Cloud subscription ownership alone does not grant a
permanent Local purchase.

The Local plan covers on-device processing, not a remote self-hosted model server.
Proposed eligibility for mixed local/cloud processing is the Cloud plan; confirm
this and remote-server rules before enforcing billing. A Local-only configuration
must not silently fall back to cloud.

## Google login and ownership

Add Google OAuth/OpenID Connect login to associate purchases and subscriptions
with a stable internal user identity. Verify Google identity server-side; use its
verified subject identifier rather than an email address as the ownership key.
Signed payment webhooks determine paid entitlements, not successful login or a
client-side paid flag.

Initial login, purchase, activation, and restore on a new Mac require internet.
After activation, a signed perpetual Local entitlement permits offline use
without monthly checks or repeated Google login. Account and purchase metadata
can reach Google/the billing service; Local audio, transcripts and model
processing stay on-device. Sign-in does not grant Gmail or Google Calendar access.

Cloud renewal, expiration, cancellation and payment recovery need server-backed
entitlement tracking and a defined cache/grace policy. Saved recordings and notes
remain readable/exportable after expiration; owned Local access remains valid.
Google login restores purchases on another Mac, not the contents of its workspace.

## Open Cloud offer details

- Does $5/month include ScribeLoop-funded ASR/LLM usage, or is it an app subscription while users separately fund their own provider API keys?
- Which models, usage/minute caps, cost ceilings and overage behavior apply if model usage is included? Unlimited use is not assumed.
- Confirm mixed-mode/remote-hosting classification, activation/device limits, subscription grace and refund rules.
- Select an eligible payment provider and verify the economics before checkout goes live.

No included AI credits, unlimited processing, or shared provider credentials are
promised by the price decision alone. If ScribeLoop funds cloud models, an
authenticated, metered model gateway needs a separate design; provider secrets
must remain outside the desktop bundle.

## Implementation and acceptance

The current internal app has Deepgram cloud ASR, configurable notes providers,
and a local Ollama batch transcription path. The Local plan needs both local ASR
and a local notes model configured and accepted with the network disconnected.
First-time model downloads happen before that offline test. The Google account
foundation is implemented, with hosting and live sign-in acceptance pending;
payment checkout and entitlement enforcement remain planned work. See
[account implementation and setup](GOOGLE_ACCOUNT_IMPLEMENTATION.md).

See the [reviewed implementation plan](PRODUCT_ROADMAP_REVIEW_2026-11-03.md) for
Google login, account records, checkout/webhook handling, offline license
verification, acceptance checks and the pending commercial details.
