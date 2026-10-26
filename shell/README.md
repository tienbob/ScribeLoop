# Desktop app shell

The current working UI is the Python/FastAPI browser development client. No native
desktop shell or installer is implemented in this directory yet. The app core uses
cloud-only Deepgram streaming and OpenAI summaries; it does not run local ML models.

The proposed next step is a native SwiftUI macOS app with an app-managed bundled
Python core and the existing Swift capture/observer helpers. See the
[macOS app implementation plan](../docs/MACOS_APP_IMPLEMENTATION_PLAN.md).

Start with a minimal packaged `.app`/permission/lifecycle spike, then native UI
parity. Release authentication is mandatory and invisible to users; the keyless
web demo is not the desktop security model. Signing/notarization remains a later
release gate under the user's existing local-development deferral.
