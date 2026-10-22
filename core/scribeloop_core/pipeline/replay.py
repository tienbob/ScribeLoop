"""One active public WAV replay; model work runs off the API event loop.

The bounded queue backpressures replay rather than dropping transcript events.
This is not a native recorder: real capture needs an independent recorder queue.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from ..capture.source import WavFileSource


class ReplayRunner:
    def __init__(self, store, backend_factory, publish):
        self.store = store
        self.backend_factory = backend_factory
        self.publish = publish
        self.active_id = None
        self.task = None
        self.stop_requested = threading.Event()

    def start(self, fixture: str, title: str, path: Path):
        if self.active_id is not None:
            raise ValueError('A replay is already active')
        session = self.store.create(fixture, title, str(path))
        self.active_id = session['id']
        self.stop_requested.clear()
        self.task = asyncio.create_task(self._run(session['id'], path))
        return session

    def stop(self, session_id: str):
        if self.active_id != session_id:
            raise ValueError('That session is not active')
        self.stop_requested.set()

    async def close(self):
        self.stop_requested.set()
        if self.task:
            await self.task

    async def _run(self, session_id: str, path: Path):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=64)

        def emit(kind, data=None):
            future = asyncio.run_coroutine_threadsafe(queue.put((kind, data)), loop)
            # Keep queue bounded and allow shutdown if the consumer failed.
            while True:
                try:
                    future.result(timeout=.2)
                    return
                except FutureTimeout:
                    if consumer_failed.is_set():
                        future.cancel()
                        raise RuntimeError('Replay consumer closed') from None

        consumer_failed = threading.Event()

        def work():
            backend = None
            source = WavFileSource(path, realtime=True)
            status = 'failed'
            try:
                backend = self.backend_factory()
                emit('ready')
                for frame in source.frames():
                    if self.stop_requested.is_set():
                        break
                    for chunk in backend.feed(frame):
                        emit('chunk', chunk)
                for chunk in backend.flush():
                    emit('chunk', chunk)
                status = 'stopped' if self.stop_requested.is_set() else 'completed'
            except Exception:  # noqa: BLE001 - isolate third-party model failures
                status = 'failed'
            finally:
                source.close()
                if backend:
                    try:
                        backend.close()
                    except Exception:  # noqa: BLE001 - cleanup failure must terminate session
                        status = 'failed'
            emit('done', status)

        worker = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                kind, data = await queue.get()
                if kind == 'ready':
                    session = self.store.transition(session_id, 'replaying')
                    await self.publish({'type': 'session', 'session': session})
                elif kind == 'chunk':
                    segment = self.store.append(session_id, data)
                    await self.publish({'type': 'segment' if segment else 'partial',
                        'session_id': session_id, 'segment': segment or data})
                elif kind == 'done':
                    session = self.store.transition(session_id, data)
                    await self.publish({'type': 'session', 'session': session})
                    break
        except Exception:  # noqa: BLE001 - persistence/publisher failure must stop producer
            consumer_failed.set()
            self.stop_requested.set()
            self.store.transition(session_id, 'failed')
        finally:
            consumer_failed.set()
            try:
                await worker
            finally:
                self.active_id = None
