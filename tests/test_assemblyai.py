"""AssemblyAI contracts with deterministic transports; no API credentials or uploads."""
import json
import threading
from collections import deque

import httpx
import pytest
from scribeloop_core.api.desktop_settings import DesktopSettings, Settings
from scribeloop_core.pipeline.assemblyai_meeting import (
    AssemblyAIConfig,
    _track,
    language_options,
    rows_for_words,
    run_batch,
)
from scribeloop_core.pipeline.deepgram_meeting import DeepgramError, StreamingBudget
from scribeloop_core.pipeline.model_selection import TranscriptionRouter
from test_deepgram_batch import mic_only_saved, saved
from test_retained_audio import retained as retained_fixture


@pytest.fixture()
def retained(tmp_path):
    yield from retained_fixture.__wrapped__(tmp_path)


def test_keys_private_independent_and_restore_clouds():
    class Vault:
        value = None
        def get_password(self,*_):
            return self.value
        def set_password(self,service,account,value):
            self.value = value
    settings = DesktopSettings(Vault())
    settings.save({'assemblyai_key':'assembly-test-key-123','deepgram_key':'deepgram-test-key-123',
                   'transcription_engine':'assemblyai'})
    public = settings.value.public()
    assert public['assemblyai_configured'] and public['deepgram_configured']
    assert 'assemblyai_key' not in public and 'assembly-test-key' not in json.dumps(public)
    cloud = settings.cloud()
    assert cloud.provider == 'assemblyai' and cloud.api_key == 'assembly-test-key-123'
    assert cloud.alternatives['deepgram'].api_key == 'deepgram-test-key-123'
    settings.save({'transcription_engine':'deepgram'})
    assert settings.cloud().alternatives['assemblyai'].api_key == 'assembly-test-key-123'
    with pytest.raises(ValueError,match='supported by AssemblyAI'):
        Settings(transcription_engine='assemblyai',language_hints=['th'])


def test_language_and_speaker_mapping():
    assert json.loads(language_options('vi',['en'])['language_codes']) == ['vi']
    assert json.loads(language_options('auto',['en','vi'])['language_codes']) == ['en','vi']
    words = [{'text':'Hello','start':0,'end':100,'speaker':'A'},
             {'text':'there','start':100,'end':200,'speaker':'B'}]
    rows = rows_for_words(words,stream='mic',epoch='a'*32,base=1000,sent=1200,language='en')
    assert [r['start_ms'] for r in rows] == [1000,1100]
    assert rows[0]['speaker_key'] == 'assemblyai:'+('a'*32)+':mic:1'
    assert len({r['speaker_key'] for r in rows}) == 2
    with pytest.raises(DeepgramError):
        rows_for_words(words,stream='mic',epoch='a'*32,base=1000,sent=1050,language='en')


def emitter(events):
    def emit(event):
        events.append(event)
        if event.get('type') == 'cloud_request':
            event['ack'].set()
    return emit


def test_prerecorded_upload_jobs_and_no_streaming(retained):
    sid = saved(retained)
    calls,events = [],[]
    def handler(request):
        calls.append((request.method,request.url.path))
        assert request.headers['authorization'] == 'test-key'
        if request.url.path.endswith('/upload'):
            assert request.content.startswith(b'RIFF')
            return httpx.Response(200,json={'upload_url':'https://cdn.assemblyai.com/upload/test'})
        if request.method == 'POST':
            body = json.loads(request.content)
            assert body['speaker_labels'] and body['speech_models'] == ['universal-2']
            return httpx.Response(200,json={'id':'test-job','status':'queued'})
        return httpx.Response(200,json={'id':'test-job','status':'completed','language_code':'en',
            'words':[{'text':'Hello','start':0,'end':100,'speaker':'A'}]})
    config = AssemblyAIConfig(api_key='test-key',batch_transport=httpx.MockTransport(handler))
    run_batch(retained.audio,sid,emitter(events),threading.Event(),config=config,
              budget=StreamingBudget(config,{}),language='auto',language_hints=['en','vi'])
    assert calls == [('POST','/v2/upload'),('POST','/v2/transcript'),('GET','/v2/transcript/test-job')]*2
    rows = [e for e in events if e.get('text')]
    assert len(rows) == 2 and all(r['speaker_key'].startswith('assemblyai:') for r in rows)
    assert all(e['reservation']['provider']=='assemblyai' for e in events if e.get('type')=='cloud_request')


