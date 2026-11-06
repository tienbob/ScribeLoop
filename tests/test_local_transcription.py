"""Local Ollama transcription: config, lifecycle, adapter and runner; all mocked.

No real Ollama server, download or audio device is used. The local HTTP server
is a MockTransport; server launches are stubbed. Recordings stay encrypted and
no file outside tmp_path is written.
"""
from __future__ import annotations

import asyncio
import json
import threading

import httpx
import numpy as np
import pytest
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline import local_transcription as lt
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
from scribeloop_core.pipeline.local_transcription import (
    LocalRecordingRunner,
    LocalTranscriptionConfig,
    LocalTranscriptionError,
    ensure_ollama,
    transcribe_local,
)
from scribeloop_core.pipeline.model_selection import TranscriptionRouter
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_recording import Keys


class Vault:
    def __init__(self):
        self.value = None
    def get_password(self, service, account):
        return self.value
    def set_password(self, service, account, value):
        self.value = value


class OllamaServer:
    """Stand-in for the Ollama REST surface with controllable state."""
    def __init__(self, *, running=True, installed=True, pull_error=None):
        self.running = running
        self.installed = installed
        self.pull_error = pull_error
        self.transcribe = lambda request: httpx.Response(200, json={
            'text':'Hello there','segments':[{'start':.1,'end':1.,'text':' Hello there '}]})
        self.requests = []
    def handler(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == '/api/version':
            if not self.running:
                raise httpx.ConnectError('refused')
            return httpx.Response(200, json={'version':'0.40.12'})
        if path == '/api/tags':
            if not self.running:
                raise httpx.ConnectError('refused')
            names = [{'name':'whisper-cpp-large-turbo-q8:latest'}] if self.installed else []
            return httpx.Response(200, json={'models':names})
        if path == '/api/show':
            return httpx.Response(200,json={'capabilities':['audio','completion']})
        if path == '/api/pull':
            if self.pull_error:
                return httpx.Response(200, content=json.dumps({'error':self.pull_error}).encode())
            self.installed = True  # A completed pull makes the model available.
            def lines():
                yield json.dumps({'status':'pulling manifest'})
                yield json.dumps({'status':'downloading sha256','total':1000,'completed':750})
            return httpx.Response(200, content=('\n'.join(lines())+'\n').encode())
        if path.endswith('/audio/transcriptions'):
            return self.transcribe(request)
        if path == '/api/generate':
            return httpx.Response(200, json={'done':True})
        raise AssertionError(path)
    def client_factory(self, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(self.handler), **kwargs)


def config(server=None, *, model='whisper-cpp-large-turbo-q8', speaker_labels=False):
    if server is None:
        return LocalTranscriptionConfig(model=model, speaker_labels=speaker_labels)
    return LocalTranscriptionConfig(model=model, speaker_labels=speaker_labels,
                                    client_factory=server.client_factory)


def test_config_allows_loopback_http_and_rejects_other_hosts_names_and_ports():
    assert config().configured
    assert LocalTranscriptionConfig(model='hf.co/publisher/model:Q8_0').configured
    assert not LocalTranscriptionConfig(model='ok-model', endpoint='https://example.com/v1').configured
    assert not LocalTranscriptionConfig(model='gemma4:cloud').configured
    for invalid in (LocalTranscriptionConfig(model='bad name'),
                    LocalTranscriptionConfig(model='ok-model', endpoint='http://example.com/v1'),
                    LocalTranscriptionConfig(model='ok-model', endpoint='http://localhost/v1#frag'),
                    LocalTranscriptionConfig(model='ok-model', endpoint='not a url'),
                    LocalTranscriptionConfig(model='')):
        assert not invalid.configured
    public = config().public()
    assert public['cloud'] is False and public['live'] is False
    assert public['runs_after_recording'] is True and public['provider'] == 'ollama'


def test_transcribe_local_posts_multipart_verbose_json_and_parses_segments():
    server = OllamaServer()
    seen = []
    def transcribe(request):
        seen.append(request)
        body = request.read()
        assert str(request.url) == 'http://localhost:11434/v1/audio/transcriptions'
        assert b'name="model"\r\n\r\nwhisper-cpp-large-turbo-q8' in body
        assert b'name="language"\r\n\r\nen' in body
        assert b'name="response_format"\r\n\r\nverbose_json' in body
        return httpx.Response(200, json={'text':'Xin chào','language':'vietnamese',
            'segments':[{'start':0.,'end':2.5,'text':' Xin chào '},
                        {'start':4.,'end':5.,'text':'   '}]})
    server.transcribe = transcribe
    pcm = np.zeros(16000,dtype=np.float32); pcm[100:2000] = .05
    result = transcribe_local(pcm, config=config(server), language='en')
    assert len(seen) == 1
    assert result['segments'] == [{'start_ms':0,'end_ms':1000,'text':'Xin chào'}]
    assert result['upload_bytes'] > 0 and result['model'] == 'whisper-cpp-large-turbo-q8'


def test_transcribe_local_rejects_inverted_segment_metadata():
    server = OllamaServer()
    server.transcribe = lambda request: httpx.Response(200, json={
        'text':'ok','segments':[{'start':3.,'end':1.,'text':'backwards'}]})
    with pytest.raises(LocalTranscriptionError) as exc:
        transcribe_local(np.zeros(1600,dtype=np.float32), config=config(server), language='en')
    assert 'invalid segment metadata' in str(exc.value)


@pytest.mark.parametrize('segments', [None, [{'start':0,'end':.1,
    'text':'language Vietnamese<asr_text>Xin chào.'}]])
def test_qwen_asr_protocol_header_is_removed_from_text_and_segments(segments):
    server = OllamaServer()
    server.transcribe = lambda request: httpx.Response(200,json={
        'text':'language Vietnamese<asr_text>Xin chào.','segments':segments})
    result = transcribe_local(np.zeros(1600,dtype=np.float32),config=config(server))
    assert result['text'] == 'Xin chào.'
    assert result['segments'][0]['text'] == 'Xin chào.'
    assert result['language'] == 'vi'


def test_qwen_header_cleanup_preserves_normal_speech():
    server = OllamaServer()
    server.transcribe = lambda request: httpx.Response(200,json={
        'text':'The language English is used here. Say <asr_text> literally.'})
    result = transcribe_local(np.zeros(1600,dtype=np.float32),config=config(server))
    assert result['text'] == 'The language English is used here. Say <asr_text> literally.'


def test_real_ollama_text_only_response_is_not_dropped():
    server = OllamaServer()
    server.transcribe = lambda request: httpx.Response(200,json={'text':'We agreed to test offline.'})
    result = transcribe_local(np.ones(16000,dtype=np.float32)*.01,config=config(server))
    assert result['segments'] == [{'start_ms':0,'end_ms':1000,'text':'We agreed to test offline.'}]


@pytest.mark.parametrize('metadata',[
    {'capabilities':['completion']},
    {'capabilities':['audio'],'remote_host':'https://ollama.com'},
    {'capabilities':['audio'],'remote_model':'a-cloud-model'},
])
def test_preparation_rejects_non_audio_and_cloud_models_before_audio(metadata):
    server = OllamaServer()
    original = server.handler
    def handler(request):
        if request.url.path == '/api/show':
            return httpx.Response(200,json=metadata)
        return original(request)
    server.handler = handler
    with pytest.raises(LocalTranscriptionError):
        ensure_ollama(config(server),lambda _:None,threading.Event())
    assert not any(r.url.path.endswith('/audio/transcriptions') for r in server.requests)


@pytest.mark.parametrize('status,expected',[
    (400,'transcription model'),(404,'was not found'),(429,'no retry'),
    (500,'retained'),(302,'HTTP 302')])
def test_transcribe_local_http_errors_are_sanitized_and_never_retried(status,expected):
    server = OllamaServer()
    server.transcribe = lambda request: httpx.Response(status)
    with pytest.raises(LocalTranscriptionError) as exc:
        transcribe_local(np.zeros(1600,dtype=np.float32), config=config(server), language='en')
    assert 'server body' not in str(exc.value)
    assert expected in str(exc.value)
    assert len(server.requests) == 1  # No retry was made.


def test_transcribe_local_rejects_malformed_and_oversized_and_unconfigured_input():
    pcm = np.zeros(1600,dtype=np.float32)
    server = OllamaServer()
    server.transcribe = lambda request: httpx.Response(200, json={'text':None,'segments':[]})
    with pytest.raises(LocalTranscriptionError):
        transcribe_local(pcm, config=config(server), language='en')
    server.transcribe = lambda request: httpx.Response(200, json={
        'text':'ok','segments':[{'start':'zero','end':1,'text':'x'}]})
    with pytest.raises(LocalTranscriptionError):
        transcribe_local(pcm, config=config(server), language='en')
    with pytest.raises(ValueError):
        transcribe_local(np.zeros(lt.WINDOW_SAMPLES+1,dtype=np.float32), config=config(server))
    with pytest.raises(LocalTranscriptionError):
        transcribe_local(pcm, config=LocalTranscriptionConfig(model=''))
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        transcribe_local(pcm, config=config(server), language='en', cancelled=cancelled)


def test_ensure_ollama_pulls_a_missing_model_and_fails_closed_on_library_miss():
    server = OllamaServer(installed=False)
    events = []
    ensure_ollama(config(server), events.append, threading.Event())
    states = [event['state'] for event in events]
    assert states[:2] == ['checking','checking'] and states[-2:] == ['loading','ready']
    assert any(event['percent'] == 75.0 for event in events)
    assert any(request.url.path == '/api/pull' for request in server.requests)
    server.pull_error = 'pull model manifest: file does not exist'
    server.installed = False
    with pytest.raises(LocalTranscriptionError) as exc:
        ensure_ollama(config(server), events.append, threading.Event())
    assert "not available in the Ollama library" in str(exc.value)


def test_ensure_ollama_starts_the_server_when_down(monkeypatch):
    server = OllamaServer(running=False)
    def wake(cancelled):
        server.running = True
    monkeypatch.setattr(lt,'_launch_server',wake)
    events = []
    ensure_ollama(config(server), events.append, threading.Event())
    assert events[0]['state'] == 'checking'
    assert len([event for event in events if event['state'] == 'starting']) == 1
    assert events[-1]['state'] == 'ready'


def test_ensure_ollama_fails_closed_without_an_installation(monkeypatch):
    server = OllamaServer(running=False)
    def absent(cancelled):
        raise LocalTranscriptionError('Ollama is not installed. Install it from ollama.com and try again.')
    monkeypatch.setattr(lt,'_launch_server',absent)
    with pytest.raises(LocalTranscriptionError) as exc:
        ensure_ollama(config(server), lambda event: None, threading.Event())
    assert 'ollama.com' in str(exc.value)


def test_ensure_ollama_requires_a_configured_model_and_cancellable_pull():
    events = []
    with pytest.raises(LocalTranscriptionError):
        ensure_ollama(LocalTranscriptionConfig(model=''), events.append, threading.Event())
    server = OllamaServer(installed=False)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        ensure_ollama(config(server), events.append, cancelled)



class ControlledSource:
    def __init__(self, seconds=12):
        self.stopped = threading.Event()
        self.closed = False
        self.count = seconds*10
    def frames(self):
        for i in range(self.count):
            for stream in ('loopback','mic'):
                yield AudioFrame(np.full(1600,.02,dtype=np.float32),stream,i*100+150,0)
        self.stopped.wait(4)
    def request_stop(self):
        self.stopped.set()
    def close(self):
        self.closed = True
        self.stopped.set()


class FakeTurns:
    """Stands in for the pyannote worker; one stable voice for the whole track."""
    def __init__(self, *, next_label=1):
        self.label = f'Speaker {next_label}'
    def window(self, pcm, start_ms, commit_start, commit_end, cancelled):
        return [{'start_ms':commit_start,'end_ms':commit_end,'label':self.label,'confidence':None}]
    def close(self):
        pass


def test_replacement_does_not_hide_a_capture_failure(tmp_path):
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    sid = store.create_recording('Capture issue',{'scope':'microphone'})['id']
    store.record_failure(sid,{'stage':'capture','code':'capture_failed','message':'Capture stopped'})
    store.revise_transcript(sid,[{'stream':'mic','start_ms':0,'end_ms':1000,
        'text':'Recovered speech','is_final':True}],provider='local-ollama',model='local')
    assert store.get(sid)['failure']['code']=='capture_failed'
    conn.close()


@pytest.fixture()
def state(tmp_path):
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    audio = SessionAudioStore(tmp_path/'audio',Keys())
    events = []
    async def publish(event):
        events.append(event)
    yield store,audio,events,publish
    conn.close()


@pytest.mark.asyncio
async def test_local_engine_transcribes_only_after_capture_and_never_cloud(state):
    store,audio,events,publish = state
    server = OllamaServer()
    source = ControlledSource()
    runner = LocalRecordingRunner(store,audio,lambda *a,**kw:source,publish,config=config(server))
    session = runner.start(None,{'language':'en','scope':'microphone'},'Local test',
                           consent_confirmed=True)
    await asyncio.sleep(.3)  # Capture is running; nothing may be transcribed yet.
    during = [event['type'] for event in events]
    assert 'segment' not in during and 'partial' not in during
    assert 'cloud_request' not in during
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task,4)
    assert store.get(session['id'])['status'] == 'completed'
    assert store.get(session['id'])['capture']['provider'] == 'local-ollama'
    rows = store.segments(session['id'])
    assert [row['text'] for row in rows] == ['Hello there','Hello there']
    assert all(row['speaker_label'] is None for row in rows)
    assert not any(event['type'] == 'cloud_request' for event in events)
    assert not any(event['type'] == 'partial' for event in events)
    assert len([event for event in events if event['type'] == 'segment']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('gap_ms', [0, 7000, 60000])
async def test_reprocess_long_saved_audio_bounds_windows_and_preserves_frames(state, monkeypatch, gap_ms):
    store,audio,events,publish = state
    server = OllamaServer()
    sid = store.create_recording('Saved long call',{'provider':'deepgram','scope':'microphone'})['id']
    audio.create_session(sid)
    writer = audio.writer(sid,'mic')
    # Native packets need not divide the 30-second ASR window. Gaps also count
    # toward request duration even though they carry no stored PCM samples.
    for i in range(64):
        writer.write(AudioFrame(np.full(8192,.02,dtype=np.float32),'mic',
            i*512+(gap_ms if i>=32 else 0),0))
    writer.close()
    store.append(sid,{'text':'Previous cloud transcript','start_ms':0,'end_ms':1000,'is_final':True})
    store.record_failure(sid,{'stage':'transcription','code':'transcription_failed','message':'Prior failure'})
    store.transition(sid,'failed')
    requests = []
    original = lt.transcribe_local
    def observe(pcm, **kwargs):
        requests.append(pcm.copy())
        return original(pcm,**kwargs)
    monkeypatch.setattr(lt,'transcribe_local',observe)
    runner = LocalRecordingRunner(store,audio,None,publish,config=config(server))
    runner.reprocess(sid,language='en')
    await runner.task
    assert store.get(sid)['status'] == 'completed'
    assert all(len(pcm)<=lt.WINDOW_SAMPLES for pcm in requests)
    assert sum(np.count_nonzero(pcm) for pcm in requests) == 64*8192
    assert all(row['text']=='Hello there' for row in store.segments(sid))
    assert len(store.revisions(sid)) == 2
    assert not any(event['type']=='cloud_request' for event in events)
    assert store.get(sid)['failure'] is None
    assert store.conn.execute("SELECT count(*) FROM events WHERE session_id=? AND type='recording_failure'",(sid,)).fetchone()[0]==1
    store.record_failure(sid,{'stage':'transcription','code':'new_failure','message':'New failure'})
    assert store.get(sid)['failure']['code']=='new_failure'


@pytest.mark.asyncio
async def test_local_speaker_labels_are_opt_in_and_turn_scoped(state):
    store,audio,events,publish = state
    server = OllamaServer()
    source = ControlledSource()
    runner = LocalRecordingRunner(store,audio,lambda *a,**kw:source,publish,
        config=config(server,speaker_labels=True),diarizer_factory=FakeTurns)
    session = runner.start(None,{'language':'auto','scope':'microphone'},'Labels test',
                           consent_confirmed=True)
    await asyncio.sleep(.3)  # Let capture commit audio before Stop.
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task,4)
    labels = {row['stream']:row['speaker_label'] for row in store.segments(session['id'])}
    assert labels == {'loopback':'Speaker 1','mic':'Speaker 2'}
    assert len(store.turns(session['id'])) >= 1
    assert any(event['type'] == 'speaker_turns' for event in events)


