"""Authenticated local replay and opt-in encrypted native meeting test workspace.

Native recording requires SQLCipher, macOS, explicit recording choice and an app agreement.
Cloud processing uses the durable first-use agreement; credentials stay in the backend.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import soundfile as sf
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..async_compat import timeout
from ..llm.ollama_lifecycle import release_all, release_if_small, session_targets
from ..llm.rolling import RollingNotes, summary_failure_code
from ..meetings.autocall import AutoCallMonitor
from ..meetings.service import DiscoveryService
from ..pipeline.languages import LanguageChoice, LanguageHints
from ..pipeline.recording import automatic_notes_eligible, recording_ready_for_notes
from ..pipeline.replay import ReplayRunner
from ..speakers.registry import SpeakerRegistry
from ..storage.db import DATABASE_ERRORS, connect, migrate
from ..storage.keys import KeyUnavailable
from ..storage.sessions import SessionStore
from .auth import check_first_message, host_allowed, new_token, origin_allowed

STATIC = Path(__file__).parent / 'static'
AUTH_TIMEOUT_S = 5
SEND_TIMEOUT_S = 5
AUTO_REASONS = {'app_inactive':'auto_app_inactive',
                'microphone_released':'auto_microphone_released'}


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


class SelectSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['select_source']
    request_id: int = Field(gt=0)
    scope: Literal['application', 'system', 'microphone']
    app_id: str | None = Field(default=None, min_length=1, max_length=256)
    microphone_id: str = Field(min_length=1, max_length=256)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=600)


class StartRecording(SelectSource):
    action: Literal['record']
    title: str = Field(default='Meeting test', min_length=1, max_length=160)
    language: LanguageChoice = 'auto'
    language_hints: LanguageHints | None = None
    consent_confirmed: bool = Field(strict=True)
    cloud_consent: bool = False
    model: Literal['deepgram','assemblyai','ollama'] = 'deepgram'
    duration_s: Literal[300,3600,10800] = 300

    @field_validator('title')
    @classmethod
    def valid_title(cls, value):
        value.encode('utf-8')
        if not value.strip():
            raise ValueError('Empty title')
        return value


class ReprocessRecording(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['reprocess']
    cloud_consent: bool = False
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    language: LanguageChoice = 'auto'
    language_hints: LanguageHints | None = None
    model: Literal['deepgram','assemblyai','ollama'] = 'deepgram'


class SavedMeeting(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['recover', 'resume', 'delete']
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    language: LanguageChoice = 'auto'
    language_hints: LanguageHints | None = None
    confirmed: bool = False
    cloud_consent: bool = False
    model: Literal['deepgram','assemblyai','ollama'] = 'deepgram'


class ConversationPage(BaseModel):
    """One bounded web transcript read; the snapshot itself stays metadata-only."""
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['conversation_page']
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    offset: int = Field(default=0, ge=0, le=10_000_000)
    anchor: str | None = Field(default=None, min_length=1, max_length=180)


class SpeakerCorrection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['rename_speaker','merge_speaker']
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=1)
    speaker_id: str = Field(min_length=1,max_length=180)
    label: str | None = Field(default=None,min_length=1,max_length=80)
    merge_target: str | None = Field(default=None,min_length=1,max_length=180)


class RetentionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['retention']
    days: int = Field(ge=1, le=3650)
    confirmed: bool


class SummaryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['summarize','export_markdown']
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=1)
    provider: str | None = Field(default=None,max_length=80)
    model: str | None = Field(default=None,max_length=100)
    text_consent: bool = False


class AcceptCall(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['accept_call']
    candidate_id: str = Field(min_length=1,max_length=600)
    app_id: str = Field(min_length=1,max_length=256)
    microphone_id: str = Field(default='default',min_length=1,max_length=256)
    model: Literal['deepgram','assemblyai','ollama'] = 'deepgram'
    duration_s: Literal[300,3600,10800] = 300
    language: LanguageChoice = 'auto'
    language_hints: LanguageHints | None = None
    consent_confirmed: bool
    automatic_summary: bool = False
    summary_provider: str | None = None
    summary_model: str | None = None
    summary_endpoint: str | None = None


class PersonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    action: Literal['create_person','link_person','set_person_learning','forget_person']
    name: str | None = Field(default=None,max_length=80)
    person_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{32}$')
    session_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{32}$')
    revision: int | None = Field(default=None,ge=1)
    speaker_id: str | None = Field(default=None,max_length=180)
    enabled: bool | None = None
    confirmed: bool = False


class DismissCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['dismiss_candidate']
    candidate_id: str = Field(min_length=1, max_length=600)


def create_app(*, database=None, fixtures=None, backend_factory=None,
               storage='memory', key_store=None, discovery_factory=None, recording_factory=None, cloud_config=None,
               require_launch_key=True, summary_provider=None, desktop=None, launch_token=None,
               source_factory=None, permission_request=None, local_config=None, account=None, email_exports=None,
               cleanup_provider=None, settings=None) -> tuple[FastAPI, str]:
    if storage not in {'memory', 'public-demo', 'encrypted'}:
        raise ValueError('Unknown storage mode')
    if (storage == 'memory') != (database is None):
        raise ValueError('Disk storage requires an explicit storage mode and database path')
    token = (launch_token or new_token()) if require_launch_key else ''
    # No provider request or health probe occurs during startup.
    summary_task = None
    auto_monitor = AutoCallMonitor()
    auto_tasks = set()
    closing = False
    automatic_scheduled = set()
    automatic_summary_lock = asyncio.Lock()
    rolling_signatures = {}
    fixtures = {name: Path(path).resolve() for name, path in (fixtures or {}).items()}
    fixture_rows = []
    for name, path in fixtures.items():
        info = sf.info(path)
        if not 0 < info.duration <= 300:
            raise ValueError('Public fixtures must be nonempty and at most five minutes')
        fixture_rows.append({'id': name, 'duration_s': info.duration})
    explicit_replay_backend = backend_factory is not None
    if backend_factory is None:
        def backend_factory():
            raise ValueError('Local inference is unavailable in the cloud-only app')

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
        session = event.get('session',{}) if event.get('type') == 'session' else {}
        if (session.get('source') == 'meeting' and session.get('status') == 'failed'
                and session.get('capture',{}).get('auto_workflow')):
            retry = getattr(getattr(app.state,'discovery',None),'recording_failed',None)
            if retry is not None:
                await retry(session['capture'].get('app_id'))
        if (session.get('source') == 'meeting' and session.get('status') in {'completed','failed','interrupted'}
                and app.state.audio):
            await publish({'type':'audio_state','session_id':session['id'],'audio':app.state.audio.status(session['id'])})
        if (not closing and recording_ready_for_notes(session) and
                session.get('capture',{}).get('auto_workflow') and session['id'] not in automatic_scheduled):
            automatic_scheduled.add(session['id'])
            recorder_task = getattr(app.state.recording,'task',None)
            task = asyncio.create_task(automatic_notes(session['id'],expected_task=recorder_task))
            auto_tasks.add(task)
            task.add_done_callback(auto_tasks.discard)

    async def automatic_notes(session_id, *, grant_override=None, expected_task=None, previous_revision=None):
        async with automatic_summary_lock:
            await _automatic_notes(session_id,grant_override=grant_override,
                expected_task=expected_task,previous_revision=previous_revision)

    async def _automatic_notes(session_id, *, grant_override=None, expected_task=None, previous_revision=None):
        nonlocal summary_task
        recorder = app.state.recording
        if expected_task is not None:
            await asyncio.shield(expected_task)
        elif recorder and recorder.task:
            await asyncio.shield(recorder.task)
        # A busy notes request queues this job; it must not consume the sole
        # automatic scheduling opportunity and force a later manual Generate.
        while summary_task is not None:
            pending = summary_task
            with contextlib.suppress(Exception):
                await asyncio.shield(pending)
        if closing or not app.state.store.agreement()['accepted']:
            return
        store = app.state.store
        session = store.get(session_id)
        if not session or (previous_revision is not None and (session['status'] != 'completed' or
                session['transcript_revision'] <= previous_revision)):
            return
        grant = grant_override or session.get('capture',{}).get('auto_summary')
        if not grant:
            return
        if not grant_override:
            count = store.conn.execute('SELECT COUNT(*) FROM segments WHERE session_id=? AND revision>=1',
                                       (session_id,)).fetchone()[0]
            if not automatic_notes_eligible(session_id,session.get('audio_duration_ms'),count):
                with store.conn:
                    store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) '
                        "VALUES (?,?,?,'automatic_notes_skipped',?)",(session_id,store._now(),'info',
                        json.dumps({'message':'Call ended early — nothing to summarize'})))
                await publish({'type':'session','session':store.get(session_id)})
                return
        try:
            attempt = 'manual_rerun_summary_attempt' if grant_override else 'automatic_summary_attempt'
            previous = store.conn.execute('SELECT payload_json FROM events WHERE session_id=? AND type=?',
                                          (session_id,attempt)).fetchall()
            if any(not grant_override or json.loads(row[0]).get('revision') == session['transcript_revision']
                   for row in previous):
                return
            if (summary_task is not None or not summary_provider or not summary_provider.public()['configured'] or
                    grant != {key:summary_provider.public()[key] for key in ('provider','model','endpoint')}):
                raise ValueError('Summary endpoint changed or busy')
            _,fingerprint,messages = store.summary_input(session_id,session['transcript_revision'],build_messages=False)
            with store.conn:
                store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                    (session_id,store._now(),'info',attempt,json.dumps({**grant,'input_hash':fingerprint,
                        'revision':session['transcript_revision']})))
            summary_task = asyncio.create_task(generate_summary(session_id,session['transcript_revision'],fingerprint,messages,automatic=True))
            summary_task.session_id = session_id
            await publish({'type':'summary_started','session_id':session_id})
        except (ValueError,*DATABASE_ERRORS):
            with contextlib.suppress(*DATABASE_ERRORS):
                store.record_summary_outcome(session_id,'summary_setup_invalid')
            await publish({'type':'summary_error','session_id':session_id,
                'message':'Recording saved. Automatic notes are unavailable; open Meeting notes to generate them manually.'})

    async def monitor_calls():
        previous = None
        while True:
            await asyncio.sleep(1)
            recorder = app.state.recording
            sid = auto_monitor.session_id
            if not sid or not recorder:
                continue
            if recorder.active_id != sid:
                auto_monitor.clear()
                continue
            state,ended = auto_monitor.observe(app.state.discovery.snapshot())
            current = {'type':'auto_call_status','session_id':sid,**state}
            if current != previous:
                previous = current
                await publish(current)
            if ended:
                recorder.stop(sid,reason=AUTO_REASONS.get(state.get('reason'),'auto_app_inactive'))
                auto_monitor.clear()

    def recording_busy():
        recorder = app.state.recording
        ask_busy=any(not task.done() for task in app.state.ask_tasks)
        return bool(ask_busy or app.state.runner.active_id or (recorder and (recorder.active_id or
                    (recorder.task and not recorder.task.done()))))

    def recording_state():
        recorder = app.state.recording
        sid = recorder.active_id if recorder else None
        return {'type':'recording_state','active_id':sid,
            'paused':bool(sid and getattr(recorder,'paused',False)),
            'stopping':bool(sid and recorder.stop_requested.is_set()),
            'processing_id':(getattr(recorder,'reprocess_id',None) or
                             getattr(recorder,'processing_id',None)) if recorder else None}

    async def maintain_audio():
        while True:
            retained = app.state.audio
            if retained and not recording_busy() and app.state.store.agreement()['accepted']:
                try:
                    async def expired(sid,retained=retained):
                        await publish({'type':'audio_state','session_id':sid,'audio':retained.status(sid)})
                    await retained.cleanup(authorized=lambda:app.state.store.agreement()['accepted'],
                                           on_expired=expired)
                except (OSError,KeyUnavailable,*DATABASE_ERRORS):
                    # No plaintext/error paths in logs. Key-first removal is retryable.
                    await publish({'type':'audio_cleanup_error',
                        'message':'Audio cleanup is pending; it will retry while idle. Saved notes are preserved.'})
            await asyncio.sleep(60)

    async def monitor_rolling():
        nonlocal summary_task
        while True:
            await asyncio.sleep(5)
            recorder=app.state.recording
            if not recorder or not recorder.active_id or summary_task is not None or not summary_provider or not summary_provider.public()['configured']:
                continue
            sid=recorder.active_id
            session=app.state.store.get(sid)
            if (not app.state.store.agreement()['accepted'] or (session.get('capture',{}).get('duration_s') or 300)<=300
                    or session['capture'].get('auto_summary')!={key:summary_provider.public()[key] for key in ('provider','model','endpoint')}):
                continue
            from ..llm.grounded import transcript_hash
            rows=app.state.store.segments(sid)
            signature=transcript_hash(rows)
            if not rows or signature==rolling_signatures.get(sid):
                continue
            rolling_signatures[sid]=signature
            summary_task=asyncio.create_task(generate_rolling(sid,rows,session['transcript_revision']))
            summary_task.session_id=sid
            summary_task.rolling=True

    async def generate_rolling(sid,rows,revision):
        nonlocal summary_task
        try:
            engine=RollingNotes(app.state.store,summary_provider,authorized=lambda:app.state.store.agreement()['accepted'])
            notes=await engine.run(sid,rows,revision=revision,rolling=True)
            if notes:
                await publish({'type':'rolling_notes','session_id':sid,'summary':notes[-1],'provider':summary_provider.provider,'model':summary_provider.model,'budget':engine.public(sid)})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - preserve prior draft, redact provider data
            await publish({'type':'rolling_notes_error','session_id':sid,
                'message':'Section notes paused. Saved transcript is safe; Generate summary can explicitly retry.'})
        finally:
            summary_task=None

    async def generate_summary(session_id, revision, fingerprint, messages, *, retry=False, automatic=False):
        nonlocal summary_task
        try:
            async with timeout(3600):
                engine=RollingNotes(app.state.store,summary_provider,authorized=lambda:app.state.store.agreement()['accepted'])
                value=await engine.run(session_id,app.state.store.segments(session_id),revision=revision,
                    retry=retry,repair_invalid=automatic)
            saved = app.state.store.save_grounded_summary(session_id,revision,fingerprint,value,
                provider=summary_provider.provider,model=summary_provider.model)
            app.state.store.record_summary_outcome(session_id)
            await publish({'type':'summary_ready','session_id':session_id,'summary':saved,
                'session':app.state.store.get(session_id),'budget':engine.public(session_id)})
            if summary_provider.provider == 'Ollama' and not summary_provider.cloud:
                # The notes task is over: free small local models immediately.
                with contextlib.suppress(Exception):
                    await release_if_small(summary_provider.base_url,summary_provider.model)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - redact provider bodies and transcript
            # Never expose provider bodies, transcript snippets, keys or raw exceptions.
            code=summary_failure_code(exc)
            with contextlib.suppress(*DATABASE_ERRORS):
                app.state.store.record_summary_outcome(session_id,code)
            await publish({'type':'summary_error','session_id':session_id,
                'message':f'Notes could not finish. Recording and transcript are saved. Diagnostic: {code}. Generate notes can retry.'})
        finally:
            summary_task = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal closing
        from contextlib import closing as close_connection

        from ..storage.encrypted import encrypted_database

        workspace_keys = None
        def unlock_audio(key, native_keys):
            nonlocal workspace_keys
            from ..storage.workspace_keys import WorkspaceAudioKeys
            path = Path(database).resolve()
            workspace_keys = WorkspaceAudioKeys(path.with_name(path.name + '.audio-keys'), key, native_keys)

        manager = (encrypted_database(Path(database), key_store, on_unlock=unlock_audio) if storage == 'encrypted'
                   else close_connection(migrate(connect(database or ':memory:'))))
        # Keychain access occurs during startup, before any recording or UI work.
        with manager as conn:
            store = SessionStore(conn)
            store.recover()
            app.state.store = store
            app.state.memory_cache = store.memory_cache
            app.state.desktop_database = Path(database).resolve() if database else None
            app.state.runner = ReplayRunner(store, backend_factory, publish)
            app.state.storage = storage
            app.state.recording = None
            app.state.audio = None
            if storage == 'encrypted' and (sys.platform == 'darwin' or recording_factory or source_factory):
                from ..pipeline.model_selection import CloudRecordingRouter
                from ..storage.audio import SessionAudioStore
                if source_factory:
                    platform_source = source_factory
                elif sys.platform == 'darwin':
                    from ..capture.macos_client import MacOSMeetingAudioSource
                    platform_source = MacOSMeetingAudioSource
                elif sys.platform.startswith('linux'):
                    from ..capture.linux_pipewire import LinuxMeetingAudioSource
                    platform_source = LinuxMeetingAudioSource
                else:
                    raise ValueError('Encrypted recording requires a platform capture source')
                audio = SessionAudioStore(Path(database).resolve().parent / 'recordings',
                                          workspace_keys,memory_cache=store.memory_cache)
                if recording_factory:
                    recording = recording_factory(store,audio,platform_source,publish)
                elif desktop is not None or local_config is not None:
                    from ..pipeline.model_selection import TranscriptionRouter
                    recording = TranscriptionRouter(store,audio,platform_source,
                        publish,cloud_config=cloud_config,local_config=local_config,
                        cleanup_provider=cleanup_provider)
                else:
                    recording = CloudRecordingRouter(store,audio,platform_source,publish,
                        cloud_config=cloud_config,cleanup_provider=cleanup_provider)
                app.state.recording = recording
                from ..storage.retained_audio import RetainedAudio
                app.state.audio = RetainedAudio(store,audio)
            app.state.discovery = (discovery_factory or DiscoveryService)(publish)
            monitor_task = None
            retention_task = None
            rolling_task = None
            try:
                if desktop and not desktop.settings.value.detection_enabled:
                    await app.state.discovery.set_enabled(False)
                await app.state.discovery.start()
                closing = False
                monitor_task = asyncio.create_task(monitor_calls())
                retention_task = asyncio.create_task(maintain_audio())
                rolling_task = asyncio.create_task(monitor_rolling())
                yield
            finally:
                closing = True
                pending_settings = getattr(app.state,'settings_task',None)
                if pending_settings is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.shield(pending_settings)
                for task in list(getattr(app.state,'ollama_checks',set())):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if rolling_task is not None:
                    rolling_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await rolling_task
                if retention_task is not None:
                    retention_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await retention_task
                if monitor_task is not None:
                    monitor_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await monitor_task
                for task in list(auto_tasks):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                try:
                    if account is not None:
                        await account.cancel()
                    if summary_task is not None:
                        summary_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await summary_task
                    await app.state.discovery.close()
                finally:
                    try:
                        if app.state.recording is not None:
                            await app.state.recording.close()
                        await app.state.runner.close()
                    finally:
                        # Quitting releases local Ollama models; small ones are
                        # usually already gone after their tasks. Bounded and
                        # best-effort so shutdown never stalls on a stuck server.
                        if storage == 'encrypted':
                            await release_all(session_targets(summary_provider,app.state.recording))
                        if app.state.audio is not None:
                            app.state.audio.audio.clear_unlocked_keys()
                        store.memory_cache.clear()
                        if workspace_keys is not None:
                            workspace_keys.close()

    app = FastAPI(title='ScribeLoop', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.desktop_contract = desktop
    app.state.account = account
    app.state.settings_saving = False
    app.state.settings_task = None
    app.state.email_exports = email_exports
    app.state.ask_tasks = set()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; media-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/healthz')
    async def healthz():
        return {'status': 'ok'}

    @app.get('/')
    async def index():
        if desktop:
            return HTMLResponse('Desktop API only', status_code=404)
        html = (STATIC/'index.html').read_text(encoding='utf-8')
        if settings:
            html = html.replace('LOCAL DEVELOPMENT', 'UBUNTU PREVIEW').replace(
                'Local development notebook', 'Ubuntu meeting notebook')
            html = html.replace('Open your call. We’ll ask before taking notes.',
                                'Choose your source and start a recording.')
            html = html.replace('Approved calls stop after app audio activity ends, then notes are prepared automatically.',
                                'Start and stop manually in this Ubuntu preview.')
            html = html.replace('OS microphone and Screen Recording permissions are still required.',
                                'A working desktop sound server and microphone are still required.')
        return HTMLResponse(html.replace('<html lang="en">',
            '<html lang="en" data-require-launch-key="'+str(require_launch_key).lower()+'">'))

    @app.get('/app.js')
    async def javascript():
        if desktop:
            return HTMLResponse('', status_code=404)
        return FileResponse(STATIC / 'app.js', media_type='text/javascript')

    @app.get('/app.css')
    async def stylesheet():
        if desktop:
            return HTMLResponse('', status_code=404)
        return FileResponse(STATIC / 'app.css', media_type='text/css')

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        host = ws.headers.get('host', '')
        if (not host_allowed(host) or not origin_allowed(ws.headers.get('origin'), host)
                or (not require_launch_key and (ws.headers.get('origin') is None
                    or ws.client is None or ws.client.host not in {'127.0.0.1','::1'}))):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=128)
        sender = reader = None
        playback_pending = False
        ask_task = None

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
            if desktop:
                event = desktop.event(event)
                desktop.check_size(event)
            await asyncio.wait_for(ws.send_json(event), timeout=SEND_TIMEOUT_S)

        def resync_cleanup(recorder):
            """Re-sync idle cleanup engines with the latest saved settings.

            A Preferences save reconfigures the recorder too, but a save that
            was rejected as busy would otherwise leave engines validating
            against the rules from before the change (for example the
            Experience skip-validation toggle).
            """
            if desktop is None or recorder is None:
                return
            refresh = getattr(recorder,'refresh_cleanup',None)
            if refresh is not None:
                refresh(desktop.settings.value.cleanup())
            elif hasattr(recorder,'cleanup_provider'):
                recorder.cleanup_provider = desktop.settings.value.cleanup()

        try:
            if require_launch_key:
                message = await asyncio.wait_for(text_frame(256), timeout=AUTH_TIMEOUT_S)
                if not check_first_message(message, token):
                    await ws.close(code=1008)
                    return
            clients.add(queue)
            store = app.state.store
            if desktop:
                await send(desktop.snapshot(app, summary_provider,
                    app.state.runner.active_id or (app.state.recording.active_id if app.state.recording else None),
                    getattr(summary_task,'session_id',None)))
            else:
                history = store.list()
                # Web history is metadata-only. Transcript bytes, notes and
                # summaries load one bounded conversation at a time below; this
                # keeps reconnect construction off the O(all-transcripts) path.
                await send({'type': 'snapshot', 'paged': True, 'fixtures': fixture_rows,
                    'agreement':store.agreement(),
                    'settings':settings.value.public() if settings else None,
                    'people':SpeakerRegistry(store).list(),
                    'summary_budgets':{row['id']:RollingNotes(store,summary_provider).public(row['id']) for row in history},
                    'audio_states':{s['id']:app.state.audio.status(s['id']) for s in history if app.state.audio and s['source']=='meeting'},
                    'auto_call':{'session_id':auto_monitor.session_id},
                    'summary_provider':summary_provider.public() if summary_provider else {'configured':False},
                    'summary_active_id':getattr(summary_task,'session_id',None),
                    'sessions': history, 'active_id': app.state.runner.active_id or
                        (app.state.recording.active_id if app.state.recording else None),
                    'recording_available': app.state.recording is not None,
                    'transcription': app.state.recording.cloud.public() if app.state.recording is not None and hasattr(app.state.recording,'cloud') else {'cloud':False,'configured':True,'provider':'test'},
                    'transcription_models': app.state.recording.profiles() if app.state.recording is not None and hasattr(app.state.recording,'profiles') else {},
                    'replay_available': not app.state.runner.unavailable,
                    'replay_enabled': explicit_replay_backend, 'storage': app.state.storage,
                    'discovery': app.state.discovery.snapshot(),
                    'transcript_revisions': {s['id']: store.revisions(s['id']) for s in history}})

            def conversation_page(command):
                request = ConversationPage.model_validate(command)
                session = store.get(request.session_id)
                if not session or session['status'] == 'deleting':
                    raise ValueError('Select a saved conversation')
                revision = session['transcript_revision']
                count = store.conn.execute('SELECT COUNT(*) FROM segments WHERE session_id=? AND revision=?',
                                           (request.session_id,revision)).fetchone()[0]
                offset = request.offset
                if request.anchor is not None:
                    position = store.conn.execute('SELECT n FROM (SELECT id, ROW_NUMBER() OVER '
                        '(ORDER BY start_ms,rowid)-1 AS n FROM segments WHERE session_id=? AND revision=?) WHERE id=?',
                        (request.session_id,revision,request.anchor)).fetchone()
                    if not position:
                        raise ValueError('Citation is from a different revision')
                    offset = (position[0] // 500) * 500
                if count and offset >= count:
                    offset = max(0,count-500)
                rows = store.segments(request.session_id,revision=revision,limit=500,offset=offset) if revision else []
                return {'type':'conversation_page', 'session_id':request.session_id, 'revision':revision,
                    'offset':offset, 'total':count, 'segments':rows,
                    'notes':store.notes(request.session_id)[:100000],
                    'summary':store.grounded_summary(request.session_id),
                    'rolling':RollingNotes(store,summary_provider).latest(request.session_id),
                    'budget':RollingNotes(store,summary_provider).public(request.session_id),
                    'speaker_turns':store.turns(request.session_id)[:10000],
                    'language_lock':store.language_lock(request.session_id),
                    'cloud_usage':store.cloud_usage(request.session_id),
                    'audio':app.state.audio.status(request.session_id)
                        if app.state.audio and session['source']=='meeting' else {}}

            async def send_events():
                nonlocal playback_pending
                while True:
                    event = await queue.get()
                    await send(event)
                    if event['type'] == 'audio_playback':
                        playback_pending = False
                    if event['type'] == 'reconnect_required':
                        await ws.close(code=1013)
                        return

            async def receive_commands():
                nonlocal summary_task,playback_pending,summary_provider,ask_task
                while True:
                    command = None
                    owns_playback = False
                    dispatch_id = None
                    try:
                        raw = await text_frame(12288 if desktop else 4096)
                        command = json.loads(raw)
                        if not isinstance(command, dict):
                            raise TypeError('Expected an object')
                        if desktop:
                            dispatch_id, command = desktop.unwrap(command)
                            if dispatch_id in desktop.receipts:
                                await queue.put(desktop.receipts[dispatch_id])
                                continue
                            action = command.get('action')
                            response = await desktop.read(app,command,summary_provider,
                                app.state.recording.active_id if app.state.recording else None,
                                getattr(summary_task,'session_id',None))
                            if response is not None:
                                await queue.put({**response,'response_to':dispatch_id})
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if app.state.settings_saving and action not in {'stop','stop_recording','revoke_agreement'}:
                                raise ValueError('Wait for the settings save to finish')
                            desktop.receipt(dispatch_id,'dispatching')
                            if action == 'delete_ask_history':
                                if any(not task.done() for task in app.state.ask_tasks):
                                    raise ValueError('Wait for Ask to finish before deleting history')
                                response=desktop.delete_ask_history(store,command)
                                await publish({**response,'response_to':dispatch_id})
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'ask_question':
                                from ..llm.meeting_ask import AskScope, MeetingAsk
                                from ..storage.search_work import (
                                    SearchAuthorizationError,
                                    SearchWork,
                                )
                                desktop.settings.value.require_hybrid_for_ask()
                                ask_embedding_config=desktop.settings.value.embedding()
                                allowed={'action','question','scope','current_id','selected_ids','previous_answer_id'}
                                selected=command.get('selected_ids')
                                current=command.get('current_id')
                                def valid_id(value):
                                    return (isinstance(value,str) and len(value)==32 and
                                            all(c in '0123456789abcdef' for c in value))
                                if (set(command)-allowed or ask_task and not ask_task.done() or recording_busy() or
                                        summary_task is not None or not store.agreement()['accepted'] or
                                        not summary_provider or not summary_provider.public()['configured'] or
                                        (selected is not None and (not isinstance(selected,list) or
                                         any(not valid_id(value) for value in selected))) or
                                        (current is not None and not valid_id(current))):
                                    raise ValueError('Ask is unavailable or invalid')
                                scope=AskScope.resolve(store,command.get('scope'),current_id=current,selected_ids=selected)
                                question=command.get('question')
                                previous_answer_id=command.get('previous_answer_id')
                                if previous_answer_id is not None and not valid_id(previous_answer_id):
                                    raise ValueError('Invalid prior Ask answer')
                                ask_provider=summary_provider
                                ask_work=SearchWork(authorize=store.search_authorization())
                                async def run_ask(request_id=dispatch_id,question=question,scope=scope,
                                                  ask_provider=ask_provider,previous_answer_id=previous_answer_id,
                                                  ask_embedding_config=ask_embedding_config,ask_work=ask_work):
                                    try:
                                        await publish({'type':'ask_started','request_id':request_id,
                                            'scope':{'kind':scope.kind,'meeting_ids':list(scope.meeting_ids)}})
                                        result=await MeetingAsk(store,ask_provider,
                                            embedding_config=ask_embedding_config,work=ask_work).answer(
                                            question,scope,previous_answer_id=previous_answer_id)
                                        ask_work.check()
                                        await publish({'type':'ask_answer','request_id':request_id,**result})
                                    except asyncio.CancelledError:
                                        await publish({'type':'ask_cancelled','request_id':request_id})
                                        raise
                                    except SearchAuthorizationError:
                                        await publish({'type':'ask_cancelled','request_id':request_id})
                                    except Exception as error:  # noqa: BLE001 - sanitize provider bodies and keys
                                        from ..llm.meeting_ask import ask_failure
                                        failure=ask_failure(error)
                                        with contextlib.suppress(*DATABASE_ERRORS), store.conn:
                                            store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                                                (None,store._now(),'error','ask_failed',json.dumps({'code':failure['code']})))
                                        await publish({'type':'ask_error','request_id':request_id,
                                            'diagnostic':failure['code'],'message':failure['message']})
                                ask_task=asyncio.create_task(run_ask())
                                app.state.ask_tasks.add(ask_task)
                                ask_task.add_done_callback(app.state.ask_tasks.discard)
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'cancel_ask':
                                if set(command)!={'action'} or not ask_task or ask_task.done():
                                    raise ValueError('No Ask request is running')
                                ask_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await ask_task
                                ask_task=None
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action in {'account_sign_in','account_cancel','account_refresh','account_sign_out'}:
                                try:
                                    if set(command)!={'action'} or account is None or not account.config.configured:
                                        raise ValueError('Account service is not configured')
                                    if recording_busy():
                                        raise ValueError('Finish recording and processing before changing accounts')
                                    if action == 'account_sign_in':
                                        url = await account.begin(publish)
                                        await queue.put({'type':'account_authorize','url':url})
                                    elif action == 'account_cancel':
                                        await account.cancel()
                                    elif action == 'account_refresh':
                                        await account.refresh()
                                    else:
                                        await account.sign_out()
                                    await publish({'type':'account_state',**account.public()})
                                    await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                except Exception:  # noqa: BLE001 - account/provider errors must not expose tokens
                                    if account is not None:
                                        account.error = 'Account action could not finish. Check account-service setup or connection and try again.'
                                        await publish({'type':'account_state',**account.public()})
                                    await queue.put({'type':'error','context':'account',
                                        'message':'Account action could not finish. Check setup or connection and try again; saved meetings are unchanged.'})
                                    await queue.put(desktop.receipt(dispatch_id,'rejected'))
                                continue
                            if action == 'check_capture_access':
                                if recording_busy() or set(command)!={'action'}:
                                    raise ValueError('Wait for capture to finish')
                                from ..capture.devices import check_capture_access
                                result = await asyncio.to_thread(check_capture_access,desktop.helper)
                                await queue.put({'type':'capture_access',**result})
                                await app.state.discovery.refresh()
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                            elif action == 'check_ollama':
                                from ..pipeline.local_transcription import (
                                    LocalTranscriptionConfig,
                                    ensure_model_ready,
                                )
                                checks = getattr(app.state,'ollama_checks',None)
                                if checks is None:
                                    checks = app.state.ollama_checks = set()
                                try:
                                    if (recording_busy() or summary_task is not None
                                            or set(command) not in ({'action'},{'action','model'})):
                                        raise ValueError('Wait for active work to finish before checking Ollama.')
                                    if any(not task.done() for task in checks):
                                        raise ValueError('A local model check is already running.')
                                    model = command.get('model',local_config.model if local_config else '')
                                    if not isinstance(model,str) or len(model)>128:
                                        raise ValueError('Enter a valid local audio model name.')
                                    check_config = LocalTranscriptionConfig(model=model,
                                        endpoint=(desktop.settings.value.local_transcription_endpoint if desktop
                                                  else local_config.base_url if local_config else None))
                                    if not check_config.configured:
                                        raise ValueError('Choose a local Ollama audio model in Preferences first.')
                                except ValueError as exc:
                                    await queue.put({'type':'local_model_status','state':'unavailable','status':str(exc),'message':str(exc)})
                                    if desktop:
                                        await queue.put(desktop.receipt(dispatch_id,'rejected'))
                                    continue
                                task = asyncio.create_task(ensure_model_ready(check_config,publish))
                                checks.add(task)
                                task.add_done_callback(checks.discard)
                                if desktop:
                                    await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'name_speaker':
                                if (recording_busy() or summary_task is not None or
                                        set(command) not in ({'action','session_id','revision','speaker_id','name'},
                                                            {'action','session_id','revision','speaker_id','person_id'})):
                                    raise ValueError('Wait for saved transcript and select a speaker')
                                from ..speakers.registry import SpeakerRegistry as Registry
                                Registry(store).name_speaker(command['session_id'],command['revision'],command['speaker_id'],
                                    name=command.get('name'),person_id=command.get('person_id'))
                                await publish({'type':'invalidate','session_id':command['session_id']})
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'rename_conversation':
                                sid=command.get('session_id')
                                if (recording_busy() or summary_task is not None or auto_tasks or
                                        set(command)!={'action','session_id','title'} or
                                        not isinstance(sid,str) or len(sid)!=32 or
                                        any(c not in '0123456789abcdef' for c in sid)):
                                    raise ValueError('Select an idle saved conversation')
                                session=store.rename_conversation(sid,command['title'])
                                await publish({'type':'conversation_renamed','session':session})
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'save_settings':
                                if recording_busy() or summary_task is not None or auto_tasks or set(command)!={'action','settings'}:
                                    raise ValueError('Wait for all processing before changing providers')
                                candidate = desktop.settings.prepare(command['settings'])
                                app.state.settings_saving = True
                                async def commit_settings(candidate=candidate,rid=dispatch_id):
                                    nonlocal summary_provider
                                    try:
                                        await asyncio.to_thread(desktop.settings.persist,candidate)
                                        desktop.settings.value = candidate
                                        summary_provider = candidate.summary()
                                        if app.state.recording:
                                            configure = getattr(app.state.recording,'configure',None)
                                            if configure is not None:
                                                configure(cloud_config=desktop.settings.cloud(),
                                                    local_config=candidate.local_transcription(),
                                                    cleanup_provider=candidate.cleanup())
                                            else:
                                                app.state.recording.base_config = desktop.settings.cloud()
                                                app.state.recording.cloud = app.state.recording.base_config
                                                app.state.recording.cleanup_provider = candidate.cleanup()
                                        await publish({'type':'settings','settings':candidate.public(),
                                            'summary_provider':summary_provider.public(),
                                            'transcription':app.state.recording.cloud.public() if app.state.recording else {}})
                                        await publish(desktop.receipt(rid,'accepted'))
                                    except Exception:
                                        await publish(desktop.receipt(rid,'rejected'))
                                        raise
                                    finally:
                                        app.state.settings_saving = False
                                task = app.state.settings_task = asyncio.create_task(commit_settings())
                                # Owned by the app: disconnecting a window cannot abandon
                                # a completed Keychain write or lose its retained receipt.
                                task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
                                await asyncio.shield(task)
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'link_calendar_event':
                                from .calendar_links import save_link
                                await publish(save_link(store,command))
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'prepare_email_export':
                                if recording_busy() or summary_task is not None:
                                    raise ValueError('Finish recording and processing before sharing')
                                if app.state.email_exports is None:
                                    from .email_exports import EmailExports
                                    app.state.email_exports = EmailExports()
                                try:
                                    result = await app.state.email_exports.prepare(store,app.state.audio,command)
                                except asyncio.CancelledError:
                                    # Reconnecting clients must not remain stuck on a
                                    # dispatching receipt after a canceled local export.
                                    await publish(desktop.receipt(dispatch_id,'rejected'))
                                    raise
                                await queue.put({**result,'response_to':dispatch_id})
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'discard_email_exports':
                                if set(command)!={'action','export_ids'}:
                                    raise ValueError('Invalid export cleanup')
                                if app.state.email_exports is not None:
                                    await asyncio.to_thread(app.state.email_exports.discard,command['export_ids'])
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                            if action == 'set_detection':
                                if recording_busy() or set(command)!={'action','enabled'}:
                                    raise ValueError('Stop recording before pausing observation')
                                candidate = desktop.settings.prepare({'detection_enabled':command['enabled']})
                                await asyncio.to_thread(desktop.settings.persist,candidate)
                                desktop.settings.value = candidate
                                await app.state.discovery.set_enabled(command['enabled'])
                                await queue.put(desktop.receipt(dispatch_id,'accepted'))
                                continue
                        action = command.get('action')
                        if not desktop and action == 'conversation_page':
                            await queue.put(conversation_page(command))
                            continue
                        if settings and not desktop and action == 'save_settings':
                            if (recording_busy() or summary_task is not None or auto_tasks
                                    or set(command) != {'action','settings'}):
                                raise ValueError('Wait for processing before changing providers')
                            await asyncio.to_thread(settings.save,command['settings'])
                            summary_provider = settings.value.summary()
                            configure_cloud = getattr(app.state.recording,'configure_cloud',None)
                            if configure_cloud is not None:
                                configure_cloud(settings.cloud())
                            elif app.state.recording:
                                app.state.recording.base_config = settings.cloud()
                                app.state.recording.cloud = app.state.recording.base_config
                            await publish({'type':'settings','settings':settings.value.public(),
                                'summary_provider':summary_provider.public(),
                                'transcription':app.state.recording.cloud.public()
                                    if app.state.recording else {}})
                            continue
                        if desktop and action in {'resume','reprocess'}:
                            sid = command.get('session_id')
                            if not isinstance(sid,str) or not store.get(sid):
                                raise ValueError('Select a saved conversation')
                            expected = desktop.settings.value.transcription_engine
                            if action == 'resume':
                                previous = store.transcript_provider(sid)
                                known = {'deepgram':'deepgram','assemblyai':'assemblyai','local-ollama':'ollama'}
                                if previous in known:
                                    expected = known[previous]
                                elif store.segments(sid):
                                    raise ValueError('Use Transcribe again to replace an unsupported transcript')
                            if command.get('model',expected) != expected:
                                raise ValueError('Transcription engine changed; review and try again')
                            command['model'] = expected
                        retained = app.state.audio
                        if retained and retained.busy and action in {'record','accept_call','resume','reprocess','recover','delete','retention','archive_audio','play_audio'}:
                            raise ValueError('Wait for the audio reader or cleanup')
                        if action in {'resume','reprocess'} and retained:
                            retained.require(command.get('session_id'))
                        if action in {'record','accept_call','resume','reprocess','summarize'}:
                            if not store.agreement()['accepted']:
                                await queue.put({'type':'error','context':'meeting',
                                    'message':'Accept the first-use agreement before recording or processing.'})
                                if desktop:
                                    await queue.put(desktop.receipt(dispatch_id,'rejected'))
                                continue
                            # Legacy wire fields are accepted, but authorization now comes
                            # exclusively from the durable, versioned app agreement.
                            for field in ('consent_confirmed','cloud_consent','text_consent'):
                                if field in command:
                                    if not isinstance(command[field],bool):
                                        raise ValueError('Invalid legacy authorization field')
                                    command[field] = True
                            if action in {'record','accept_call'}:
                                command['consent_confirmed'] = True
                            if action in {'record','resume','reprocess'}:
                                command['cloud_consent'] = True
                            if action == 'summarize':
                                command['text_consent'] = True
                        if action == 'accept_agreement':
                            if set(command) != {'action','version'} or not isinstance(command['version'],str):
                                raise ValueError('Invalid agreement')
                            await publish({'type':'agreement','agreement':store.accept_agreement(command['version'])})
                        elif action == 'revoke_agreement':
                            if set(command) != {'action'}:
                                raise ValueError('Invalid agreement')
                            state = store.revoke_agreement()
                            for pending_ask in list(app.state.ask_tasks):
                                pending_ask.cancel()
                            recorder = app.state.recording
                            if recorder:
                                if recorder.active_id:
                                    recorder.stop(recorder.active_id)
                                cancellation = getattr(recorder,'cancel_processing',None)
                                if cancellation is not None:
                                    cancellation.set()
                            if summary_task:
                                summary_task.cancel()
                            for task in list(auto_tasks):
                                task.cancel()
                            await publish({'type':'agreement','agreement':state})
                        elif command.get('action') == 'start':
                            if not explicit_replay_backend:
                                raise ValueError('Local model replay is disabled in cloud mode')
                            if app.state.recording and (app.state.recording.active_id or
                                    (app.state.recording.task and not app.state.recording.task.done())):
                                raise ValueError('A recording is active')
                            request = StartReplay.model_validate(command)
                            if not request.consent_confirmed:
                                raise ValueError('Confirm authorization before replay')
                            if request.fixture not in fixtures:
                                raise ValueError('Unknown public fixture')
                            session = app.state.runner.start(request.fixture, request.title,
                                                              fixtures[request.fixture])
                            await publish({'type': 'session', 'session': session})
                        elif action == 'set_recording_paused':
                            if (not desktop or set(command) != {'action','session_id','paused'}
                                    or type(command.get('paused')) is not bool):
                                raise ValueError('Invalid recording pause command')
                            recorder = app.state.recording
                            if not recorder or not command.get('session_id'):
                                raise ValueError('No active recording')
                            recorder.set_paused(command['session_id'],command['paused'])
                            await publish(recording_state())
                        elif action == 'stop_recording':
                            if not desktop or set(command) != {'action'}:
                                raise ValueError('Invalid recording Stop command')
                            recorder = app.state.recording
                            if recorder and recorder.active_id:
                                recorder.stop(recorder.active_id)
                            auto_monitor.clear()
                            await queue.put(recording_state())
                        elif command.get('action') == 'stop':
                            if set(command) != {'action','session_id'} or not isinstance(command.get('session_id'),str):
                                raise ValueError('Invalid Stop command')
                            recorder = app.state.recording
                            if recorder and recorder.active_id == command.get('session_id'):
                                recorder.stop(command.get('session_id'))
                                auto_monitor.clear()
                            elif app.state.runner.active_id == command.get('session_id'):
                                app.state.runner.stop(command.get('session_id'))
                            else:
                                saved = store.get(command['session_id'])
                                if not saved or saved['source'] != 'meeting':
                                    raise ValueError('Unknown recording')
                                # A late/repeated Stop is harmless after capture finishes.
                                # Never route a saved meeting to the replay runner.
                            if desktop:
                                await queue.put(recording_state())
                        elif command.get('action') == 'microphone_permission':
                            if set(command) != {'action'} or app.state.recording is None:
                                raise ValueError('Permission request unavailable')
                            from ..capture.devices import request_microphone_access
                            await asyncio.to_thread(permission_request or request_microphone_access)
                            await app.state.discovery.refresh()
                        elif command.get('action') == 'accept_call':
                            request = AcceptCall.model_validate(command)
                            recorder = app.state.recording
                            if not recorder or app.state.runner.active_id or not request.consent_confirmed:
                                raise ValueError('Recording unavailable or permission missing')
                            grant = None
                            if request.automatic_summary and (not desktop or desktop.settings.value.automatic_notes):
                                if not summary_provider or not summary_provider.public()['configured']:
                                    raise ValueError('Summary not configured')
                                grant = {key:summary_provider.public()[key] for key in ('provider','model','endpoint')}
                                if grant != {'provider':request.summary_provider,'model':request.summary_model,
                                             'endpoint':request.summary_endpoint}:
                                    raise ValueError('Summary settings changed; review permission again')
                            source = await app.state.discovery.select(scope='application',app_id=request.app_id,
                                microphone_id=request.microphone_id,candidate_id=request.candidate_id)
                            from ..meetings.contracts import CaptureSelection
                            selection = CaptureSelection(scope='application',bundle_ids=(source['app_id'],),
                                microphone_device_id=request.microphone_id)
                            if hasattr(recorder,'select_model'):
                                recorder.select_model(request.model)
                            title = f"{source['name']} · {datetime.now().astimezone().strftime('%b %d, %H:%M')}"
                            if desktop:
                                source = {**source, 'microphone_processing':{
                                    'gain_db':desktop.settings.value.microphone_gain_db,
                                    'noise_filter':desktop.settings.value.microphone_noise_filter,
                                    'noise_threshold_db':desktop.settings.value.microphone_noise_threshold_db}}
                            session = recorder.start(selection,{**source,'engine':request.model,
                                'language':request.language,'language_hints':request.language_hints,'duration_s':request.duration_s,'cloud_consent':True,'auto_workflow':True,
                                'auto_title':True,'auto_summary':grant},title,consent_confirmed=True)
                            auto_monitor.track(session['id'],request.app_id,
                                stop_on_input_release=desktop.settings.value.stop_on_microphone_release if desktop else False,
                                input_was_active=True)
                            with contextlib.suppress(ValueError):
                                await app.state.discovery.dismiss(request.candidate_id)
                            await publish({'type':'session','session':session})
                        elif command.get('action') == 'keep_recording':
                            if set(command) != {'action','session_id'}:
                                raise ValueError('Invalid command')
                            auto_monitor.keep_recording(command['session_id'])
                        elif command.get('action') in {'summarize','export_markdown'}:
                            request = SummaryRequest.model_validate(command)
                            rows,fingerprint,messages = store.summary_input(request.session_id,request.revision,
                                build_messages=False)
                            if request.action == 'export_markdown':
                                from ..llm.grounded import export_markdown
                                await queue.put({'type':'markdown_export','session_id':request.session_id,
                                    'filename':f'scribeloop-{request.session_id}-r{request.revision}-{uuid4().hex}.md',
                                    'content':export_markdown(store.get(request.session_id),rows,
                                        store.revisions(request.session_id),store.grounded_summary(request.session_id))})
                            else:
                                if (not summary_provider or not summary_provider.public()['configured'] or
                                        summary_task is not None or request.provider != summary_provider.provider or
                                        request.model != summary_provider.model or
                                        (summary_provider.public()['cloud'] and not request.text_consent)):
                                    raise ValueError('Summary provider, consent or active request invalid')
                                with store.conn:
                                    store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) '
                                        'VALUES (?,?,?,?,?)',(request.session_id,store._now(),'info','summary_request',
                                        json.dumps({'revision':request.revision,'input_hash':fingerprint,
                                            'provider':request.provider,'model':request.model,
                                            'text_consent':request.text_consent})))
                                summary_task = asyncio.create_task(generate_summary(request.session_id,
                                    request.revision,fingerprint,messages,retry=True))
                                summary_task.session_id = request.session_id
                                await publish({'type':'summary_started','session_id':request.session_id})
                        elif action in {'create_person','link_person','set_person_learning','forget_person'}:
                            request=PersonRequest.model_validate(command)
                            fields={'create_person':{'action','name'},'link_person':{'action','person_id','session_id','revision','speaker_id'},
                                'set_person_learning':{'action','person_id','enabled'},'forget_person':{'action','person_id','confirmed'}}[action]
                            if set(command)!=fields or recording_busy() or summary_task is not None:
                                raise ValueError('Wait for processing and provide the selected person fields')
                            registry=SpeakerRegistry(store)
                            affected=[]
                            if action=='create_person':
                                registry.create(request.name)
                            elif action=='link_person':
                                registry.link(request.session_id,request.revision,request.speaker_id,request.person_id)
                                affected=[request.session_id]
                            elif action=='set_person_learning':
                                registry.learning(request.person_id,request.enabled)
                            else:
                                if not request.confirmed:
                                    raise ValueError('Confirm permanent person deletion')
                                affected=registry.forget(request.person_id)
                            await publish({'type':'people_changed','people':registry.list(),
                                'sessions':[store.get(sid) for sid in affected],
                                'segments':{sid:store.segments(sid) for sid in affected},
                                'notes':{sid:store.notes(sid) for sid in affected},
                                'grounded_summaries':{sid:store.grounded_summary(sid) for sid in affected}})
                        elif command.get('action') in {'rename_speaker','merge_speaker'}:
                            request = SpeakerCorrection.model_validate(command)
                            recorder = app.state.recording
                            if (app.state.runner.active_id or (recorder and
                                    (recorder.active_id or (recorder.task and not recorder.task.done())))):
                                raise ValueError('Wait for active work to finish')
                            if ((request.action == 'rename_speaker' and
                                    (request.label is None or request.merge_target is not None)) or
                                    (request.action == 'merge_speaker' and
                                    (request.merge_target is None or request.label is not None))):
                                raise ValueError('Invalid correction fields')
                            store.correct_speaker(request.session_id,request.revision,request.speaker_id,
                                label=request.label,merge_target=request.merge_target)
                            await publish({'type':'speakers_corrected','session_id':request.session_id,
                                'segments':store.segments(request.session_id),
                                'notes':store.notes(request.session_id),
                                'summary':store.grounded_summary(request.session_id)})
                        elif command.get('action') == 'reprocess':
                            request = ReprocessRecording.model_validate(command)
                            if not app.state.recording or app.state.runner.active_id or summary_task is not None:
                                raise ValueError('Cloud rerun is unavailable or notes are still processing')
                            # Re-sync the cleanup engine with the latest saved settings so a
                            # provider captured earlier cannot re-validate against old rules.
                            resync_cleanup(app.state.recording)
                            if hasattr(app.state.recording,'select_model'):
                                app.state.recording.select_model(request.model)
                            previous_revision = store.get(request.session_id)['transcript_revision']
                            app.state.recording.reprocess(request.session_id, language=request.language,
                                **({'language_hints':request.language_hints} if request.language_hints is not None else {}),
                                **({'cloud_consent':request.cloud_consent} if hasattr(app.state.recording,'cloud') else {}))
                            automatic_scheduled.add(request.session_id)
                            if summary_provider and summary_provider.public()['configured'] and (not desktop or desktop.settings.value.automatic_notes):
                                grant = {key:summary_provider.public()[key] for key in ('provider','model','endpoint')}
                                task = asyncio.create_task(automatic_notes(request.session_id,grant_override=grant,
                                    expected_task=app.state.recording.task,previous_revision=previous_revision))
                                auto_tasks.add(task)
                                task.add_done_callback(auto_tasks.discard)
                        elif command.get('action') in {'recover','resume','delete'}:
                            request = SavedMeeting.model_validate(command)
                            recorder = app.state.recording
                            if not recorder or app.state.runner.active_id:
                                raise ValueError('Saved meeting operations unavailable')
                            if request.action == 'recover':
                                recorder.recover_saved(request.session_id)
                            elif request.action == 'resume':
                                if hasattr(recorder,'select_model'):
                                    recorder.select_model(request.model)
                                resync_cleanup(recorder)
                                recorder.resume(request.session_id, language=request.language,
                                    **({'language_hints':request.language_hints} if request.language_hints is not None else {}),
                                    **({'cloud_consent':request.cloud_consent} if hasattr(recorder,'cloud') else {}))
                            else:
                                if not request.confirmed:
                                    raise ValueError('Confirm permanent deletion')
                                recorder.delete_saved(request.session_id)
                        elif action in {'archive_audio','play_audio'}:
                            allowed = {'action','session_id','archived' if action == 'archive_audio' else 'stream'}
                            if action=='play_audio' and 'start_ms' in command:
                                allowed.add('start_ms')
                            if set(command) != allowed or not retained or recording_busy():
                                raise ValueError('Saved audio operation unavailable')
                            sid = SavedMeeting.model_validate({'action':'recover','session_id':command['session_id']}).session_id
                            retained.require(sid)
                            if store.get(sid)['status'] not in {'completed','failed','interrupted'}:
                                raise ValueError('Select a saved meeting')
                            if action == 'archive_audio':
                                store.archive_audio(sid,command['archived'])
                                await publish({'type':'audio_state','session_id':sid,'audio':retained.status(sid)})
                            else:
                                if command['stream'] not in {'mic','loopback'}:
                                    raise ValueError('Invalid audio track')
                                if playback_pending:
                                    raise ValueError('Wait for previous playback delivery')
                                playback_pending = owns_playback = True
                                data = await retained.playback(sid,command['stream'],start_ms=command.get('start_ms',0))
                                await queue.put({'type':'audio_playback','session_id':sid,'stream':command['stream'],
                                    'wav_base64':base64.b64encode(data).decode('ascii')})
                        elif command.get('action') == 'retention':
                            request = RetentionRequest.model_validate(command)
                            recorder = app.state.recording
                            if not request.confirmed or not recorder or app.state.runner.active_id:
                                raise ValueError('Confirm retention cleanup')
                            candidates = store.retention_candidates(request.days)
                            # Serialize retirement; no recording or reader may own keys.
                            for session_id in candidates:
                                recorder.delete_saved(session_id)
                                await recorder.task
                                if store.get(session_id):
                                    raise ValueError('Retry pending deletion before continuing')
                            await publish({'type':'retention_complete','deleted':len(candidates)})
                        elif command.get('action') == 'record':
                            request = StartRecording.model_validate(command)
                            recorder = app.state.recording
                            if not recorder or app.state.runner.active_id or not request.consent_confirmed:
                                raise ValueError('Recording is not available or consent missing')
                            source = await app.state.discovery.select(scope=request.scope,
                                app_id=request.app_id, microphone_id=request.microphone_id,
                                candidate_id=request.candidate_id)
                            from ..meetings.contracts import CaptureSelection
                            selection = CaptureSelection(scope=request.scope,
                                bundle_ids=(source['app_id'],) if request.scope == 'application' else (),
                                microphone_device_id=request.microphone_id)
                            if app.state.runner.active_id:
                                raise ValueError('A replay is active')
                            if hasattr(recorder,'select_model'):
                                recorder.select_model(request.model)
                            grant=({key:summary_provider.public()[key] for key in ('provider','model','endpoint')}
                                   if summary_provider and summary_provider.public()['configured'] and
                                   (not desktop or desktop.settings.value.automatic_notes) else None)
                            source = {**source,'auto_workflow':True,'auto_title':request.title.strip() in {'Meeting conversation','Meeting test'},'auto_summary':grant,'language':request.language,'language_hints':request.language_hints,'duration_s':request.duration_s,'cloud_consent':request.cloud_consent,
                                      'asr_engine':request.model if hasattr(recorder,'select_model') else recorder.cloud.public()['model'] if hasattr(recorder,'cloud') else 'test'}
                            if desktop:
                                source['microphone_processing'] = {
                                    'gain_db':desktop.settings.value.microphone_gain_db,
                                    'noise_filter':desktop.settings.value.microphone_noise_filter,
                                    'noise_threshold_db':desktop.settings.value.microphone_noise_threshold_db}
                            session = recorder.start(selection, source, request.title,
                                consent_confirmed=request.consent_confirmed)
                            if request.scope == 'application':
                                state = app.state.discovery.snapshot()
                                auto_monitor.track(session['id'],request.app_id,
                                    stop_on_input_release=desktop.settings.value.stop_on_microphone_release if desktop else False,
                                    input_was_active=state.get('input_activity',{}).get(request.app_id)=='active')
                            await publish({'type': 'session', 'session': session})
                        elif command.get('action') == 'select_source':
                            request = SelectSource.model_validate(command)
                            source = await app.state.discovery.select(
                                scope=request.scope, app_id=request.app_id,
                                microphone_id=request.microphone_id,
                                candidate_id=request.candidate_id)
                            # Preferences are ephemeral and scoped to this connection.
                            if queue.full():
                                await ws.close(code=1013)
                                return
                            queue.put_nowait({'type': 'source_selected',
                                              'request_id': request.request_id, 'source': source})
                        elif command.get('action') == 'dismiss_candidate':
                            request = DismissCandidate.model_validate(command)
                            await app.state.discovery.dismiss(request.candidate_id)
                        else:
                            raise ValueError('Unknown command')
                        if desktop:
                            await queue.put(desktop.receipt(dispatch_id,'accepted'))
                    except (ValueError, ValidationError, TypeError, OSError, KeyUnavailable, *DATABASE_ERRORS):
                        if desktop and dispatch_id:
                            await queue.put(desktop.receipt(dispatch_id,'rejected'))
                        if owns_playback:
                            playback_pending = False
                        if queue.full():
                            await ws.close(code=1013)
                            return
                        action = command.get('action') if isinstance(command, dict) else None
                        if desktop and action in {'stop','stop_recording','set_recording_paused','save_settings','set_detection','meeting_page','history_page','search_meetings','ask_evidence','ask_history','delete_ask_history','ask_question','cancel_ask','people_page','request_status','name_speaker','audio_info','audio_window','rename_conversation','link_calendar_event','prepare_email_export','discard_email_exports'}:
                            queue.put_nowait({'type':'error','context':'desktop','request_id':dispatch_id,
                                'message':('Stop could not be confirmed. Reconnect to check the current recording, or quit ScribeLoop to stop capture.'
                                    if action in {'stop','stop_recording'} else
                                    'Pause or Continue could not be confirmed. Reconnect to check the active recording; Stop remains available.'
                                    if action=='set_recording_paused' else
                                    'Could not rename this conversation. Choose a saved record, use a single-line name of 1–160 characters, and wait for processing to finish.'
                                    if action=='rename_conversation' else
                                    'Calendar link was not saved. Choose an available conversation and try again.'
                                    if action=='link_calendar_event' else
                                    'Could not prepare this attachment. Use a saved conversation with an up-to-date summary or retained audio, and finish any active processing.'
                                    if action in {'prepare_email_export','discard_email_exports'} else
                                    'Could not play this audio. Check its availability or recover interrupted audio before trying again.'
                                    if action in {'audio_info','audio_window'} else
                                    'Name was not saved. Reopen the current transcript and choose a speaker; no partial person was created.'
                                    if action=='name_speaker' else
                                    'Settings were not saved. Wait for processing to finish, check the URL/model/key fields and Keychain access.'
                                    if action=='save_settings' else
                                    'Stop recording before changing detection. The previous detection state remains available in Preferences.'
                                    if action=='set_detection' else
                                    'Search could not finish. The local index may be unavailable; your saved conversations are unchanged.'
                                    if action=='search_meetings' else
                                    'Chat deletion could not be confirmed. Reopen History before retrying; meeting data is unchanged.'
                                    if action=='delete_ask_history' else
                                    'Ask could not prepare evidence. Check the question and meeting scope; your saved conversations are unchanged.'
                                    if action in {'ask_evidence','ask_history','ask_question','cancel_ask'} else
                                    'This page or citation is unavailable. Reopen the meeting to load its current transcript revision.')})
                            continue
                        meeting = (isinstance(action, str) and action
                                   in {'select_source', 'dismiss_candidate', 'record', 'microphone_permission', 'reprocess', 'recover', 'resume', 'delete', 'retention','rename_speaker','merge_speaker','summarize','export_markdown','accept_call','keep_recording','accept_agreement','revoke_agreement','archive_audio','play_audio','create_person','link_person','set_person_learning','forget_person','check_ollama'})
                        queue.put_nowait({'type': 'error', 'context': 'meeting' if meeting else 'replay',
                            'request_id': command.get('request_id') if meeting else None,
                            'message': ('Summary/export rejected. Select the current saved transcript, check configuration and the first-use agreement, and wait for any summary request to finish.'
                            if action in {'summarize','export_markdown'} else
                            'Person change rejected. Select a saved speaker/person and wait for processing; Forget requires confirmation.'
                            if action in {'create_person','link_person','set_person_learning','forget_person'} else
                            'Speaker correction rejected. Reopen the saved transcript; choose a valid name or a speaker from the same track and generation.'
                            if action in {'rename_speaker','merge_speaker'} else
                            'Request rejected. Wait for active work to finish; check consent, saved session, or source selection.'
                            if action in {'recover','resume','delete','retention'} else
                            'Saved audio is unavailable or busy. Check its expiry and try again.'
                            if action in {'archive_audio','play_audio','reprocess'} else
                            'Local model check could not run. Set the Ollama endpoint and model in Preferences and wait for active work to finish.'
                            if action == 'check_ollama' else
                            'Source unavailable or suggestion expired. Choose again manually.')
                            if meeting else
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
            if ask_task and not ask_task.done():
                ask_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ask_task
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
    parser.add_argument('--public-fixture', type=Path,
                        help='optional public replay WAV; required only with --storage public-demo')
    parser.add_argument('--storage', choices=['encrypted', 'public-demo'], default='encrypted')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--port', type=int, default=8757)
    parser.add_argument('--require-launch-key',action='store_true',
                        help='require a per-launch key instead of keyless local demo access')
    parser.add_argument('--no-discovery', action='store_true',
                        help='disable metadata-only automatic meeting discovery')
    args = parser.parse_args()
    if args.storage == 'public-demo' and args.public_fixture is None:
        parser.error('--storage public-demo requires --public-fixture')
    if args.database is None:
        if args.storage == 'encrypted':
            from platformdirs import user_data_path
            args.database = user_data_path('ScribeLoop', appauthor=False) / 'sessions.sqlcipher'
        else:
            args.database = Path('data/phase1/public-replays.sqlite3')
    args.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    from ..llm.summary_provider import SummaryProvider
    from ..meetings.service import manual_discovery, native_discovery
    app, token = create_app(database=args.database, storage=args.storage,
                            summary_provider=SummaryProvider(),
                            require_launch_key=args.require_launch_key,
                            discovery_factory=manual_discovery if args.no_discovery else native_discovery,
                            fixtures={'development-sample': args.public_fixture} if args.public_fixture else {})
    # Fragment is not sent in HTTP requests; client removes it after reading.
    suffix = '#token='+token if args.require_launch_key else ''
    print(f'Open locally: http://127.0.0.1:{args.port}/'+suffix, file=sys.stderr)
    uvicorn.run(app, host='127.0.0.1', port=args.port, ws_max_size=8192, access_log=False)


if __name__ == '__main__':
    main()
