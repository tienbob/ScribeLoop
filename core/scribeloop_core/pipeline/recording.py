"""Opt-in five-minute encrypted recording with independent transcription workers.

Native reads and disk writes have a bounded queue; inference never blocks capture
Stop. The selected backend owns transcription; capture never switches providers.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import shutil
import threading
import time

from ..storage.db import DATABASE_ERRORS
from .failures import failure_diagnostic
from .language import LanguageDetectionError

log = logging.getLogger(__name__)
STOP_REASONS = frozenset({'manual_stop','auto_app_inactive','auto_microphone_released'})
STOP_REASON_WINS = frozenset({'duration_limit','auto_app_inactive','auto_microphone_released','app_shutdown'})


def resolve_end_reason(stop_reason, capture_end_reason):
    if stop_reason in STOP_REASON_WINS:
        return stop_reason
    return capture_end_reason or stop_reason or 'unknown'


def automatic_notes_eligible(session_id, duration_ms, confirmed_segments):
    if confirmed_segments or (duration_ms or 0) >= 30000:
        return True
    log.warning('Automatic notes skipped for session %s: duration_ms=%s confirmed_segments=%s',
                session_id,duration_ms or 0,confirmed_segments)
    return False


class RecordingRunner:
    supported_languages = frozenset({'vi','en','auto'})
    def __init__(self, store, audio, source_factory, publish, *, transcriber=None,
                 queue_size=512, max_s=300, stop_timeout_s=12,
                 live_transcriber=None, diarizer=None, finalize_transcript=None):
        self.store, self.audio = store, audio
        self.source_factory, self.publish = source_factory, publish
        self.transcriber = transcriber
        self.live_transcriber = live_transcriber
        self.use_live = transcriber is None or live_transcriber is not None
        self.diarizer = diarizer
        self.finalize_transcript = transcriber is None and live_transcriber is None if finalize_transcript is None else finalize_transcript
        self.capture_done = threading.Event()
        self.storage_ready = threading.Event()
        self.language = 'auto'
        self.language_lock = None
        self.queue_size, self.max_s = queue_size, max_s
        self.stop_timeout_s = stop_timeout_s
        self.active_id = None
        self.reprocess_id = None
        self.processing_id = None
        self.task = None
        self.source = None
        self.closed = False
        self.stop_requested = threading.Event()
        self.cancel_processing = threading.Event()
        self._reason_lock = threading.Lock()
        self._stop_reason = None
        self._capture_end_reason = None

    def start(self, selection, source_metadata, title, *, consent_confirmed):
        if consent_confirmed is not True:
            raise ValueError('Recording requires explicit consent')
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Recording or processing already active')
        self.language = source_metadata.get('language', 'auto')
        self.language_lock = None
        if self.language not in self.supported_languages:
            raise ValueError('Unsupported language hint')
        from ..capture.microphone_processing import MicrophoneProcessing
        self.microphone_processing = MicrophoneProcessing.model_validate(source_metadata.get('microphone_processing', {}))
        session = self.store.create_recording(title, source_metadata)
        self.store.set_audio_paths(session['id'], self.audio)
        self.active_id = session['id']
        with self._reason_lock:
            self._stop_reason = None
            self._capture_end_reason = None
        self.stop_requested.clear()
        self.cancel_processing.clear()
        self.capture_done.clear()
        self.storage_ready.clear()
        self.task = asyncio.create_task(self._run(session['id'], selection))
        return session

    def _request_stop(self, reason):
        with self._reason_lock:
            if self._stop_reason is None:
                self._stop_reason = reason
            self.stop_requested.set()

    def stop(self, session_id, *, reason='manual_stop'):
        if reason not in STOP_REASONS:
            raise ValueError('Invalid recording stop reason')
        if self.active_id != session_id:
            raise ValueError('That recording is not active')
        self._request_stop(reason)

    async def close(self):
        self.closed = True
        self.cancel_processing.set()
        self._request_stop('app_shutdown')
        if self.task:
            await self.task

    def _capture(self, session_id, selection, ready, checkpoint=None):
        writers = {}
        source = None
        producer = None
        abort = threading.Event()
        frames = queue.Queue(maxsize=self.queue_size)
        outcome = []
        created_key = False
        capture_failed = False
        samples = {'mic': 0, 'loopback': 0}
        selected_streams = ('mic',) if getattr(selection,'scope',None) == 'microphone' else tuple(samples)
        from ..capture.microphone_processing import MicrophoneProcessing, MicrophoneProcessor
        config = getattr(self, 'microphone_processing', MicrophoneProcessing())
        processor = MicrophoneProcessor(config)
        keep_raw = config.enabled or getattr(self,'keep_raw_microphone',False)
        synced = dict(samples)
        try:
            # Keys and both writers must exist before even constructing a source.
            reserve=self.max_s*16000*4*(len(selected_streams)+int(keep_raw))+50_000_000
            if shutil.disk_usage(self.audio.root.parent).free<reserve:
                raise OSError('Insufficient space for selected recording limit')
            self.audio.create_session(session_id)
            created_key = True
            for stream in samples:
                writers[stream] = self.audio.writer(session_id, stream)
            if keep_raw:
                writers['mic_raw'] = self.audio.raw_microphone_writer(session_id)
            self.storage_ready.set()
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
                    self._request_stop('duration_limit')
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
                if frame.stream not in selected_streams:
                    raise ValueError('Audio arrived outside the selected capture scope')
                if frame.t_ms+frame.pcm.size/16>(self.max_s+5)*1000:
                    raise ValueError('Audio exceeds selected recording timeline')
                if frame.stream == 'mic':
                    if keep_raw:
                        writers['mic_raw'].write(frame)
                    if config.enabled:
                        frame = processor.process(frame)
                writers[frame.stream].write(frame)
                samples[frame.stream] += frame.pcm.size
                if checkpoint and samples[frame.stream]-synced[frame.stream] >= 32000:
                    if shutil.disk_usage(self.audio.root.parent).free<50_000_000:
                        raise OSError('Recording disk reserve exhausted')
                    writers[frame.stream].checkpoint()
                    if frame.stream == 'mic' and keep_raw:
                        writers['mic_raw'].checkpoint()
                    synced[frame.stream] = samples[frame.stream]
                    checkpoint(frame.stream,round(writers[frame.stream].last_end))
                if not announced and all(samples[stream] for stream in selected_streams):
                    announced = True
                    ready()
            producer.join()
            if outcome:
                raise outcome[0]
            with self._reason_lock:
                self._capture_end_reason = getattr(source,'end_reason',None)
            if not all(samples[stream] for stream in selected_streams) and not self.stop_requested.is_set():
                raise RuntimeError('A selected capture stream produced no audio')
            for stream, writer in writers.items():
                writer.close()
                if checkpoint and stream in selected_streams:
                    checkpoint(stream,round(writer.last_end))
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
                self.audio.original_path(session_id, 'mic').with_name('mic.raw.slpa').unlink(missing_ok=True)
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
        capture_progress = {}
        def checkpoint(stream,end_ms):
            loop.call_soon_threadsafe(capture_progress.update,{stream:end_ms})
        def persist_progress():
            for stream,end_ms in list(capture_progress.items()):
                self.store.checkpoint(session_id,stream,recorded_ms=end_ms)
            capture_progress.clear()
        capture = asyncio.create_task(asyncio.to_thread(self._capture, session_id, selection, ready,checkpoint))
        ready_wait = asyncio.create_task(ready_event.wait())
        stopping = False
        live = asyncio.create_task(self._process(session_id, live=True, finalize=False)) if self.use_live else None
        diar = asyncio.create_task(self._diarize(session_id)) if self.use_live and self.diarizer is not False else None
        reported_live_error = False
        stage = 'capture'
        try:
            while not capture.done():
                await asyncio.wait({capture, ready_wait}, timeout=.1,
                                   return_when=asyncio.FIRST_COMPLETED)
                persist_progress()
                if ready_wait.done() and not stopping:
                    await self._state(session_id, 'recording')
                    # Avoid a completed Event wait spinning the event loop.
                    ready_wait = asyncio.create_task(asyncio.Event().wait())
                if live and live.done() and not reported_live_error and live.exception():
                    reported_live_error = True
                    log.warning('Live transcription failed (%s)',type(live.exception()).__name__)
                    await self.publish({'type':'transcription_error','session_id':session_id,
                            'message':'Transcription updates stopped; encrypted recording continues. Check the cloud key, network or quota, then resume after Stop.'})
                if self.stop_requested.is_set() and not stopping:
                    stopping = True
                    await self._state(session_id, 'stopping')
            samples = await capture
            stage = 'transcription'
            persist_progress()
            self.store.record_audio_duration(session_id, samples)
            with self._reason_lock:
                end_reason = resolve_end_reason(self._stop_reason,self._capture_end_reason)
            self.store.record_end(session_id,end_reason)
            self.capture_done.set()
            self.active_id = None
            self.processing_id = session_id
            await self._state(session_id, 'processing')
            if self.cancel_processing.is_set():
                await self._state(session_id, 'interrupted')
                return
            if live:
                await live
                if diar:
                    await diar
                await self._complete_live(session_id)
            else:
                await self._process(session_id)
        except Exception as exc:  # noqa: BLE001 - fail visibly without leaking text/PCM in logs
            self.stop_requested.set()
            with contextlib.suppress(Exception):
                await capture
            log.warning('Meeting recording/processing failed (%s)', type(exc).__name__)
            diagnostic = failure_diagnostic(exc,stage)
            with contextlib.suppress(*DATABASE_ERRORS):
                self.active_id = None  # Capture has drained before publishing failure.
                self.store.record_failure(session_id,diagnostic)
                if stage == 'capture':
                    self.store.record_end(session_id,'capture_failed:'+diagnostic['code'])
                await self._state(session_id, 'failed')
            await self.publish({'type': 'recording_error', 'session_id': session_id,
                'diagnostic':diagnostic,
                'message': ('Cannot confidently detect the early speech language. Select Tiếng Việt or English and transcribe again.'
                           if isinstance(exc, LanguageDetectionError) else diagnostic['message'])})
        finally:
            self.cancel_processing.set()
            self.capture_done.set()
            for task in (live, diar):
                if task:
                    with contextlib.suppress(Exception):
                        await task
            ready_wait.cancel()
            if self.active_id == session_id:
                self.active_id = None
            if self.processing_id == session_id:
                self.processing_id = None

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
        if language not in self.supported_languages:
            raise ValueError('Unsupported language hint')
        self.language = language
        prior = self.store.language_lock(session_id)
        self.language_lock = prior if language == 'auto' and prior and prior.get('mode') == 'auto' else None
        self.cancel_processing.clear()
        self.reprocess_id = session_id
        self.task = asyncio.create_task(self._reprocess(session_id))

    async def _reprocess(self, session_id):
        try:
            await self._state(session_id, 'processing')
            await self._process(session_id, replace=True)
        except Exception as exc:  # noqa: BLE001 - retain original transcript on model/storage failure
            log.warning('Meeting reprocessing failed (%s)', type(exc).__name__)
            diagnostic = failure_diagnostic(exc,'transcription')
            with contextlib.suppress(*DATABASE_ERRORS):
                self.store.record_failure(session_id,diagnostic)
                await self._state(session_id, 'failed')
            await self.publish({'type':'recording_error', 'session_id':session_id,
                'diagnostic':diagnostic,
                'message':('Cannot confidently detect the early speech language. Select Tiếng Việt or English and transcribe again.'
                           if isinstance(exc, LanguageDetectionError) else
                           diagnostic['message']+' Previous transcript retained.')})
        finally:
            self.reprocess_id = None

    async def _process(self, session_id, *, replace=False, live=False, finalize=True):
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
        if live:
            while not self.storage_ready.is_set() and not self.capture_done.is_set() and not self.cancel_processing.is_set():
                await asyncio.sleep(.01)
            transcriber = self.live_transcriber
            if transcriber is None:
                from .live import transcribe_live
                transcriber = transcribe_live
            processing = asyncio.create_task(asyncio.to_thread(transcriber,
                self.audio, session_id, emit, self.cancel_processing, self.capture_done,
                language=self.language, locked_language=self.language_lock,
                checkpoints=self.store.checkpoints(session_id),
                confirmed_text={s:' '.join(r['text'] for r in self.store.segments(session_id) if r['stream']==s)
                                for s in ('mic','loopback')}))
        else:
            processing = asyncio.create_task(asyncio.to_thread(self._transcribe,
                self.audio, session_id, emit, self.cancel_processing))
        try:
            while not processing.done() or not chunks.empty():
                try:
                    chunk = chunks.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.05)
                    continue
                if chunk.get('type') == 'cloud_request':
                    usage = self.store.reserve_cloud(session_id,chunk['reservation'])
                    chunk['ack'].set()  # Do not dispatch before the durable reservation.
                    await self.publish({'type':'cloud_usage','session_id':session_id,**usage})
                    continue
                if chunk.get('type') == 'recorded_checkpoint':
                    self.store.checkpoint(session_id,chunk['stream'],recorded_ms=chunk['end_ms'])
                    continue
                if chunk.get('type') == 'confirmed_window':
                    rows = self.store.commit_window(session_id,chunk['stream'],chunk['segments'],chunk['end_ms'])
                    for row in rows:
                        await self.publish({'type':'segment','session_id':session_id,'segment':row})
                    await self.publish({'type':'partial','session_id':session_id,'stream':chunk['stream'],
                                        'segment':{'text':''}})
                    continue
                if chunk.get('type') in {'partial','transcription_progress','model_metrics'}:
                    if chunk.get('mode') == 'streaming' and chunk.get('stream') in {'mic','loopback'}:
                        watermark = self.store.checkpoints(session_id).get(chunk['stream'],{}).get('recorded_ms',0)
                        chunk['recorded_ms'] = max(watermark,chunk.get('recorded_ms',0))
                        chunk['send_lag_ms'] = max(0,chunk['recorded_ms']-chunk.get('sent_ms',0))
                    await self.publish({**chunk,'session_id':session_id})
                    continue
                if chunk.get('type') == 'language_locked':
                    self.language_lock = chunk
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
        except BaseException:
            self.cancel_processing.set()
            raise
        finally:
            await processing
        if not finalize:
            return replacement if replace else None
        if self.closed:
            await self._state(session_id, 'interrupted')
        else:
            if replace:
                await self._replace_transcript(session_id,replacement)
            await self._finish(session_id)

    async def _replace_transcript(self, session_id, replacement):
        config = getattr(self,'cloud',None) or getattr(self,'config',None)
        provenance = config.public() if config is not None else {}
        self.store.revise_transcript(session_id, replacement,
            provider=provenance.get('provider',getattr(self,'provider',None)),
            model=provenance.get('model'))
        await self.publish({'type':'transcript_replaced','session_id':session_id,
                            'segments':self.store.segments(session_id),
                            'transcript_revisions':self.store.revisions(session_id)})

    async def _complete_live(self, session_id):
        if self.closed:
            await self._state(session_id,'interrupted')
        elif self.finalize_transcript:
            await self.publish({'type':'transcription_stage','session_id':session_id,'stage':'finalizing'})
            await self._process(session_id,replace=True)
        else:
            await self._finish(session_id)

    async def _finish(self, session_id):
        note = self.store.create_transcript_notes(session_id)
        await self.publish({'type':'notes','session_id':session_id,'notes':note})
        await self._state(session_id, 'interrupted' if self.closed else 'completed')

    async def _diarize(self, session_id):
        while not self.storage_ready.is_set() and not self.capture_done.is_set() and not self.cancel_processing.is_set():
            await asyncio.sleep(.01)
        from .diar import diarize_live
        events = queue.Queue(maxsize=8)
        cancelled = threading.Event()
        def emit(event):
            while not cancelled.is_set() and not self.cancel_processing.is_set():
                try:
                    events.put(event,timeout=.1)
                    return
                except queue.Full:
                    continue
        prior = self.store.turns(session_id)
        # Anonymous embeddings are intentionally not persisted; don't reuse an
        # old label after restart without fresh identity evidence.
        labels = [int(t['label'].split()[-1]) for t in prior if t.get('label') and t['label'].startswith('Speaker ')]
        checkpoint = self.store.checkpoints(session_id).get('loopback',{}).get('diarized_ms',0)
        worker = asyncio.create_task(asyncio.to_thread(self.diarizer or diarize_live,
            self.audio,session_id,emit,cancelled,self.capture_done,
            checkpoint=checkpoint,next_label=max(labels,default=0)+1))
        try:
            while not worker.done() or not events.empty():
                if self.cancel_processing.is_set():
                    cancelled.set()
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.05)
                    continue
                self.store.checkpoint(session_id,'loopback')
                self.store.save_turns(session_id,'loopback',event['turns'],event['end_ms'])
                await self.publish({'type':'speaker_turns','session_id':session_id,
                                    'turns':self.store.turns(session_id)})
            await worker
        except Exception as exc:  # noqa: BLE001 - model/storage boundary, retain encrypted data
            cancelled.set()
            if not self.cancel_processing.is_set():
                log.warning('Delayed diarization unavailable (%s)',type(exc).__name__)
                await self.publish({'type':'speaker_warning','session_id':session_id,
                    'message':'Speaker labels are unavailable. Recording and transcription remain independent; meeting voices stay unknown.'})
        finally:
            cancelled.set()
            with contextlib.suppress(Exception):
                await worker

    def _saved(self, session_id):
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Wait for recording and processing to finish')
        session = self.store.get(session_id)
        if not session or session['source'] != 'meeting' or not session['consent_confirmed']:
            raise ValueError('Select an authorized saved meeting')
        if session['status'] not in {'completed','failed','interrupted','deleting'}:
            raise ValueError('Saved meeting is not idle')
        return session

    def recover_saved(self, session_id):
        session = self._saved(session_id)
        if session['status'] == 'deleting':
            raise ValueError('Deletion is pending')
        self.task = asyncio.create_task(self._recover_saved(session_id))

    async def _recover_saved(self, session_id):
        try:
            await self._state(session_id,'processing')
            session = self.store.get(session_id)
            streams = ('mic',) if session.get('capture',{}).get('scope') == 'microphone' else ('mic','loopback')
            report = await asyncio.to_thread(self.audio.recover_session,session_id,streams=streams)
            self.store.set_audio_paths(session_id,self.audio)
            self.store.recovery_report(session_id,report)
            self.store.record_audio_duration(session_id,{s:r['samples'] for s,r in report.items()})
            await self._state(session_id,'interrupted')
            await self.publish({'type':'recovery_complete','session_id':session_id,
                'report':report,'message':'Authenticated audio recovered. Originals retained; missing tail duration is unknown. Capture was not restarted.'})
        except Exception as exc:  # noqa: BLE001 - model/storage boundary, retain encrypted data
            log.warning('Audio recovery failed (%s)',type(exc).__name__)
            await self._state(session_id,'failed')
            await self.publish({'type':'recording_error','session_id':session_id,
                'message':'Recovery failed authentication or storage checks. Originals retained; no capture opened.'})

    def resume(self, session_id, *, language='auto'):
        session = self._saved(session_id)
        if session['status'] == 'deleting' or language not in self.supported_languages:
            raise ValueError('Cannot resume this meeting')
        if self.store.segments(session_id) and not self.store.checkpoints(session_id):
            raise ValueError('Use Transcribe again for legacy batch transcripts')
        self.language = language
        prior = self.store.language_lock(session_id)
        self.language_lock = prior if language == 'auto' and prior else None
        self.cancel_processing.clear()
        self.capture_done.set()
        self.storage_ready.set()
        self.task = asyncio.create_task(self._resume(session_id))

    async def _resume(self, session_id):
        try:
            await self._state(session_id,'processing')
            await self._process(session_id,live=True,finalize=False)
            if self.diarizer is not False:
                await self._diarize(session_id)
            await self._complete_live(session_id)
        except Exception as exc:  # noqa: BLE001 - model/storage boundary, retain encrypted data
            log.warning('Catch-up failed (%s)',type(exc).__name__)
            await self._state(session_id,'interrupted')
            await self.publish({'type':'recording_error','session_id':session_id,
                'message':'Catch-up paused. Recover incomplete audio or select a language, then resume. Confirmed text is retained.'})
        finally:
            self.cancel_processing.set()

    def delete_saved(self, session_id):
        self._saved(session_id)
        self.store.begin_delete(session_id)
        self.task = asyncio.create_task(self._delete_saved(session_id))

    async def _delete_saved(self, session_id):
        try:
            await asyncio.to_thread(self.audio.delete_session,session_id)
            self.store.finish_delete(session_id)
            await self.publish({'type':'session_deleted','session_id':session_id})
        except Exception as exc:  # noqa: BLE001 - model/storage boundary, retain encrypted data
            log.warning('Session deletion pending (%s)',type(exc).__name__)
            await self.publish({'type':'recording_error','session_id':session_id,
                'message':'Deletion is pending. Retry Delete to finish cleanup. Backups and already unlocked readers are outside this guarantee.'})

    async def _state(self, session_id, status):
        session = self.store.transition(session_id, status)
        await self.publish({'type': 'session', 'session': session})


def transcribe_recording(audio, session_id, emit, cancelled, *, language='auto', locked_language=None):
    """Actual meeting path: local MOSS float16 + SDPA, explicit language hint."""
    from .meeting_asr import transcribe_moss
    return transcribe_moss(audio, session_id, emit, cancelled, language=language, locked_language=locked_language)