@pytest.mark.asyncio
async def test_local_labels_fall_back_to_unlabeled_transcript_when_unavailable(state):
    store,audio,events,publish = state
    server = OllamaServer()
    source = ControlledSource()
    def broken_factory(**kwargs):
        raise LocalTranscriptionError('diarization model missing')
    runner = LocalRecordingRunner(store,audio,lambda *a,**kw:source,publish,
        config=config(server,speaker_labels=True),diarizer_factory=broken_factory)
    session = runner.start(None,{'language':'auto','scope':'microphone'},'Fallback test',
                           consent_confirmed=True)
    await asyncio.sleep(.3)  # Let capture commit audio before Stop.
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task,4)
    assert store.get(session['id'])['status'] == 'completed'
    rows = store.segments(session['id'])
    assert len(rows) == 2 and all(row['speaker_label'] is None for row in rows)
    assert any(event['type'] == 'speaker_warning' for event in events)



@pytest.mark.asyncio
async def test_local_preparation_failure_fails_transcription_closed(state, monkeypatch):
    store,audio,events,publish = state
    server = OllamaServer(running=False)  # Down and stay down: launches fail closed.
    source = ControlledSource()
    def broken_launch(cancelled):
        raise LocalTranscriptionError('Ollama is not installed. Install it from ollama.com and try again.')
    monkeypatch.setattr(lt,'_launch_server',broken_launch)
    runner = LocalRecordingRunner(store,audio,lambda *a,**kw:source,publish,config=config(server))
    session = runner.start(None,{'language':'en','scope':'microphone'},'Down test',
                           consent_confirmed=True)
    await asyncio.sleep(.3)
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task,4)  # The failure is recorded, not raised to the caller.
    assert store.get(session['id'])['status'] == 'failed'
    assert store.segments(session['id']) == []
    assert any(event['type'] == 'local_model_status' and event.get('state') == 'unavailable'
               for event in events)


