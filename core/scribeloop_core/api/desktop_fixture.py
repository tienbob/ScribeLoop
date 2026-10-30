"""M0 desktop fixture: private bootstrap, synthetic SQLCipher data, read-only IPC.

No provider/configuration/capture modules are constructed. No .env, production
Keychain or user meeting store is read. stdin is a private parent control pipe;
stdout contains bounded JSON readiness/status only, never a token or user data.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import select
import socket
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..capture.source import AudioFrame
from ..storage.audio import SessionAudioStore
from ..storage.encrypted import encrypted_database
from ..storage.sessions import SessionStore
from ..storage.vectors import SqliteVecIndex
from .auth import check_first_message, host_allowed, origin_allowed

PROTOCOL=1
MAX_CONTROL=2048
MAX_RESPONSE=256_000


class FixtureKeys:
    def __init__(self):
        self.values={}
    def get(self,account):
        return self.values.get(account)
    def create(self,account):
        if account in self.values:
            raise ValueError('Fixture key already exists')
        self.values[account]=secrets.token_bytes(32)
        return self.values[account]
    def delete(self,account):
        del self.values[account]


def read_control(timeout=None):
    """Bound a private pipe line without buffered prefetch or partial-line hangs."""
    deadline = None if timeout is None else time.monotonic() + timeout
    data = bytearray()
    while len(data) <= MAX_CONTROL:
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        if not select.select([0], [], [], remaining)[0]:
            raise TimeoutError("Parent control timeout")
        part = os.read(0, 1)
        if not part:
            return bytes(data)
        data.extend(part)
        if part == b"\n":
            return bytes(data)
    raise ValueError("Parent control exceeds bounds")


def validate_bootstrap(line):
    if not isinstance(line,bytes) or len(line)>MAX_CONTROL:
        raise ValueError('Invalid bootstrap size')
    value=json.loads(line)
    if (set(value)!={'protocol','token','launch_id','fixture'} or type(value['protocol']) is not int or
            value['protocol']!=PROTOCOL or value['fixture'] is not True or
            not isinstance(value['token'],str) or len(value['token'])!=64 or
            any(c not in '0123456789abcdef' for c in value['token']) or
            not isinstance(value['launch_id'],str) or len(value['launch_id'])!=32 or
            any(c not in '0123456789abcdef' for c in value['launch_id'])):
        raise ValueError('Invalid fixture bootstrap')
    return value


def create_fixture_app(token,launch_id,root,keys):
    @asynccontextmanager
    async def lifespan(app):
        with encrypted_database(root/'fixture.sqlcipher',keys) as conn:
            store=SessionStore(conn)
            audio=SessionAudioStore(root/'recordings',keys)
            sid=store.create_recording('Design review · synthetic',{'provider':'fixture','scope':'microphone'})['id']
            audio.create_session(sid)
            store.set_audio_paths(sid,audio)
            for stream in ('mic','loopback'):
                with audio.writer(sid,stream) as writer:
                    for second in range(3):
                        pcm=(np.sin(np.arange(16000)*2*np.pi*440/16000)*.05).astype(np.float32)
                        writer.write(AudioFrame(pcm,stream,second*1000,0))
            store.append(sid,{'text':'The team agreed to test the native desktop workflow with synthetic data.',
                'stream':'mic','start_ms':0,'end_ms':3000,'is_final':True},stream='mic')
            store.transition(sid,'completed')
            store.create_transcript_notes(sid)
            for stream in ('mic','loopback'):
                for _ in audio.frames(sid,stream):
                    pass
            index=SqliteVecIndex(conn,dim=16)
            index.add(sid,0,3000,[store.segments(sid)[0]['id']],'Synthetic fixture',np.ones(16,dtype=np.float32).tobytes())
            # Exercise soundfile's bundled native library without writing plaintext audio.
            assert 'WAV' in sf.available_formats()
            app.state.store=store
            app.state.checks={'sqlcipher':True,'encrypted_audio':True,'sqlite_vec':True,
                              'soundfile':True,'provider_calls':False,'capture':False,'keychain':'not_requested'}
            yield
        keys.values.clear()

    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])
    @app.websocket('/ws')
    async def ws_endpoint(ws:WebSocket):
        host=ws.headers.get('host','')
        if (not host_allowed(host) or not origin_allowed(ws.headers.get('origin'),host) or
                not ws.client or ws.client.host not in {'127.0.0.1','::1','testclient'}):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            first=await asyncio.wait_for(ws.receive_text(),5)
            if len(first)>128 or not check_first_message(first,token):
                await ws.close(code=1008)
                return
            store=app.state.store
            snapshot={'type':'snapshot','protocol':PROTOCOL,'launch_id':launch_id,'fixture':True,
                'checks':app.state.checks,'sessions':store.list(),
                'segments':{s['id']:store.segments(s['id']) for s in store.list()}}
            encoded=json.dumps(snapshot)
            if len(encoded)>MAX_RESPONSE:
                raise ValueError('Fixture snapshot exceeds bounds')
            await ws.send_text(encoded)
            while True:
                raw=await ws.receive_text()
                if len(raw)>1024:
                    await ws.close(code=1008)
                    return
                command=json.loads(raw)
                if command=={'action':'snapshot'}:
                    await ws.send_text(encoded)
                elif command=={'action':'ping'}:
                    await ws.send_json({'type':'pong','launch_id':launch_id})
                else:
                    await ws.send_json({'type':'error','message':'M0 fixture is read-only; capture and providers are disabled.'})
        except (WebSocketDisconnect,asyncio.TimeoutError,ValueError):
            return
    return app


def deny_external_network(event,args):
    if event=='socket.connect':
        address=args[1]
        if not isinstance(address,tuple) or address[0] not in {'127.0.0.1','::1'}:
            raise PermissionError('Fixture forbids external connections')


async def serve(bootstrap):
    sys.addaudithook(deny_external_network)
    stop=threading.Event()
    def watch_parent():
        while True:
            try:
                command=read_control()
            except (ValueError, OSError):
                stop.set()
                return
            if not command or command==b'STOP\n':
                stop.set()
                return
            stop.set()  # Unknown parent control also fails closed.
            return
    threading.Thread(target=watch_parent,name='fixture-parent',daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='scribeloop-m0-') as directory:
        root=Path(directory)
        os.chmod(root,0o700)
        app=create_fixture_app(bootstrap['token'],bootstrap['launch_id'],root,FixtureKeys())
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        sock.bind(('127.0.0.1',0))
        sock.listen(16)
        config=uvicorn.Config(app,host='127.0.0.1',port=0,log_level='critical',access_log=False,
                              ws='websockets',ws_max_size=4096,timeout_graceful_shutdown=3)
        server=uvicorn.Server(config)
        task=asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError('Fixture startup failed')
                    if stop.is_set():
                        server.should_exit=True
                    await asyncio.sleep(.02)
            print(json.dumps({'type':'ready','protocol':PROTOCOL,'pid':os.getpid(),
                'launch_id':bootstrap['launch_id'],'host':'127.0.0.1','port':sock.getsockname()[1],
                'fixture':True}),flush=True)
            while not stop.is_set() and not task.done():
                await asyncio.sleep(.05)
            server.should_exit=True
            await task
        finally:
            server.should_exit=True
            if not task.done():
                await task
            sock.close()


def main():
    try:
        line=read_control(timeout=10)
        bootstrap=validate_bootstrap(line)
        asyncio.run(serve(bootstrap))
    except Exception:  # noqa: BLE001 - no exception bodies, paths or bootstrap secrets in diagnostics
        print('{"type":"fatal","message":"Isolated fixture failed to start or stop."}',flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
