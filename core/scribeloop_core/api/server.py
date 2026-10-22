"""Authenticated Phase 1 public-fixture replay workspace; loopback only.

Disk storage here is deliberately limited to explicitly configured public test
fixtures. Real-recording capture/upload waits for encrypted application storage.
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
from urllib.parse import urlsplit

import soundfile as sf
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..pipeline.replay import ReplayRunner
from ..storage.db import connect, migrate
from ..storage.sessions import SessionStore
from .auth import check_first_message, host_allowed, new_token

STATIC = Path(__file__).parent / 'static'


class StartReplay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start']
    fixture: str = Field(min_length=1, max_length=64)
    title: str = Field(default='Untitled replay', min_length=1, max_length=160)
    consent_confirmed: bool = Field(strict=True)


def create_app(*, database=None, fixtures=None, backend_factory=None) -> tuple[FastAPI, str]:
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
        conn = migrate(connect(database or ':memory:'))
        store = SessionStore(conn)
        store.recover()
        app.state.store = store
        app.state.runner = ReplayRunner(store, backend_factory, publish)
        try:
            yield
        finally:
            await app.state.runner.close()
            conn.close()

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
        origin = ws.headers.get('origin')
        if not host_allowed(host) or (origin is not None and
                (urlsplit(origin).scheme not in {'http', 'https'} or urlsplit(origin).netloc != host)):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=128)
        sender = None
        try:
            message = await asyncio.wait_for(ws.receive_text(), timeout=5)
            if len(message) > 256 or not check_first_message(message, token):
                await ws.close(code=1008)
                return
            clients.add(queue)
            store = app.state.store
            await ws.send_json({'type': 'snapshot', 'fixtures': fixture_rows,
                'sessions': store.list(), 'active_id': app.state.runner.active_id,
                'segments': {s['id']: store.segments(s['id']) for s in store.list()}})

            async def send_events():
                while True:
                    event = await queue.get()
                    await ws.send_json(event)
                    if event['type'] == 'reconnect_required':
                        await ws.close(code=1013)
                        return

            sender = asyncio.create_task(send_events())
            while True:
                try:
                    raw = await ws.receive_text()
                    if len(raw) > 4096:
                        raise ValueError('Command too large')
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
                except (ValueError, ValidationError, TypeError):
                    if queue.full():
                        await ws.close(code=1013)
                        return
                    queue.put_nowait({'type': 'error', 'message':
                        'Request rejected. Check authorization, fixture, and active replay.'})
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, KeyError):
            pass
        finally:
            clients.discard(queue)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await sender

    return app, token


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-fixture', type=Path, required=True,
                        help='public/authorized non-sensitive test WAV, at most five minutes')
    parser.add_argument('--database', type=Path, default=Path('data/phase1/public-replays.sqlite3'))
    parser.add_argument('--port', type=int, default=8757)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    app, token = create_app(database=args.database, fixtures={'development-sample': args.public_fixture})
    # Fragment is not sent in HTTP requests; client removes it after reading.
    print(f'Open locally: http://127.0.0.1:{args.port}/#token={token}', file=sys.stderr)
    uvicorn.run(app, host='127.0.0.1', port=args.port, ws_max_size=8192, access_log=False)


if __name__ == '__main__':
    main()