def test_router_routes_engines_refuses_mixing_and_reports_unconfigured(state):
    store,audio,_,publish = state
    server = OllamaServer()
    source = ControlledSource()
    router = TranscriptionRouter(store,audio,lambda *a,**kw:source,publish,
        cloud_config=DeepgramConfig(api_key='test-key-never-log-this'),local_config=config(server))
    assert router.profiles()['deepgram']['cloud'] is True
    assert router.profiles()['ollama']['cloud'] is False
    router.select_model('ollama')
    assert router.cloud.public()['provider'] == 'ollama'
    router.select_model('deepgram')
    assert router.cloud.public()['cloud'] is True
    sid = store.create_recording('Cloud talk',{'provider':'deepgram','engine':'deepgram'})['id']
    store.append(sid,{'text':'cloud transcript','start_ms':0,'end_ms':100,'is_final':True,
                      'language':'en'},stream='mic')
    store.recover()
    router.select_model('ollama')
    with pytest.raises(ValueError) as exc:
        router.resume(sid,language='en')
    assert 'another provider' in str(exc.value)
    bare = TranscriptionRouter(store,audio,lambda *a,**kw:source,publish,
        cloud_config=DeepgramConfig(api_key='test-key-never-log-this'))
    with pytest.raises(ValueError) as exc:
        bare.select_model('ollama')
    assert 'not configured' in str(exc.value)


