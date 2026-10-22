"""Authenticated Phase 1 public-fixture replay workspace; loopback only.

Disk storage here is deliberately limited to explicitly configured public test
fixtures. Real-recording capture/upload waits for the complete encrypted recorder lifecycle.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import soundfile as sf
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..pipeline.replay import ReplayRunner
from ..storage.db import DATABASE_ERRORS, connect, migrate
from ..storage.sessions import SessionStore
from .auth import check_first_message, host_allowed, new_token, origin_allowed

STATIC = Path(__file__).parent / 'static'
AUTH_TIMEOUT_S = 5
SEND_TIMEOUT_S = 5


class StartReplay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start']
    fixture: str = Field(min_length=1, max_length=64)
    title: str = Field(default='Untitled replay', min_length=1, max_length=160)
    consent_confirmed: bool = Field(strict=True)

    @field_validator('title', 'fixture')
    @classmethod
    def valid_text(cls, value):
        value.encode('utf-8')
        if not value.strip():
            raise ValueError('Empty field')
        return value


def create_app(*, database=None, fixtures=None, backend_factory=None,
               storage='memory', key_store=None) -> tuple[FastAPI, str]:
    if storage not in {'memory', 'public-demo', 'encrypted'}:
        raise ValueError('Unknown storage mode')
    if (storage == 'memory') != (database is None):
        raise ValueError('Disk storage requires an explicit storage mode and database path')
    token = new_token()
    fixtures = {name: Path(path).resolve() for name, path in (fixtures or {}).items()}
    fixture_rows = []
    for name, path in fixtures.items():
        info = sf.info(path)
        if not 0 < info.duration <= 300:
            raise ValueError('Public fixtures must be nonempty and at most five minutes')
        fixture_rows.append({'id': name, 'duration_s': info.duration})
    if backend_factory is None:
        def backend_factory():
            from ..pipeline.asr import ChunkedFasterWhisper
            return ChunkedFasterWhisper(model_size='tiny', chunk_s=4, language='en',
                                         local_files_only=True)

    clients = set()

    async def publish(event):
        # A stalled UI must not block persistence or another UI. Slow clients are
        # disconnected; the next authenticated snapshot restores confirmed text.
        for queue in list(clients):
            if queue.full():
                clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'type': 'reconnect_required'})
            else:
                queue.put_nowait(event)

    @asynccontextmanager
    async def lifespan(app):
        from contextlib import closing

        from ..storage.encrypted import encrypted_database

        manager = (encrypted_database(Path(database), key_store) if storage == 'encrypted'
                   else closing(migrate(connect(database or ':memory:'))))
        # Keychain access occurs during startup, before any recording or UI work.
        with manager as conn:
            store = SessionStore(conn)
            store.recover()
            app.state.store = store
            app.state.runner = ReplayRunner(store, backend_factory, publish)
            app.state.storage = storage
            try:
                yield
            finally:
                await app.state.runner.close()

    app = FastAPI(title='ScribeLoop', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/healthz')
    async def healthz():
        return {'status': 'ok'}

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/app.js')
    async def javascript():
        return FileResponse(STATIC / 'app.js', media_type='text/javascript')

    @app.get('/app.css')
    async def stylesheet():
        return FileResponse(STATIC / 'app.css', media_type='text/css')

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        host = ws.headers.get('host', '')
        if not host_allowed(host) or not origin_allowed(ws.headers.get('origin'), host):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=128)
        sender = reader = None

        async def text_frame(limit):
            frame = await ws.receive()
            if frame['type'] == 'websocket.disconnect':
                raise WebSocketDisconnect(frame.get('code', 1000))
            text = frame.get('text')
            if not isinstance(text, str) or len(text) > limit:
                await ws.close(code=1008)
                raise WebSocketDisconnect(1008)
            return text

        async def send(event):
            await asyncio.wait_for(ws.send_json(event), timeout=SEND_TIMEOUT_S)

        try:
            message = await asyncio.wait_for(text_frame(256), timeout=AUTH_TIMEOUT_S)
            if not check_first_message(message, token):
                await ws.close(code=1008)
                return
            clients.add(queue)
            store = app.state.store
            history = store.list()
            await send({'type': 'snapshot', 'fixtures': fixture_rows,
                'sessions': history, 'active_id': app.state.runner.active_id,
                'replay_available': not app.state.runner.unavailable, 'storage': app.state.storage,
                'segments': {s['id']: store.segments(s['id']) for s in history}})

            async def send_events():
                while True:
                    event = await queue.get()
                    await send(event)
                    if event['type'] == 'reconnect_required':
                        await ws.close(code=1013)
                        return

            async def receive_commands():
                while True:
                    try:
                        raw = await text_frame(4096)
                        command = json.loads(raw)
                        if not isinstance(command, dict):
                            raise TypeError('Expected an object')
                        if command.get('action') == 'start':
                            request = StartReplay.model_validate(command)
                            if not request.consent_confirmed:
                                raise ValueError('Confirm authorization before replay')
                            if request.fixture not in fixtures:
                                raise ValueError('Unknown public fixture')
                            session = app.state.runner.start(request.fixture, request.title,
                                                              fixtures[request.fixture])
                            await publish({'type': 'session', 'session': session})
                        elif command.get('action') == 'stop':
                            app.state.runner.stop(command.get('session_id'))
                        else:
                            raise ValueError('Unknown command')
                    except (ValueError, ValidationError, TypeError, *DATABASE_ERRORS):
                        if queue.full():
                            await ws.close(code=1013)
                            return
                        queue.put_nowait({'type': 'error', 'message':
                            'Request rejected. Check authorization, fixture, and active replay.'})

            sender = asyncio.create_task(send_events())
            reader = asyncio.create_task(receive_commands())
            done, _ = await asyncio.wait({sender, reader}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.TimeoutError:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
                await asyncio.wait_for(ws.close(code=1008 if sender is None else 1013), timeout=1)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            clients.discard(queue)
            for task in (sender, reader):
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect,
                                             RuntimeError, asyncio.TimeoutError):
                        await task

    return app, token


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-fixture', type=Path, required=True,
                        help='public/authorized non-sensitive test WAV, at most five minutes')
    parser.add_argument('--storage', choices=['encrypted', 'public-demo'], default='encrypted')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--port', type=int, default=8757)
    args = parser.parse_args()
    if args.database is None:
        if args.storage == 'encrypted':
            from platformdirs import user_data_path
            args.database = user_data_path('ScribeLoop', appauthor=False) / 'sessions.sqlcipher'
        else:
            args.database = Path('data/phase1/public-replays.sqlite3')
    args.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    app, token = create_app(database=args.database, storage=args.storage,
                            fixtures={'development-sample': args.public_fixture})
    # Fragment is not sent in HTTP requests; client removes it after reading.
    print(f'Open locally: http://127.0.0.1:{args.port}/#token={token}', file=sys.stderr)
    uvicorn.run(app, host='127.0.0.1', port=args.port, ws_max_size=8192, access_log=False)


if __name__ == '__main__':
    main()
