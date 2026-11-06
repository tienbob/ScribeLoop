"""Ollama model lifecycle: small models unload per task; big ones idle out or unload on quit."""
import json
import threading

import httpx
import pytest
from scribeloop_core.llm import transcript_cleanup
from scribeloop_core.llm.ollama_lifecycle import (
    KEEP_ALIVE,
    parameter_count_b,
    release,
    release_all,
    release_if_small,
    session_targets,
)
from scribeloop_core.llm.summary_provider import ollama_request_body
from scribeloop_core.pipeline import local_transcription as lt
from scribeloop_core.pipeline.local_transcription import (
    LocalRecordingRunner,
    LocalTranscriptionConfig,
    _persist_warm,
)
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore

ENDPOINT = 'http://127.0.0.1:11434/v1'


class FakeOllama:
    """Loopback stand-in: /api/show size metadata and counted /api/generate calls."""

    def __init__(self, *, size_b=None, show_status=200):
        self.size_b = size_b
        self.show_status = show_status
        self.generate_fail = None
        self.generates = []

    def handler(self, request):
        path = request.url.path
        if path == '/api/show':
            if self.show_status != 200:
                return httpx.Response(self.show_status)
            if self.size_b is None:
                return httpx.Response(200, json={'capabilities':['completion']})
            return httpx.Response(200, json={'details':{'parameter_size':f'{self.size_b}B'}})
        if path == '/api/generate':
            if self.generate_fail == 'connect':
                raise httpx.ConnectError('refused')
            self.generates.append((str(request.url), json.loads(request.read())))
            if self.generate_fail == '500':
                return httpx.Response(500)
            return httpx.Response(200, json={'done':True})
        raise AssertionError(path)

    def factory(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), **kwargs)

    def sync_factory(self, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(self.handler), **kwargs)


def test_parameter_count_prefers_the_raw_gguf_count_and_falls_back_to_details():
    assert parameter_count_b({'model_info':{'general.parameter_count':8029798400}}) == pytest.approx(8.0297984)
    assert parameter_count_b({'details':{'parameter_size':'0.6B'}}) == pytest.approx(0.6)
    assert parameter_count_b({'details':{'parameter_size':'412M'}}) == pytest.approx(0.412)
    assert parameter_count_b({'details':{'parameter_size':'27B'}}) == pytest.approx(27)
    for unknown in ({}, {'details':{'parameter_size':'large'}}, None):
        assert parameter_count_b(unknown) is None


@pytest.mark.asyncio
async def test_release_posts_keep_alive_zero_and_never_reaches_remote_endpoints():
    server = FakeOllama()
    assert await release(ENDPOINT,'tiny-model',client_factory=server.factory) is True
    url,body = server.generates[0]
    assert url == 'http://127.0.0.1:11434/api/generate'
    assert body == {'model':'tiny-model','keep_alive':0}
    remote = FakeOllama()
    assert await release('https://provider.example/v1','tiny-model',client_factory=remote.factory) is False
    assert remote.generates == []


@pytest.mark.asyncio
async def test_release_treats_provider_and_transport_failures_as_best_effort():
    failing = FakeOllama()
    failing.generate_fail = '500'
    assert await release('http://localhost:11434','m',client_factory=failing.factory) is False
    down = FakeOllama()
    down.generate_fail = 'connect'
    assert await release('http://localhost:11434','m',client_factory=down.factory) is False


@pytest.mark.asyncio
async def test_release_if_small_unloads_only_known_models_below_the_threshold():
    small = FakeOllama(size_b=0.6)
    assert await release_if_small(ENDPOINT,'asr',client_factory=small.factory) is True
    assert len(small.generates) == 1
    for size in (27,8.0):
        big = FakeOllama(size_b=size)
        assert await release_if_small(ENDPOINT,'big',client_factory=big.factory) is False
        assert big.generates == []
    unknown = FakeOllama(show_status=500)
    assert await release_if_small(ENDPOINT,'odd',client_factory=unknown.factory) is False
    assert unknown.generates == []


@pytest.mark.asyncio
async def test_release_all_is_deduplicated_skips_remote_and_never_raises():
    server = FakeOllama()
    await release_all([(ENDPOINT,'a'),('http://127.0.0.1:11434','a'),
                       ('https://provider.example/v1','b')],client_factory=server.factory)
    assert len(server.generates) == 1
    broken = FakeOllama()
    broken.generate_fail = 'connect'
    await release_all([(ENDPOINT,'a')],client_factory=broken.factory)


