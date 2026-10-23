"""One active public WAV replay; model work runs off the API event loop.

The bounded queue backpressures replay rather than dropping transcript events.
This is not a native recorder: real capture needs an independent recorder queue.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..capture.source import WavFileSource
from ..storage.db import DATABASE_ERRORS

log = logging.getLogger(__name__)


class ReplayChunk(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=16384)
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(gt=0, strict=True)
    is_final: bool = Field(strict=True)
    language: str | None = Field(default=None, max_length=64)
    confidence: float | None = None
    speaker_key: str | None = Field(default=None, pattern=r"^deepgram:[0-9a-f]{32}:(mic|loopback):[0-9]{1,4}$")

    @model_validator(mode='after')
    def valid(self):
        if self.end_ms <= self.start_ms or not self.text.strip():
            raise ValueError('Empty text or invalid transcript span')
        self.text.encode('utf-8')
        if self.language is not None:
            self.language.encode('utf-8')
        return self


class ReplayRunner:
    def __init__(self, store, backend_factory, publish):
        self.store = store
        self.backend_factory = backend_factory
        self.publish = publish
        self.active_id = None
        self.task = None
        self.stop_requested = threading.Event()
        self.closed = False
        self.unavailable = False

    def start(self, fixture: str, title: str, path: Path):
        if self.closed or self.unavailable:
            raise ValueError('Replay unavailable; restart the server')
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
        self.closed = True
        self.stop_requested.set()
        if self.task:
            await self.task

    async def _run(self, session_id: str, path: Path):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=64)
        consumer_failed = threading.Event()

        def emit(kind, data=None):
            if consumer_failed.is_set():
                return False
            future = asyncio.run_coroutine_threadsafe(queue.put((kind, data)), loop)
            while True:
                try:
                    future.result(timeout=.2)
                    return True
                except FutureTimeout:
                    if consumer_failed.is_set():
                        future.cancel()
                        return False

        def work():
            backend = source = None
            status = 'failed'
            try:
                info = sf.info(path)
                if not 0 < info.duration <= 300:
                    raise ValueError('Fixture is no longer within development bounds')
                source = WavFileSource(path, realtime=True)
                if self.stop_requested.is_set():
                    status = 'stopped'
                else:
                    backend = self.backend_factory()
                    emit('ready')
                    for frame in source.frames():
                        if self.stop_requested.is_set() or consumer_failed.is_set():
                            break
                        for chunk in backend.feed(frame):
                            validated = ReplayChunk.model_validate(chunk)
                            if validated.end_ms > round(info.duration * 1000):
                                raise ValueError('Transcript extends beyond audio')
                            if not emit('chunk', validated.model_dump()):
                                break
                    if not consumer_failed.is_set():
                        for chunk in backend.flush():
                            validated = ReplayChunk.model_validate(chunk)
                            if validated.end_ms > round(info.duration * 1000):
                                raise ValueError('Transcript extends beyond audio')
                            if not emit('chunk', validated.model_dump()):
                                break
                    status = 'stopped' if self.stop_requested.is_set() else 'completed'
            except Exception as exc:  # noqa: BLE001 - isolate third-party model/source failures
                log.warning('Replay worker failed (%s)', type(exc).__name__)
            finally:
                for resource in (source, backend):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception as exc:  # noqa: BLE001 - cleanup must not strand replay
                            status = 'failed'
                            log.warning('Replay cleanup failed (%s)', type(exc).__name__)
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
                    # Close the model and free the slot before announcing terminal
                    # state: a client may immediately start its next replay.
                    await worker
                    session = self.store.transition(session_id, data)
                    self.active_id = None
                    await self.publish({'type': 'session', 'session': session})
                    break
        except Exception as exc:  # noqa: BLE001 - stop producer if persistence/publishing fails
            consumer_failed.set()
            self.stop_requested.set()
            await worker
            log.error('Replay consumer failed (%s)', type(exc).__name__)
            self.unavailable = True
            with contextlib.suppress(*DATABASE_ERRORS):
                self.store.transition(session_id, 'failed')
            self.active_id = None
            await self.publish({'type': 'replay_unavailable', 'session_id': session_id,
                                'message': 'Replay storage failed. Restart the local server.'})
        finally:
            consumer_failed.set()
            try:
                await worker
            finally:
                # A terminal publish may already have allowed a new replay.
                if self.active_id == session_id:
                    self.active_id = None