def test_live_partial_final_dedup_termination(retained):
    sid = mic_only_saved(retained)
    events = []
    final = {'type':'Turn','turn_order':0,'end_of_turn':True,'language_code':'en',
             'words':[{'text':'Hello','start':0,'end':100,'speaker':'A'}]}
    class Socket:
        def __init__(self):
            self.events = deque([{'type':'Begin'}, {'type':'Turn','transcript':'Hel','end_of_turn':False},final,final,
                                 {'type':'Termination','audio_duration_seconds':.2}])
            self.sent = []
        def __enter__(self):
            return self
        def __exit__(self,*_):
            pass
        def send(self,value):
            self.sent.append(value)
        def recv(self,**_):
            return json.dumps(self.events.popleft())
    sock = Socket()
    def connector(url,**kwargs):
        assert url.startswith('wss://streaming.assemblyai.com/v3/ws?')
        assert 'speaker_labels=true' in url and 'universal-3-6-pro' in url
        assert kwargs['additional_headers']['Authorization']=='test-key'
        return sock
    config = AssemblyAIConfig(api_key='test-key',connector=connector)
    done = threading.Event(); done.set()
    _track(retained.audio,sid,'mic',emitter(events),threading.Event(),done,
           config=config,budget=StreamingBudget(config,{}),language='auto',language_hints=['en','vi'],checkpoint=0)
    assert any(e['type']=='partial' for e in events)
    assert len([e for e in events if e['type']=='confirmed_window' and e['segments']]) == 1
    assert events[-1]['end_ms'] == 200
    assert any(isinstance(v,str) and json.loads(v)['type']=='Terminate' for v in sock.sent)


@pytest.mark.asyncio
async def test_router_rerun_writes_assemblyai_provenance(retained):
    sid = mic_only_saved(retained)
    def handler(request):
        if request.url.path.endswith('/upload'):
            return httpx.Response(200,json={'upload_url':'https://cdn.assemblyai.com/upload/test'})
        return httpx.Response(200,json={'id':'test-job','status':'completed','language_code':'en',
            'words':[{'text':'Hello','start':0,'end':100,'speaker':'A'}]})
    config = AssemblyAIConfig(api_key='test-key',batch_transport=httpx.MockTransport(handler))
    async def publish(event):
        pass
    runner = TranscriptionRouter(retained.store,retained.audio,lambda:None,publish,cloud_config=config)
    assert runner.selected == 'assemblyai'
    assert runner.profiles()['assemblyai']['cloud']
    runner.select_model('assemblyai')
    runner.reprocess(sid,language='en',cloud_consent=True)
    await runner.task
    assert retained.store.get(sid)['status'] == 'completed'
    assert retained.store.transcript_provider(sid) == 'assemblyai'
    assert retained.store.revisions(sid)[0]['model'] == 'universal-2'
    assert retained.store.segments(sid)[0]['local_speaker_id']
    await runner.close()


def test_final_speaker_correction_preserves_words_and_checkpoints(retained):
    sid = mic_only_saved(retained)
    epoch = 'b'*32
    words = [{'text':'Hello','start':0,'end':100,'speaker':'A'},
             {'text':'again','start':100,'end':200,'speaker':'A'}]
    old = rows_for_words(words,stream='mic',epoch=epoch,base=0,sent=200,language='en')
    retained.store.checkpoint(sid,'mic',recorded_ms=200)
    retained.store.commit_window(sid,'mic',old,200)
    checkpoints = retained.store.checkpoints(sid)
    words[1]['speaker'] = 'B'
    revised = rows_for_words(words,stream='mic',epoch=epoch,base=0,sent=200,language='en')
    retained.store.relabel_speech(sid,'mic',old,revised)
    result = retained.store.segments(sid)
    assert [r['text'] for r in result] == ['Hello','again']
    assert len({r['local_speaker_id'] for r in result}) == 2
    assert retained.store.checkpoints(sid) == checkpoints
    changed = [{**r,'text':'invented'} for r in revised]
    with pytest.raises(ValueError,match='changed transcript content'):
        retained.store.relabel_speech(sid,'mic',revised,changed)
    assert retained.store.segments(sid) == result


def test_pending_speaker_keeps_transcript_unattributed():
    rows = rows_for_words([{'text':'Yep','start':0,'end':100,'speaker':'PENDING'}],
        stream='mic',epoch='a'*32,base=0,sent=100,language='en',speaker='A')
    assert rows[0]['text'] == 'Yep' and rows[0]['speaker_key'] is None
