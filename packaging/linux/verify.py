"""Installed-package smoke in a disposable X11/DBus/Secret Service session.

No provider calls. A virtual microphone and output monitor validate GStreamer;
the real desktop window must authenticate, then close and reap its backend.
"""
from __future__ import annotations

import os
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
        try:
            for frame in source.frames():
                assert frame.pcm.size > 0
                seen.add(frame.stream)
                if abs(frame.pcm).max() > .05:
                    signal.add(frame.stream)
                count += 1
                if count >= 30 and (scope == 'microphone' or len(seen) == 2):
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
    gi.require_version('WebKit2', '4.1')
    from gi.repository import GLib, Gtk, WebKit2
    from scribeloop_core.api.linux import desktop as launch
    original_main = Gtk.main
    deadline = time.monotonic() + 40
    verified = []
    pending = False
    failure = []
    export_started = False
    exported = Path(os.environ['XDG_DATA_HOME'])/'export-smoke.md'

    def result(view, response, *unused):
        nonlocal pending, export_started
        pending = False
        try:
            text = view.run_javascript_finish(response).get_js_value().to_string()
            if 'Connected locally' in text and not export_started:
                export_started = True
                view.run_javascript("const a=document.createElement('a');"
                        "a.href=URL.createObjectURL(new Blob(['ScribeLoop export smoke']));"
                        "a.download='export-smoke.md'; document.body.append(a); a.click(); a.remove();",
                        None, None, None)
        except Exception as exc:  # noqa: BLE001 - report only exception type
            failure.append(type(exc).__name__)
            Gtk.main_quit()

    def check():
        nonlocal pending
        if time.monotonic() > deadline:
            failure.append('Export Save dialog timed out' if export_started else 'Desktop authentication timed out')
            Gtk.main_quit()
            return False
        if export_started:
            for window in Gtk.Window.list_toplevels():
                if isinstance(window, Gtk.FileChooserDialog):
                    window.set_current_folder(str(exported.parent))
                    window.set_current_name(exported.name)
                    window.response(Gtk.ResponseType.ACCEPT)
            if exported.exists():
                assert exported.read_text() == 'ScribeLoop export smoke'
                verified.append(True)
                Gtk.main_quit()
                return False
        if not pending and not export_started:
            for window in Gtk.Window.list_toplevels():
                view = window.get_child()
                if isinstance(view, WebKit2.WebView):
                    pending = True
                    view.run_javascript("document.getElementById('connection')?.textContent || ''",
                                        None, result, None)
                    break
        return not verified and not failure

    def test_main():
        GLib.timeout_add(250, check)
        original_main()
    Gtk.main = test_main
    launch()
    assert verified and not failure, failure
    workspace = Path(os.environ['XDG_DATA_HOME'])/'ScribeLoop/meetings.sqlcipher'
    assert workspace.is_file()
    assert workspace.read_bytes()[:16] != b'SQLite format 3\x00'
    print('DESKTOP-OK authenticated GTK notebook, encrypted workspace, export and owned shutdown', flush=True)


if __name__ == '__main__':
    capture()
    desktop()
