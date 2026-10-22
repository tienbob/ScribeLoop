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

log = logging.getLogger(__name__)


class RecordingRunner:
    def __init__(self, store, audio, source_factory, publish, *, transcriber=None,
                 queue_size=512, max_s=300, stop_timeout_s=12):
        self.store, self.audio = store, audio
        self.source_factory, self.publish = source_factory, publish
        self.transcriber = transcriber or transcribe_recording
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
            for writer in writers.values():
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
            if source is not None:
                source.close()
            if producer is not None:
                producer.join(timeout=3)
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
            await capture
            self.active_id = None
            await self._state(session_id, 'processing')
            if self.cancel_processing.is_set():
                await self._state(session_id, 'interrupted')
                return
            # Bounded output queue keeps transcript persistence on the DB thread.
            chunks = queue.Queue(maxsize=64)
            def emit(chunk):
                while not self.cancel_processing.is_set():
                    try:
                        chunks.put(chunk, timeout=.1)
                        return
                    except queue.Full:
                        continue
            processing = asyncio.create_task(asyncio.to_thread(self.transcriber,
                self.audio, session_id, emit, self.cancel_processing))
            try:
                while not processing.done() or not chunks.empty():
                    try:
                        chunk = chunks.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(.05)
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
                note = self.store.create_transcript_notes(session_id)
                await self.publish({'type': 'notes', 'session_id': session_id, 'notes': note})
                await self._state(session_id, 'completed')
        except Exception as exc:  # noqa: BLE001 - fail visibly without leaking text/PCM in logs
            self.stop_requested.set()
            with contextlib.suppress(Exception):
                await capture
            log.warning('Meeting recording/processing failed (%s)', type(exc).__name__)
            with contextlib.suppress(*DATABASE_ERRORS):
                await self._state(session_id, 'failed')
            await self.publish({'type': 'recording_error', 'session_id': session_id,
                'message': 'Recording or local transcription failed. Check microphone/Screen Recording '
                           'permission, running source, available disk space, and cached ASR model. '
                           'Any captured audio remains encrypted; no plaintext fallback was used.'})
        finally:
            ready_wait.cancel()
            if self.active_id == session_id:
                self.active_id = None

    async def _state(self, session_id, status):
        session = self.store.transition(session_id, status)
        await self.publish({'type': 'session', 'session': session})


def transcribe_recording(audio, session_id, emit, cancelled):
    """Cached local test baseline, with original frame timestamps and gaps.

    No network, plaintext audio files, named-speaker claims, or summary model.
    MOSS batch configuration remains unchanged; this is a separate smoke path.
    """
    import numpy as np
    from faster_whisper import WhisperModel

    from .replay import ReplayChunk

    model = WhisperModel('tiny', device='cpu', compute_type='int8', cpu_threads=2,
                         local_files_only=True)
    for stream in ('loopback', 'mic'):
        # Verify the complete authenticated container before persisting any transcript.
        for _ in audio.frames(session_id, stream):
            if cancelled.is_set():
                return
        buffers = []
        count = 0
        start = end = None
        def drain(stream=stream):
            nonlocal buffers, count, start
            if not count:
                return
            pcm = np.concatenate(buffers)
            segments, info = model.transcribe(pcm, beam_size=3, vad_filter=True)
            duration = pcm.size * 1000 / 16000
            for segment in segments:
                if cancelled.is_set():
                    return
                text = segment.text.strip()
                a = int(start + max(0, segment.start * 1000))
                b = int(start + min(duration, segment.end * 1000))
                if text and b > a:
                    chunk = ReplayChunk.model_validate({'text':text, 'start_ms':a, 'end_ms':b,
                        'is_final':True, 'language':info.language, 'confidence':segment.avg_logprob}).model_dump()
                    emit({**chunk, 'stream': stream})
            buffers, count, start = [], 0, None
        for frame in audio.frames(session_id, stream):
            if cancelled.is_set():
                return
            if end is not None and abs(frame.t_ms - end) > 1:
                drain()
            if start is None:
                start = frame.t_ms
            buffers.append(frame.pcm)
            count += frame.pcm.size
            end = frame.t_ms + frame.pcm.size * 1000 / 16000
            if count >= 30 * 16000:
                drain()
        drain()
