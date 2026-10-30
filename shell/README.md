# Desktop app shell

`macos/Desktop/` implements the SwiftUI production client: meeting history,
recording controls, notes and citations, transcript paging, people, encrypted
playback, rename/delete and Preferences. It owns a bundled Python core through
a private authenticated connection. `macos/ScribeLoop/` is the isolated M0 fixture
host. The browser UI remains a development/test client.

The working local app is `dist/macos/ScribeLoop.app`. It uses Deepgram streaming
and explicit saved-audio batch reruns, with configurable notes providers; no local
speech model is bundled. See [launch/build instructions](../packaging/macos/DESKTOP.md)
and the [implementation history](../docs/MACOS_APP_IMPLEMENTATION_PLAN.md).

Developer ID signing, notarization, installer/upgrade acceptance and public
distribution credential protection remain open. The private-demo bundle is
ad-hoc signed. See the [current checklist](../docs/CURRENT_STATUS.md).

Ubuntu uses the GTK/WebKit host in `core/scribeloop_core/api/linux.py` with the shared
notebook UI. See [Linux packaging](../packaging/linux/README.md).
