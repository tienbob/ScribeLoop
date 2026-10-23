"""Opt-in five-minute native recording; encryption precedes offline transcription.

Native reads and disk writes have separate threads and a bounded queue. A slow
model never blocks Stop: inference starts only after both encrypted files close.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
import time

from ..storage.db import DATABASE_ERRORS
from .language import LanguageDetectionError

log = logging.getLogger(__name__)


class RecordingRunner:
    def __init__(self, store, audio, source_factory, publish, *, transcriber=None,
                 queue_size=512, max_s=300, stop_timeout_s=12):
        self.store, self.audio = store, audio
        self.source_factory, self.publish = source_factory, publish
        self.transcriber = transcriber
        self.language = 'auto'
        self.language_lock = None
        self.queue_size, self.max_s = queue_size, max_s
        self.stop_timeout_s = stop_timeout_s
        self.active_id = None
        self.task = None
        self.source = None
        self.closed = False
        self.stop_requested = threading.Event()
        self.cancel_processing = threading.Event()

    def start(self, selection, source_metadata, title, *, consent_confirmed):
        if consent_confirmed is not True:
            raise ValueError('Recording requires explicit consent')
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Recording or processing already active')
        self.language = source_metadata.get('language', 'auto')
        self.language_lock = None
        if self.language not in {'vi','en','auto'}:
            raise ValueError('Unsupported language hint')
        session = self.store.create_recording(title, source_metadata)
        self.store.set_audio_paths(session['id'], self.audio)
        self.active_id = session['id']
        self.stop_requested.clear()
        self.cancel_processing.clear()
        self.task = asyncio.create_task(self._run(session['id'], selection))
        return session

    def stop(self, session_id):
        if self.active_id != session_id:
            raise ValueError('That recording is not active')
        self.stop_requested.set()

    async def close(self):
        self.closed = True
        self.cancel_processing.set()
        self.stop_requested.set()
        if self.task:
            await self.task

    def _capture(self, session_id, selection, ready):
        writers = {}
        source = None
        producer = None
        abort = threading.Event()
        frames = queue.Queue(maxsize=self.queue_size)
        outcome = []
        created_key = False
        capture_failed = False
        samples = {'mic': 0, 'loopback': 0}
        try:
            # Keys and both writers must exist before even constructing a source.
            self.audio.create_session(session_id)
            created_key = True
            for stream in samples:
                writers[stream] = self.audio.writer(session_id, stream)
            if self.stop_requested.is_set():
                for writer in writers.values():
                    writer.close()
                return samples
            source = self.source_factory(selection, consent_confirmed=True)
            self.source = source

            def read():
                try:
                    for frame in source.frames():
                        if abort.is_set():
                            break
                        try:
                            frames.put_nowait(frame)
                        except queue.Full:
                            raise RuntimeError('Recorder queue overflow; capture stopped') from None
                except Exception as exc:  # noqa: BLE001 - native source boundary
                    outcome.append(exc)
                finally:
                    abort.set()

            producer = threading.Thread(target=read, name='meeting-native-reader', daemon=True)
            producer.start()
            started = time.monotonic()
            stop_at = None
            announced = False
            while producer.is_alive() or not frames.empty():
                now = time.monotonic()
                if now - started >= self.max_s:
                    self.stop_requested.set()
                if self.stop_requested.is_set():
                    if stop_at is None:
                        stop_at = now
                    source.request_stop()  # Retries if startup has not registered a child yet.
                    if now - stop_at > self.stop_timeout_s:
                        raise TimeoutError('Native capture did not drain after Stop')
                try:
                    frame = frames.get(timeout=.05)
                except queue.Empty:
                    continue
                writers[frame.stream].write(frame)
                samples[frame.stream] += frame.pcm.size
                if not announced and all(samples.values()):
                    announced = True
                    ready()
            producer.join()
            if outcome:
                raise outcome[0]
            if not all(samples.values()) and not self.stop_requested.is_set():
                raise RuntimeError('A selected capture stream produced no audio')
            for writer in writers.values():
                writer.close()
            return samples
        except BaseException:
            capture_failed = True
            for writer in writers.values():
                # One failed close must not leak the other track or mask the
                # recording failure that triggered cleanup.
                with contextlib.suppress(Exception):
                    writer.abort()
            # A failed native run retains its authenticated encrypted prefix for
            # future recovery. A failure before source construction has no audio.
            if source is None and created_key:
                for stream in samples:
                    self.audio.path(session_id, stream).unlink(missing_ok=True)
                self.audio.forget_session_key(session_id)
            raise
        finally:
            abort.set()
            try:
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        if not capture_failed:
                            raise
                        log.warning('Native source cleanup also failed')
            finally:
                try:
                    if producer is not None:
                        producer.join(timeout=3)
                finally:
                    self.source = None

    async def _run(self, session_id, selection):
        loop = asyncio.get_running_loop()
        ready_event = asyncio.Event()
        def ready():
            loop.call_soon_threadsafe(ready_event.set)
        capture = asyncio.create_task(asyncio.to_thread(self._capture, session_id, selection, ready))
        ready_wait = asyncio.create_task(ready_event.wait())
        stopping = False
        try:
            while not capture.done():
                await asyncio.wait({capture, ready_wait}, timeout=.1,
                                   return_when=asyncio.FIRST_COMPLETED)
                if ready_wait.done() and not stopping:
                    await self._state(session_id, 'recording')
                    # Avoid a completed Event wait spinning the event loop.
                    ready_wait = asyncio.create_task(asyncio.Event().wait())
                if self.stop_requested.is_set() and not stopping:
                    stopping = True
                    await self._state(session_id, 'stopping')
            samples = await capture
            self.store.record_audio_duration(session_id, samples)
            self.active_id = None
            await self._state(session_id, 'processing')
            if self.cancel_processing.is_set():
                await self._state(session_id, 'interrupted')
                return
            await self._process(session_id)
        except Exception as exc:  # noqa: BLE001 - fail visibly without leaking text/PCM in logs
            self.stop_requested.set()
            with contextlib.suppress(Exception):
                await capture
            log.warning('Meeting recording/processing failed (%s)', type(exc).__name__)
            with contextlib.suppress(*DATABASE_ERRORS):
                await self._state(session_id, 'failed')
            await self.publish({'type': 'recording_error', 'session_id': session_id,
                'message': ('Cannot confidently detect the early speech language. Select Tiếng Việt or English and transcribe again.'
                           if isinstance(exc, LanguageDetectionError) else 'Recording or local transcription failed. Check microphone/Screen Recording '
                           'permission, running source, available disk space, and cached ASR model. '
                           'Any captured audio remains encrypted; no plaintext fallback was used.')})
        finally:
            ready_wait.cancel()
            if self.active_id == session_id:
                self.active_id = None

    def _transcribe(self, audio, session_id, emit, cancelled):
        if self.transcriber:
            return self.transcriber(audio, session_id, emit, cancelled)
        return transcribe_recording(audio, session_id, emit, cancelled, language=self.language, locked_language=self.language_lock)

    def reprocess(self, session_id, *, language):
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Recording or processing already active')
        session = self.store.get(session_id)
        if (not session or session['source'] != 'meeting' or not session['consent_confirmed']
                or session['status'] not in {'completed','failed','interrupted'}):
            raise ValueError('Select a saved authorized meeting')
        if language not in {'vi','en','auto'}:
            raise ValueError('Unsupported language hint')
        self.language = language
        prior = self.store.language_lock(session_id)
        self.language_lock = prior if language == 'auto' and prior and prior.get('mode') == 'auto' else None
        self.cancel_processing.clear()
        self.task = asyncio.create_task(self._reprocess(session_id))

    async def _reprocess(self, session_id):
        try:
            await self._state(session_id, 'processing')
            await self._process(session_id, replace=True)
        except Exception as exc:  # noqa: BLE001 - retain original transcript on model/storage failure
            log.warning('Meeting reprocessing failed (%s)', type(exc).__name__)
            with contextlib.suppress(*DATABASE_ERRORS):
                await self._state(session_id, 'failed')
            await self.publish({'type':'recording_error', 'session_id':session_id,
                'message':('Cannot confidently detect the early speech language. Select Tiếng Việt or English and transcribe again.'
                           if isinstance(exc, LanguageDetectionError) else
                           'Local MOSS reprocessing failed. Previous transcript and encrypted audio retained.')})

    async def _process(self, session_id, *, replace=False):
        # Bounded output queue keeps transcript persistence on the DB thread.
        chunks = queue.Queue(maxsize=64)
        def emit(chunk):
            while not self.cancel_processing.is_set():
                try:
                    chunks.put(chunk, timeout=.1)
                    return
                except queue.Full:
                    continue
        replacement = []
        processing = asyncio.create_task(asyncio.to_thread(self._transcribe,
            self.audio, session_id, emit, self.cancel_processing))
        try:
            while not processing.done() or not chunks.empty():
                try:
                    chunk = chunks.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.05)
                    continue
                if chunk.get('type') == 'language_locked':
                    self.store.set_language_lock(session_id, chunk)
                    await self.publish({**chunk, 'session_id':session_id})
                    continue
                if replace:
                    if len(replacement) >= 16000:
                        raise ValueError('Transcript exceeds segment limit')
                    replacement.append(chunk)
                    continue
                row = self.store.append(session_id, chunk, stream=chunk['stream'])
                if row:
                    await self.publish({'type': 'segment', 'session_id': session_id, 'segment': row})
            await processing
        finally:
            self.cancel_processing.set()
            await processing
        if self.closed:
            await self._state(session_id, 'interrupted')
        else:
            if replace:
                self.store.revise_transcript(session_id, replacement)
                await self.publish({'type':'transcript_replaced', 'session_id':session_id,
                                    'segments':self.store.segments(session_id)})
            note = self.store.create_transcript_notes(session_id)
            await self.publish({'type': 'notes', 'session_id': session_id, 'notes': note})
            await self._state(session_id, 'completed')

    async def _state(self, session_id, status):
        session = self.store.transition(session_id, status)
        await self.publish({'type': 'session', 'session': session})


def transcribe_recording(audio, session_id, emit, cancelled, *, language='auto', locked_language=None):
    """Actual meeting path: local MOSS float16 + SDPA, explicit language hint."""
    from .meeting_asr import transcribe_moss
    return transcribe_moss(audio, session_id, emit, cancelled, language=language, locked_language=locked_language)
