"""Capture: AudioSource protocol + platform sources.

- `source.py` — the protocol + dev/test sources (WAV replay, silence)
- `macos_client.py` — talks to the Swift Core Audio tap helper (macOS only)
- `linux_pipewire.py` — GStreamer pipewiresrc/pulsesrc → appsink (Linux only)
- `native/` — Swift helpers that own the OS realtime callbacks
"""
