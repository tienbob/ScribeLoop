# ScribeLoop for Ubuntu 22.04+

Linux preview: a GTK 3 / WebKitGTK desktop window around the shared notebook,
with Deepgram live transcription, saved notes, retained-audio playback and
Markdown exports. Start and stop recordings manually. Choose **microphone only**
or **all system audio plus microphone**. System capture includes all sound from
the output selected when recording starts. Headphones reduce acoustic echo.

Per-application capture and automatic call detection are not implemented on
Linux. A default-device change does not silently switch an active recording;
stop and start a new recording after changing devices. This is a preview, not a
claim of physical microphone, echo or Wayland acceptance on every Ubuntu host.

## Install and use

Use the `.deb` matching **both your Ubuntu release and CPU architecture**:

```sh
sudo apt update
sudo apt install ./scribeloop_0.1.0~preview1+ubuntu22.04_amd64.deb
scribeloop
```

Launch **ScribeLoop** from the application menu. Expand **Provider settings**, add
your Deepgram key, notes provider name, API base URL, model and key, then Save.
Keys are stored through Secret Service, normally GNOME Keyring. Leave key inputs
blank to keep existing keys. Changing the notes endpoint clears the previous
key unless you supply a replacement. The named OpenAI/Gemini/Ollama/LM Studio
presets use their built-in base URLs; use a different provider name for a custom
endpoint.

Accept the processing agreement, open **Recording preferences & manual
recording**, select and confirm the source, then Record. Nothing records on
launch. Quit closes the owned backend and stops capture. Transcription sends
audio to Deepgram; cloud notes send transcript text to your configured provider.
Provider charges apply. Review generated text and citations.

The workspace is `$XDG_DATA_HOME/ScribeLoop` (normally
`~/.local/share/ScribeLoop`). SQLCipher protects the database; audio uses the
existing encrypted recording store. There is no plaintext key fallback. Use a
normal graphical desktop login with an unlocked Secret Service. Headless servers,
root sessions, WSL without a full desktop and locked/missing keyrings are not
supported. Ubuntu's **universe** repository must be enabled for WebKitGTK.

The GTK web view is ephemeral and accepts navigation only to the current
backend's loopback origin. Credentials are not loaded from a repository `.env`.
Exported Markdown is plaintext. Removing the package preserves the workspace
and keyring entries. Do not remove those keys if you want to keep your meetings.

## Build

Build separately on each target Ubuntu release and architecture. Packages vendor
Python dependencies but use Ubuntu's maintained GTK, WebKit and GStreamer stack.
The package restricts Python's minor version to avoid loading incompatible binary
wheels after a distribution upgrade. Install the package for the new Ubuntu
release after upgrading. An amd64 package cannot run on arm64 or vice versa.

From the repository root, with Docker running:

```sh
docker build -f packaging/linux/Dockerfile --build-arg UBUNTU_VERSION=22.04 -t scribeloop-ubuntu:22.04 packaging/linux
mkdir -p dist/linux
docker run --rm -v "$PWD:/src:ro" -v "$PWD/dist/linux:/output" scribeloop-ubuntu:22.04 --output /output
```

Repeat with `24.04` for Ubuntu 24.04. Native Docker builds use your host
architecture; on Apple Silicon add `--platform linux/amd64` to **both** commands
for an Intel/AMD package. Build and test on the actual newer Ubuntu release before
distributing there; “22.04+” is a baseline, not an assertion that all future
libraries have been validated. CI builds 22.04 and 24.04 independently.

For source development, install the apt packages in `Dockerfile`, create a venv
with `python3 -m venv --system-site-packages .venv-linux`, then:

```sh
.venv-linux/bin/pip install -e '.[security,dev]' sqlcipher3==0.6.4
.venv-linux/bin/python -m scribeloop_core.api.linux
```

## Compatibility research

- [Ubuntu Jammy WebKitGTK 4.1 introspection package](https://packages.ubuntu.com/jammy-updates/gir1.2-webkit2-4.1)
  supplies the same GTK 3 API used by this shell on 22.04 and 24.04.
- [GStreamer pulsesrc](https://gstreamer.freedesktop.org/documentation/pulseaudio/pulsesrc.html)
  selects an explicit source device. This works with PulseAudio and PipeWire's
  PulseAudio compatibility server; PipeWire is not forced onto Ubuntu 22.04.
- [GStreamer appsink](https://gstreamer.freedesktop.org/documentation/app/appsink.html)
  supports timed pulls outside realtime callbacks. Capture uses nondropping
  bounded sinks, 16 kHz mono float32 tracks and one pipeline clock.
- [PyInstaller GNU/Linux compatibility guidance](https://www.pyinstaller.org/en/latest/usage.html#making-gnu-linux-apps-forward-compatible)
  explains why native binaries need a compatible baseline. This package instead
  builds against each supported Ubuntu system Python and declares that ABI.

## Release acceptance still needed

CI checks package installation, imports, encrypted storage, generated audio,
startup, authenticated UI and shutdown under a temporary desktop/keyring session.
Before sharing a release, test a physical microphone and output monitor, two-track
alignment, long recordings, device removal, Wayland and X11, suspend/resume,
provider setup, playback/export and reinstall on each supported Ubuntu version.
