"""Ubuntu GTK desktop host and owned authenticated loopback backend."""
from __future__ import annotations

import asyncio
import json
import os
import select
import socket
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .desktop import bootstrap
from .desktop_fixture import read_control
from .desktop_settings import DesktopSettings
from .server import create_app


def make_app(request):
    from keyring.backends.SecretService import Keyring

    from ..capture.linux_pipewire import LinuxMeetingAudioSource, linux_capture_devices
    from ..meetings.service import DiscoveryService
    settings = DesktopSettings(backend=Keyring())
    return create_app(database=Path(request['database']), storage='encrypted',
        launch_token=request['token'], settings=settings,
        source_factory=LinuxMeetingAudioSource, cloud_config=settings.cloud(),
        summary_provider=settings.value.summary(),
        discovery_factory=lambda publish: DiscoveryService(publish,
            devices_provider=linux_capture_devices))[0]


async def serve(request):
    import uvicorn
    stop = threading.Event()

    def parent():
        try:
            read_control()
        finally:
            stop.set()
    threading.Thread(target=parent, daemon=True, name='linux-parent').start()
    app = make_app(request)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen(16)
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False,
            ws='websockets', ws_max_size=16384, timeout_graceful_shutdown=5))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError('Backend startup failed')
                if stop.is_set() or asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError('Backend startup interrupted')
                await asyncio.sleep(.02)
            print(json.dumps({'port':sock.getsockname()[1]}), flush=True)
            while not task.done() and not stop.is_set():
                await asyncio.sleep(.05)
        finally:
            server.should_exit = True
            await task


def local_navigation(uri, port):
    """Permit only this launch's loopback origin inside the privileged window."""
    try:
        parsed = urlsplit(uri)
        return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                and parsed.port == port and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def desktop():
    import secrets

    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import GLib, Gtk, WebKit2
    from platformdirs import user_data_path

    root = user_data_path('ScribeLoop', appauthor=False)
    if root.is_symlink():
        raise ValueError('Redirected workspace directory')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = {'protocol':2, 'token':secrets.token_hex(32), 'launch_id':secrets.token_hex(16),
               'database':str((root/'meetings.sqlcipher').resolve())}
    installed = Path('/usr/lib/scribeloop/launch.py')
    command = ([sys.executable, '-I', str(installed)] if installed.is_file() else
               [sys.executable, '-m', 'scribeloop_core.api.linux'])
    child = subprocess.Popen([*command, '--backend'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        child.stdin.write(json.dumps(request)+'\n')
        child.stdin.flush()
        if not select.select([child.stdout], [], [], 40)[0]:
            raise TimeoutError('Startup timed out')
        ready = json.loads(child.stdout.readline(2048))
        port = ready.get('port')
        if type(port) is not int or not 0 < port < 65536:
            raise ValueError('Backend could not unlock the workspace')
        GLib.set_prgname('ScribeLoop')
        context = WebKit2.WebContext.new_ephemeral()
        view = WebKit2.WebView.new_with_context(context)
        view.get_settings().set_enable_developer_extras(False)

        def policy(webview, decision, kind):
            if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                uri = decision.get_request().get_uri()
                if uri.startswith(f'blob:http://127.0.0.1:{port}/'):
                    decision.download()
                    return True
            if (kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                         WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION)
                    and not local_navigation(decision.get_request().get_uri(), port)):
                decision.ignore()
                return True
            return False
        view.connect('decide-policy', policy)
        view.connect('permission-request', lambda webview, permission: (permission.deny(), True)[1])
        window = Gtk.Window(title='ScribeLoop')
        window.set_default_size(1200, 820)
        window.add(view)

        def download_started(webcontext, download):
            def destination(item, suggested):
                chooser = Gtk.FileChooserDialog(title='Export meeting', parent=window,
                    action=Gtk.FileChooserAction.SAVE)
                chooser.add_buttons('Cancel', Gtk.ResponseType.CANCEL,
                                    'Save', Gtk.ResponseType.ACCEPT)
                chooser.set_do_overwrite_confirmation(True)
                chooser.set_current_name(Path(suggested or 'meeting.md').name)
                try:
                    if chooser.run() == Gtk.ResponseType.ACCEPT:
                        item.set_allow_overwrite(True)
                        item.set_destination(Path(chooser.get_filename()).as_uri())
                    else:
                        item.cancel()
                finally:
                    chooser.destroy()
                return True
            download.connect('decide-destination', destination)
        context.connect('download-started', download_started)
        window.connect('delete-event', lambda *args: (Gtk.main_quit(), False)[1])

        def health():
            if child.poll() is not None:
                Gtk.main_quit()
                return False
            return True
        GLib.timeout_add_seconds(1, health)
        view.load_uri(f'http://127.0.0.1:{port}/#token={request["token"]}')
        window.show_all()
        Gtk.main()
    finally:
        if child.poll() is None:
            try:
                child.stdin.write('STOP\n')
                child.stdin.flush()
                child.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()


def main():
    if not sys.platform.startswith('linux'):
        raise SystemExit('ScribeLoop Ubuntu requires Linux')
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['--backend']:
            asyncio.run(serve(bootstrap(read_control(timeout=10))))
        elif not sys.argv[1:]:
            desktop()
        else:
            raise ValueError('Unknown option')
    except Exception:  # noqa: BLE001 - sanitize native and provider diagnostics
        if sys.argv[1:] == ['--backend']:
            print('{"error":"Workspace unavailable"}', flush=True)
        else:
            # Fixed text: native exceptions may include paths or provider credentials.
            print('ScribeLoop could not start. Check the desktop session, Secret Service unlock, '
                  'workspace lock and installed dependencies.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
