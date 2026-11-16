#!/usr/bin/env python3
"""Installed-package smoke in a disposable X11/DBus/Secret Service session.

No provider calls. A virtual microphone and output monitor validate GStreamer;
the real desktop window must authenticate, then close and reap its backend.
"""
from __future__ import annotations

import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/usr/lib/scribeloop')


def capture():
    import gi
    from scribeloop_core.capture.linux_pipewire import (
        LinuxMeetingAudioSource,
        linux_capture_devices,
    )
    from scribeloop_core.meetings.contracts import CaptureSelection
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    subprocess.run(['pulseaudio', '--start', '--exit-idle-time=-1'], check=True)
    for args in [('module-null-sink', 'sink_name=scribeloop_test_output'),
                 ('module-remap-source', 'source_name=scribeloop_test_mic',
                  'master=scribeloop_test_output.monitor')]:
        subprocess.run(['pactl', 'load-module', *args], check=True)
    subprocess.run(['pactl', 'set-default-sink', 'scribeloop_test_output'], check=True)
    subprocess.run(['pactl', 'set-default-source', 'scribeloop_test_mic'], check=True)
    assert linux_capture_devices().available
    tone = Gst.parse_launch('audiotestsrc is-live=true wave=sine freq=440 ! '
                            'audioconvert ! pulsesink device=scribeloop_test_output')
    tone.set_state(Gst.State.PLAYING)
    for scope in ('microphone', 'system'):
        source = LinuxMeetingAudioSource(CaptureSelection(scope=scope,
            microphone_device_id='default'), consent_confirmed=True)
        seen = set()
        count = 0
        signal = set()
        frames = iter(source.frames())
        try:
            for frame in frames:
                assert frame.pcm.size > 0
                seen.add(frame.stream)
                if abs(frame.pcm).max() > .05:
                    signal.add(frame.stream)
                count += 1
                if count >= 30 and (scope == 'microphone' or len(seen) == 2):
                    assert source.request_stop()
                    drained = False
                    for _ in range(1000):
                        try:
                            next(frames)
                        except StopIteration:
                            drained = True
                            break
                    assert drained, 'GStreamer EOS did not finish capture'
                    break
        finally:
            source.close()
        assert seen == ({'mic'} if scope == 'microphone' else {'mic', 'loopback'})
        assert signal == seen, 'Generated tone did not reach each selected track'
    tone.set_state(Gst.State.NULL)
    print('CAPTURE-OK microphone and system tracks', flush=True)


def desktop():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    original_main = Gtk.main
    deadline = time.monotonic() + 40
    verified = []
    failure = []

    def check():
        if time.monotonic() > deadline:
            failure.append("Native notebook window timed out")
            Gtk.main_quit()
            return False
        windows=[window for window in Gtk.Window.list_toplevels() if window.get_title()=="ScribeLoop"]
        if windows:
            verified.append(True)
            Gtk.main_quit()
            return False
        return not verified and not failure

    def test_main():
        GLib.timeout_add(250, check)
        original_main()
    Gtk.main = test_main
    runpy.run_path('/usr/lib/scribeloop/launch.py', run_name='__main__')
    assert verified and not failure, failure
    workspace = Path(os.environ['XDG_DATA_HOME'])/'ScribeLoop/meetings.sqlcipher'
    assert workspace.is_file()
    assert workspace.read_bytes()[:16] != b'SQLite format 3\x00'
    print('DESKTOP-OK native GTK notebook, encrypted workspace and owned shutdown', flush=True)


if __name__ == '__main__':
    capture()
    desktop()