def test_engine_settings_validate_and_publicize():
    settings = DesktopSettings(Vault())
    public = settings.value.public()
    assert public['transcription_engine'] == 'deepgram'
    assert public['local_transcription_configured'] is False
    settings.save({'transcription_engine':'ollama',
                   'local_transcription_model':'whisper-cpp-large-turbo-q8'})
    assert settings.value.public()['local_transcription_configured'] is True
    assert DesktopSettings(settings.backend).value.transcription_engine == 'ollama'
    for bad in ('','bad name'):
        with pytest.raises(ValueError):
            settings.save({'transcription_engine':'ollama','local_transcription_model':bad})

    # Selecting the local engine without any saved model is rejected on a fresh install.
    with pytest.raises(ValueError):
        DesktopSettings(Vault()).save({'transcription_engine':'ollama'})
    with pytest.raises(ValueError):
        settings.save({'transcription_engine':'lmstudio'})
    with pytest.raises(ValueError):
        settings.save({'transcription_engine':'ollama','local_transcription_model':'ok-model',
                       'local_transcription_endpoint':'http://example.com/v1'})


def test_experience_validation_toggle_reaches_the_live_recorder(state):
    """The Experience skip-validation toggle must reach the running engines.

    A Preferences save reconfigures the router; a stale engine captured before
    that save is re-synced before Transcribe again dispatches.
    """
    from scribeloop_core.llm.summary_provider import SummaryProvider
    from scribeloop_core.pipeline.model_selection import TranscriptionRouter
    store,audio,_,publish = state
    server = OllamaServer()
    source = ControlledSource()
    settings = DesktopSettings(Vault())
    settings.save({'transcription_engine':'deepgram','deepgram_key':'test-key-never-log-this',
                   'local_transcript_cleanup':True,'local_cleanup_provider':'Ollama',
                   'local_cleanup_model':'text-model','local_cleanup_validate':True})
    router = TranscriptionRouter(store,audio,lambda *a,**kw:source,publish,
        cloud_config=DeepgramConfig(api_key='test-key-never-log-this'),
        local_config=config(server),cleanup_provider=settings.value.cleanup())
    assert router.runners['deepgram'].cleanup_provider.cleanup_validate is True
    # The Experience toggle skips validation; the save reconfigures the recorder.
    candidate = settings.prepare({'local_cleanup_validate':False})
    settings.value = candidate
    router.configure(cloud_config=settings.cloud(),
        local_config=candidate.local_transcription(),cleanup_provider=candidate.cleanup())
    assert router.runners['deepgram'].cleanup_provider.cleanup_validate is False
    # An engine built before the save keeps validating until re-synced.
    stale = SummaryProvider(settings={'SUMMARY_BASE_URL':'http://127.0.0.1:11434/v1',
        'SUMMARY_MODEL':'text-model','SUMMARY_PROVIDER':'Ollama','SUMMARY_INSTRUCTIONS':''})
    stale.cleanup_validate = True
    router.runners['deepgram'].cleanup_provider = stale
    router.runners['ollama'].config.cleanup_provider = stale
    router.refresh_cleanup(settings.value.cleanup())
    assert router.runners['deepgram'].cleanup_provider.cleanup_validate is False
    assert router.runners['ollama'].config.cleanup_provider.cleanup_validate is False
    # Disabling cleanup entirely removes the engine from both runners.
    settings.value = settings.prepare({'local_transcript_cleanup':False})
    router.refresh_cleanup(settings.value.cleanup())
    assert router.runners['deepgram'].cleanup_provider is None
    assert router.runners['ollama'].config.cleanup_provider is None



