"""Production desktop bootstrap. Private parent pipe, no .env or implicit capture."""
from __future__ import annotations

import asyncio
import functools
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from .desktop_contract import PROTOCOL, DesktopContract
from .desktop_fixture import read_control
from .desktop_settings import DesktopSettings
from .server import create_app


def bootstrap(line):
    if not isinstance(line,bytes) or len(line)>2048:
        raise ValueError('Invalid bootstrap')
    value = json.loads(line)
    if (not isinstance(value,dict) or set(value)!={'protocol','token','launch_id','database'}
            or type(value['protocol']) is not int or value['protocol']!=PROTOCOL
            or any(not isinstance(value[key],str) or len(value[key])!=size or
                any(c not in '0123456789abcdef' for c in value[key])
                for key,size in [('token',64),('launch_id',32)])
            or not isinstance(value['database'],str) or len(value['database'])>1024
            or not Path(value['database']).is_absolute()
            or Path(value['database']).suffix!='.sqlcipher'):
        raise ValueError('Invalid bootstrap')
    return value


def make_app(request, helpers, *, settings=None, key_store=None, discovery_factory=None,
             recording_factory=None):
    from ..capture.devices import macos_capture_devices, request_microphone_access
    from ..capture.macos_client import MacOSMeetingAudioSource
    from ..meetings.macos import MacOSActivityObserver, installed_apps
    from ..meetings.service import DiscoveryService

    if settings is None:
        config = helpers.parent/'Resources/transcription.json'
        value = json.loads(config.read_text(encoding='utf-8'))
        key = value.get('api_key') if isinstance(value,dict) else None
        if (not isinstance(key,str) or not 16<=len(key)<=512 or not key.isascii()
                or any(c.isspace() for c in key)):
            raise ValueError('Operator transcription configuration is missing')
        settings = DesktopSettings(transcription_key=key)
    contract = DesktopContract(settings, helpers/'scribeloop-meeting')
    def discovery(publish):
        return DiscoveryService(publish,
            observer=MacOSActivityObserver(helpers/'scribeloop-observer'), apps_provider=installed_apps,
            devices_provider=functools.partial(macos_capture_devices,contract.helper))
    return create_app(database=Path(request['database']), storage='encrypted', key_store=key_store,
        discovery_factory=discovery_factory or discovery, recording_factory=recording_factory,
        source_factory=functools.partial(MacOSMeetingAudioSource,helper_path=contract.helper),
        permission_request=functools.partial(request_microphone_access,contract.helper),
        cloud_config=settings.cloud(), summary_provider=settings.value.summary(),
        launch_token=request['token'], desktop=contract)[0]


async def serve(request, *, app_factory=make_app, helpers=None):
    # Resources/Core/<exe>, with helpers in Contents/Helpers, independent of cwd.
    helpers = helpers or Path(sys.executable).resolve().parents[2]/'Helpers'
    if not all((helpers/name).is_file() for name in ('scribeloop-meeting','scribeloop-observer')):
        raise ValueError('Bundled native helpers are missing')
    stop = threading.Event()
    def parent():
        try:
            read_control()  # STOP, unknown control, or EOF all stop this owned backend.
        finally:
            stop.set()
    threading.Thread(target=parent, name='desktop-parent', daemon=True).start()
    app = app_factory(request,helpers)
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0))
        sock.listen(16)
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False,
            ws='websockets', ws_max_size=16384, timeout_graceful_shutdown=5))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    if stop.is_set():
                        server.should_exit=True
                    if task.done():
                        await task
                        raise RuntimeError('Core startup failed')
                    await asyncio.sleep(.02)
            print(json.dumps({'type':'ready','protocol':PROTOCOL,'pid':os.getpid(),
                'launch_id':request['launch_id'],'host':'127.0.0.1','port':sock.getsockname()[1],
                'boot_id':app.state.desktop_contract.boot_id}),flush=True)
            while not stop.is_set() and not task.done():
                await asyncio.sleep(.05)
            server.should_exit=True
            await task
        finally:
            server.should_exit=True
            if not task.done():
                await task


def main():
    try:
        if sys.argv[1:] == ['--inspect-audio']:
            from ..storage.audio_quality import main as inspect_audio
            inspect_audio(json.loads(read_control(timeout=10)))
            return
        request = bootstrap(read_control(timeout=10))
        asyncio.run(serve(request))
    except Exception:  # noqa: BLE001 - never leak keys, paths, provider bodies or transcript
        print('{"type":"fatal","message":"Desktop core could not open storage or settings. Check Keychain access, database lock and bundle installation."}',flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