def test_session_targets_collect_loopback_ollama_models_only():
    from types import SimpleNamespace
    notes = SimpleNamespace(provider='Ollama',cloud=False,base_url=ENDPOINT,model='notes')
    cleanup = SimpleNamespace(provider='Ollama',cloud=False,base_url='http://localhost:11434/v1',model='clean')
    asr = SimpleNamespace(configured=True,base_url=ENDPOINT,model='asr')
    router = SimpleNamespace(cleanup_provider=cleanup,runners={'ollama':SimpleNamespace(config=asr)})
    assert session_targets(notes,router) == [
        (ENDPOINT,'notes'),('http://localhost:11434/v1','clean'),(ENDPOINT,'asr')]
    cloud = SimpleNamespace(provider='Gemini',cloud=True,base_url='https://provider.example/v1',model='g')
    assert session_targets(cloud,None) == []
    assert session_targets(None,SimpleNamespace(cleanup_provider=None)) == []


def test_ollama_request_bodies_carry_the_idle_keep_alive():
    body = ollama_request_body([{'role':'user','content':'synthetic'}],None,model='local')
    assert body['keep_alive'] == KEEP_ALIVE == '15m'
    assert body['options'] == {'num_ctx':16384,'num_predict':4000,'temperature':0.2}


def test_persist_warm_applies_the_idle_keep_alive_after_warming():
    server = FakeOllama()
    cfg = LocalTranscriptionConfig(model='tiny-audio-model',endpoint=ENDPOINT,
        client_factory=server.sync_factory)
    assert _persist_warm(cfg,threading.Event()) is True
    url,body = server.generates[0]
    assert url == 'http://127.0.0.1:11434/api/generate'
    assert body == {'model':'tiny-audio-model','keep_alive':KEEP_ALIVE}


@pytest.fixture()
def saved():
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    sid = store.create_recording('Local meeting',{'scope':'microphone','provider':'local-ollama'})['id']
    row = store.append(sid,{'text':'Nam will send 25 reports tomorrow','start_ms':100,'end_ms':1200,
        'is_final':True,'language':'en','speaker_key':'ollama:'+'a'*32+':mic:1'},stream='mic')
    store.transition(sid,'completed')
    store.transition(sid,'processing')
    yield store,sid,row
    conn.close()


class OllamaEditor:
    provider = 'Ollama'
    cloud = False
    configured = True
    model = 'local-text'
    base_url = ENDPOINT
    cleanup_validate = True
    cleanup_vocabulary = ()

    def __init__(self):
        self.calls = []

    async def generate(self,messages,*,schema):
        self.calls.append((messages,schema))
        rows = json.loads(messages[1]['content'])['untrusted_transcript']
        return json.dumps({'segments':[{'id':row['id'],'text':row['text']} for row in rows]})


@pytest.mark.asyncio
async def test_apply_cleanup_releases_small_ollama_cleanup_models(monkeypatch,saved):
    store,sid,_row = saved
    seen = []

    async def fake_release(base_url,model,*,client_factory=None):
        seen.append((base_url,model))
        return True

    monkeypatch.setattr(transcript_cleanup,'release_if_small',fake_release)
    async def publish(event):
        pass
    await transcript_cleanup.apply_cleanup(store,sid,OllamaEditor(),asr_model='audio',
        publish=publish,cancelled=threading.Event())
    assert seen == [(ENDPOINT,'local-text')]

    class CloudEditor(OllamaEditor):
        provider = 'Gemini'
        cloud = True
        base_url = 'https://provider.example/v1'

    seen.clear()
    await transcript_cleanup.apply_cleanup(store,sid,CloudEditor(),asr_model='audio',
        publish=publish,cancelled=threading.Event())
    assert seen == []


@pytest.mark.asyncio
async def test_local_finish_releases_the_small_asr_model(monkeypatch,saved):
    store,sid,_row = saved
    seen = []

    async def fake_release(base_url,model,*,client_factory=None):
        seen.append((base_url,model))
        return True

    monkeypatch.setattr(lt,'release_if_small',fake_release)
    async def publish(event):
        pass
    runner = LocalRecordingRunner(store,None,None,publish,
        config=LocalTranscriptionConfig(model='tiny-audio-model',endpoint=ENDPOINT))
    await runner._finish(sid)
    assert store.get(sid)['status'] == 'completed'
    assert seen == [(ENDPOINT,'tiny-audio-model')]