@pytest.mark.asyncio
async def test_record_command_routes_ollama_engine_through_the_server(tmp_path):
    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.service import DiscoveryService

    server = OllamaServer()
    class MicrophoneSource(ControlledSource):
        def frames(self):
            yield from (frame for frame in super().frames() if frame.stream == 'mic')
    source_holder = []
    def source(*_args,**_kwargs):
        item = MicrophoneSource()
        source_holder.append(item)
        return item
    holder = {}
    def recording(store,audio,_factory,publish):
        holder['store'] = store
        return TranscriptionRouter(store,audio,source,publish,
            cloud_config=DeepgramConfig(api_key='test-key-never-log-this'),
            local_config=config(server))
    app,token = create_app(storage='encrypted',database=tmp_path/'app.sqlcipher',
        key_store=Keys(),recording_factory=recording,
        discovery_factory=lambda publish:DiscoveryService(publish))
    with TestClient(app) as client, client.websocket_connect(
            'ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
        ws.send_text(token)
        while (event := ws.receive_json()).get('type') != 'snapshot':
            pass
        store = holder['store']
        ws.send_json({'action':'accept_agreement','version':store.AGREEMENT_VERSION})
        while (event := ws.receive_json()).get('type') != 'agreement':
            pass
        ws.send_json({'action':'record','request_id':1,'scope':'microphone',
            'microphone_id':'default','title':'Routed','model':'ollama',
            'consent_confirmed':True})
        session = None
        while session is None:
            event = ws.receive_json()
            if event.get('type') == 'session':
                session = event['session']
        assert session['capture']['engine'] == 'ollama'
        assert session['capture']['provider'] == 'local-ollama'
        assert session['capture']['model'] == 'whisper-cpp-large-turbo-q8'
        sid = session['id']
        assert client.portal.call(getattr,app.state.recording,'selected') == 'ollama'
        await asyncio.sleep(.3)
        ws.send_json({'action':'stop','session_id':sid})
        diagnostic,terminal,seen = None,False,[]
        for _ in range(140):
            event = ws.receive_json()
            seen.append(event.get('type'))
            if event.get('type') == 'recording_error':
                diagnostic = (event.get('diagnostic') or event.get('message'))
            if event.get('type') == 'session' and event['session']['id'] == sid:
                session = event['session']
                if session['status'] in ('completed','failed','interrupted'):
                    terminal = True
                    if session['status'] == 'completed' or diagnostic is not None:
                        break
        assert terminal, 'recording never reached a terminal state'
        assert session['status'] == 'completed', f"{diagnostic!r} · events: {seen!r}"
        rows = client.portal.call(app.state.store.segments,sid)
        assert [row['text'] for row in rows] == ['Hello there']
        assert all(row['speaker_label'] is None for row in rows)
        assert all(request.url.host in {'localhost','127.0.0.1'} for request in server.requests)


@pytest.mark.asyncio
async def test_check_ollama_reports_status_and_rejects_unconfigured(tmp_path):
    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.service import DiscoveryService

    app,token = create_app(storage='encrypted',database=tmp_path/'check.sqlcipher',
        key_store=Keys(),discovery_factory=lambda p:DiscoveryService(p))
    with TestClient(app,base_url='http://127.0.0.1') as client, client.websocket_connect(
            'ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
        ws.send_text(token)
        while ws.receive_json().get('type') != 'snapshot':
            pass
        ws.send_json({'action':'check_ollama'})
        reply = ws.receive_json()
        assert reply['type'] == 'error' and reply['context'] == 'meeting'
        assert 'Ollama' in reply['message'] and 'Preferences' in reply['message']


def test_desktop_checks_unsaved_model_and_applies_local_settings_without_restart(tmp_path,monkeypatch):
    from pathlib import Path
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from scribeloop_core.api.desktop_contract import DesktopContract
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.service import DiscoveryService
    settings = DesktopSettings(Vault())
    contract = DesktopContract(settings,Path('/fake/helper'))
    seen = []
    async def ready(config,publish,**kwargs):
        seen.append(config.model)
        await publish({'type':'local_model_status','state':'ready','status':config.model+' ready'})
        return 'ready'
    monkeypatch.setattr(lt,'ensure_model_ready',ready)
    def recording(store,audio,source,publish):
        return TranscriptionRouter(store,audio,source,publish,cloud_config=DeepgramConfig())
    app,token = create_app(storage='encrypted',database=tmp_path/'draft.sqlcipher',
        key_store=Keys(),desktop=contract,recording_factory=recording,
        summary_provider=settings.value.summary(),discovery_factory=lambda p:DiscoveryService(p))
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        while ws.receive_json()['type'] != 'snapshot': pass
        def send(command):
            rid = uuid4().hex
            ws.send_json({'protocol':2,'request_id':rid,'command':command})
            return rid
        send({'action':'check_ollama','model':'gemma4:e2b'})
        while (event := ws.receive_json())['type'] != 'local_model_status': pass
        assert event['state'] == 'ready' and seen == ['gemma4:e2b']
        assert settings.value.transcription_engine == 'deepgram'
        assert settings.value.local_transcription_model == ''  # Check never saves the draft.
        for engine in ['ollama','deepgram','ollama']:
            rid = send({'action':'save_settings','settings':{'transcription_engine':engine,
                'local_transcription_model':'gemma4:e2b'}})
            while (event := ws.receive_json()).get('request_id') != rid: pass
            assert event['status'] == 'accepted'
            assert app.state.recording.selected == engine
            assert app.state.recording.cloud.public()['cloud'] == (engine=='deepgram')
        send({'action':'check_ollama','model':'gemma4:cloud'})
        while (event := ws.receive_json())['type'] != 'local_model_status': pass
        assert event['state'] == 'unavailable' and event['status']


