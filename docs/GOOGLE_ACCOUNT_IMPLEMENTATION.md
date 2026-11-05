# Google sign-in and account foundation

Implemented in the working tree on 3 November 2026. The supplied Google desktop
client ID is public configuration and is included in the desktop build. No Google
client secret, payment secret or entitlement signing private key is bundled.

## Delivered

- System-browser Google authorization-code flow with PKCE, state and a
  server-issued, single-use nonce; bounded loopback callback and cancellation.
- Separate account API verifies Google identity with `google-auth`, maps Google
  subjects to internal users, and returns signed plan ownership. Email changes
  preserve identity; matching email addresses do not merge Google accounts.
- Short-lived opaque access tokens and rotating refresh credentials. Server-side
  credentials are hashes; desktop refresh credentials are in a separate Keychain
  namespace scoped to the account origin and verification key.
- Ed25519-signed account entitlements, including perpetual Local ownership and a
  Cloud end timestamp. A cached, verified Local entitlement can be read offline.
- Account/plan Preferences, browser sign-in, cancel, refresh and sign-out commands.
  Reconnect snapshots contain public account state; credentials stay off the
  desktop websocket. Account errors are visible in Preferences.
- A missing deployment configuration shows setup required and leaves the existing
  internal workspace usable. Merely signing in grants no purchase/subscription.

## Remaining deployment steps

The Google client has been supplied. **The HTTPS account-service origin and its
signing key have not been configured or deployed.** Consequently real Google
sign-in cannot yet finish in the installed app. The full flow has been exercised
with mock services and real cryptographic signatures; this is not a live Google
acceptance result.

1. Choose a server/hosting environment and an HTTPS origin for the account API.
   No paid hosting or domain has been created by this implementation.
2. Install the separate server dependencies. Google verification packages are not
   added to the Mac application's frozen runtime:

   ```sh
   uv pip install --python .venv/bin/python -e '.[accounts]'
   mkdir -p data/accounts
   chmod 700 data/accounts
   .venv/bin/python -m scribeloop_core.accounts --generate-key data/accounts/signing.key
   ```

   This writes the private key with owner-only permissions and prints only its
   public verification key. Keep the private key on the account server with a
   secure backup. `data/` is ignored by Git. Do not replace this key casually:
   previously issued Local licenses depend on its verification key.

3. Copy [the public config example](../packaging/macos/account-config.example.json)
   to `data/accounts/config.json`. Set `api_url` to the actual HTTPS origin and
   `entitlement_public_key` to the printed public key. Keep the supplied client ID.
4. Start the service behind an HTTPS reverse proxy:

   ```sh
   .venv/bin/python -m scribeloop_core.accounts \
     --config data/accounts/config.json \
     --database data/accounts/accounts.sqlite \
     --signing-key data/accounts/signing.key \
     --trust-loopback-proxy
   ```

   The process binds only to `127.0.0.1:8091`. Configure the reverse proxy for the
   chosen public origin, HTTPS, request-size/time limits and per-client rate
   limits. Protect the database/private-key volume, backups and access logs.
   `--trust-loopback-proxy` accepts forwarded client/protocol headers only from
   `127.0.0.1`. Omit it for direct requests; forwarding headers are then ignored.
   The proxy must replace client-supplied headers, for example in Nginx:

   ```nginx
   proxy_pass http://127.0.0.1:8091;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_set_header X-Forwarded-Proto $scheme;
   ```

   Only terminate public TLS at this trusted same-host proxy. Loopback trust also
   trusts other local processes that can reach this port; it does not authenticate
   the proxy executable. Use host/process isolation if that stronger boundary is
   required. Do not configure wildcard forwarding trust.

   The launcher explicitly runs one worker. Its in-memory 60/min/client-IP limiter
   is supplemental, resets at restart and is not shared across processes/replicas;
   clients behind the same NAT still share an IP limit. Public-edge rate limits
   remain required. Establish shared limiting before adding replicas/workers.

   Do not expose the separate desktop core or a signing-key file through the proxy.
   Multi-worker/database scaling and deployment hardening need acceptance before
   a public paid launch; the current service uses a dedicated SQLite database.

5. Rebuild the desktop with the public API origin and public verification key:

   ```sh
   SCRIBELOOP_ACCOUNT_API_URL=https://accounts.your-domain.example \
   SCRIBELOOP_ENTITLEMENT_PUBLIC_KEY=YOUR_PUBLIC_VERIFICATION_KEY \
     build/macos/venv13/bin/python packaging/macos/build_desktop.py
   ```

   Replace both placeholders. The existing local rebuild resets ScribeLoop's
   Screen Recording grant; approve the refreshed build and run the silent access
   check. Preview builds do not include production account configuration.
6. Complete one real sign-in with an allowed Google test user. Verify logout,
   cancellation, account switching and restore before publishing the app.

Google OAuth scopes are only `openid email profile`. The browser callback is a
temporary local loopback address, distinct from the account API's HTTPS origin.
Google Calendar and Gmail access are not requested. macOS Calendar integration
remains a later EventKit implementation.

## Payment and release boundaries

Prices remain **$3 once for Local** and **$5/month for Cloud**. There is no checkout,
payment webhook adapter, model gateway or paid-feature enforcement in this phase.
Every new account starts unpaid. There is no public endpoint that grants paid
ownership and no developer “mark paid” UI. A later billing adapter must populate
ownership only after verifying payment events; Cloud usage/BYOK terms remain open.

No meeting audio/transcript or workspace keys are uploaded to the account service.
Signing out removes the desktop account credential record and attempts remote
session revocation; a failed network call cannot promise server-side logout.
Saved encrypted meetings are preserved. Offline purchase enforcement, refunds,
key rotation, account/device limits and entitlement migration need release review
when checkout is implemented.

## Verification

Tests use synthetic identities, isolated account databases and local loopback
callbacks. They cover subject/email separation, unpaid default, hashed credentials,
challenge replay/expiry, refresh rotation, logout, signed entitlement tampering,
offline cache, callback state/PKCE, immediate cancellation, desktop receipts and
redacted errors. Real Google's verifier is additionally tested with locally signed
RSA tokens and a synthetic certificate response for issuer/audience/expiry/signature.
No live Google login, microphone capture, provider upload or payment is performed
by these tests.

The full regression run passed **667 tests**, with two optional modules skipped.
Five additional real Google-verifier tests passed in the server development
environment. Focused account/protocol/packaging checks passed after the final
validation-error redaction change. Ruff, whitespace checks, SwiftUI compilation
and the 38-binary bundle audit passed.

The canonical app was rebuilt in place as **0.1.15 (16)**. Hands-on inspection
confirmed the Account and plan section, disabled setup-required sign-in button,
correct $3-once/$5-month pricing and nine restored conversations. The supplied
client ID is present in public bundle configuration; Google server verification
packages are absent from the frozen desktop runtime. The build performed the
requested app-scoped ScreenCapture reset; recording access must be re-approved
locally before the next recording. No microphone or speaker was used for this phase.

References: [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect).
