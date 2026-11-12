"""Audit regressions: source fidelity, filtered retrieval, consent and accounting."""
import asyncio
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.llm.meeting_ask import AskScope, MeetingAsk
from scribeloop_core.llm.summary_provider import SummaryProvider
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.embeddings import EmbeddingConfig, pack_embedding
from scribeloop_core.storage.search import MeetingSearch, _summary_text
from scribeloop_core.storage.search_work import SearchAuthorizationError, SearchWork
from scribeloop_core.storage.sessions import SessionStore


@pytest.fixture
def store():
    conn=migrate(connect(':memory:'))
    yield SessionStore(conn)
    conn.close()


def meeting(store,texts,*,at=1700000000000):
    sid=store.create_recording('Synthetic audit meeting',{})['id']
    for i,text in enumerate(texts):
        store.append(sid,{'text':text,'start_ms':i*1000,'end_ms':i*1000+900,
            'is_final':True,'speaker_key':f'deepgram:{sid}:mic:1'},stream='mic')
    with store.conn:
        store.conn.execute("UPDATE sessions SET started_at=?,status='completed' WHERE id=?",(at,sid))
    return sid


CONFIG=EmbeddingConfig('Custom','http://localhost:1234/v1','synthetic-audit',32)


class Embedder:
    def __init__(self): self.calls=[]
    async def embed(self,texts):
        self.calls.append(texts)
        return [pack_embedding(([1.,0.] if 'NEEDLE' in text or 'architecture' in text else [0.,1.])+[0.]*30,32)
                for text in texts]


def provider(request):
    return SummaryProvider(settings={'SUMMARY_PROVIDER':'LM Studio','SUMMARY_BASE_URL':'http://127.0.0.1:1234/v1',
                                     'SUMMARY_MODEL':'audit'},request=request)


async def cited(messages):
    passage=json.loads(messages[1]['content'])['untrusted_evidence'][0]
    return {'claims':[{'text':'The architecture supports plugins.','kind':'fact',
                      'citation_ids':[passage['citation_id']]}], 'insufficient_evidence':False,'caveat':None}


@pytest.mark.asyncio
async def test_matching_tail_survives_bounded_evidence_and_reopens(store):
    sid=meeting(store,['background '*6000+' NEEDLE architecture supports plugins'])
    embedder=Embedder(); scope=AskScope.resolve(store,'current',current_id=sid)
    engine=MeetingAsk(store,provider(cited),embedding_config=CONFIG,embedder=embedder)
    result=await engine.answer('architecture',scope)
    passage=result['evidence']['passages'][0]
    assert 'NEEDLE' in passage['text'] and len(passage['text'])<=1200
    assert passage['excerpt_start']>48000
    history=await engine.history(scope)
    assert not history[0]['stale'] and history[0]['evidence'][0]['text']==passage['text']
    follow=await engine.answer('What about architecture?',scope,previous_answer_id=result['answer_id'])
    assert follow['saved']


@pytest.mark.asyncio
async def test_clipped_prefix_does_not_mark_new_answer_stale(store):
    sid=meeting(store,['architecture '+ 'x'*51000]); scope=AskScope.resolve(store,'current',current_id=sid)
    engine=MeetingAsk(store,provider(cited))
    result=await engine.answer('architecture',scope)
    assert result['evidence']['truncated']
    assert not (await engine.history(scope))[0]['stale']
    segment=store.segments(sid)[0]
    with store.conn: store.conn.execute('UPDATE segments SET text=? WHERE id=?',('Changed source',segment['id']))
    assert (await engine.history(scope))[0]['stale']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['date','person'])
async def test_excluded_chunks_cannot_starve_eligible_nearest_neighbor(store,kind):
    for i in range(25): meeting(store,[f'NEEDLE excluded {i}'],at=1600000000000)
    sid=meeting(store,['eligible source'],at=1800000000000)
    filters={'date_from':1700000000000}
    if kind=='person':
        filters={'person_id':store.segments(sid)[0]['display_speaker_id']}
    result=await MeetingSearch(store,embedding_config=CONFIG,embedder=Embedder()).query_async(
        'architecture',limit=5,**filters)
    assert result['results'][0]['session_id']==sid and len(result['results'])==1


@pytest.mark.asyncio
async def test_adjacent_boilerplate_does_not_inherit_semantic_rank(store):
    sid=meeting(store,['hello everyone']*15+['NEEDLE architecture supports plugins'])
    target=store.segments(sid)[-1]['id']
    result=await MeetingSearch(store,embedding_config=CONFIG,embedder=Embedder()).query_async(
        'How can the architecture be extended?',limit=5)
    assert result['results'][0]['source_id']==target


@pytest.mark.asyncio
async def test_utf8_budget_matches_the_actual_request_body():
    called=[]
    async def request(messages): called.append(messages); return {}
    model=provider(request)
    messages=[{'role':'system','content':'a'*6000},{'role':'user','content':'ệ'*16000}]
    assert len(model.request_bytes(messages))<100000
    await model.generate(messages)
    assert len(called)==1
    with pytest.raises(ValueError,match='request too large'):
        await model.generate([{'role':'user','content':'ệ'*34000}])
    assert len(called)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['documents','query','answer'])
@pytest.mark.parametrize('reaccept',[False,True])
async def test_revocation_invalidates_all_request_stages_even_after_reaccept(store,stage,reaccept):
    sid=meeting(store,['NEEDLE architecture '+str(i)+' x'*600 for i in range(20)])
    scope=AskScope.resolve(store,'current',current_id=sid)
    store.accept_agreement(store.AGREEMENT_VERSION)
    paused=asyncio.Event(); release=asyncio.Event(); calls=[]
    class Paused(Embedder):
        async def embed(self,texts):
            calls.append('query' if texts==['architecture'] else 'documents')
            if stage==calls[-1]: paused.set(); await release.wait()
            return await super().embed(texts)
    async def request(messages):
        calls.append('answer')
        if stage=='answer': paused.set(); await release.wait()
        return await cited(messages)
    model=provider(request); embedder=Paused()
    # Query/answer cases use a cached document index and then arm the chosen boundary.
    if stage!='documents':
        await MeetingSearch(store,embedding_config=CONFIG,embedder=Embedder()).query_async('architecture')
    work=SearchWork(authorize=store.search_authorization())
    task=asyncio.create_task(MeetingAsk(store,model,embedding_config=CONFIG,embedder=embedder,work=work).answer('architecture',scope))
    await asyncio.wait_for(paused.wait(),3)
    store.revoke_agreement()
    if reaccept: store.accept_agreement(store.AGREEMENT_VERSION)
    before=list(calls); release.set()
    with pytest.raises(SearchAuthorizationError): await task
    assert calls==before
    assert not store.conn.execute('SELECT id FROM meeting_answers').fetchall()
    if reaccept:
        fresh=await MeetingAsk(store,model,embedding_config=CONFIG,embedder=Embedder(),
            work=SearchWork(authorize=store.search_authorization())).answer('architecture',scope)
        assert fresh['saved']


@pytest.mark.asyncio
async def test_total_accounting_includes_cold_indexing_query_and_answer(store,monkeypatch):
    sid=meeting(store,['NEEDLE architecture'])
    class Delayed(Embedder):
        async def embed(self,texts):
            await asyncio.sleep(.01)
            return await super().embed(texts)
    async def request(messages): await asyncio.sleep(.01); return await cited(messages)
    original=MeetingSearch.sync_async
    async def lexical_sync(search,scope=None):
        await asyncio.sleep(.01)
        return await original(search,scope)
    monkeypatch.setattr(MeetingSearch,'sync_async',lexical_sync)
    engine=MeetingAsk(store,provider(request),embedding_config=CONFIG,embedder=Delayed())
    scope=AskScope.resolve(store,'current',current_id=sid)
    started=time.monotonic(); result=await engine.answer('architecture',scope); elapsed=(time.monotonic()-started)*1000
    usage=result['usage']
    assert usage['embedding_requests']==2 and usage['answer_requests']==1 and usage['total_requests']==3
    assert usage['total_duration_ms']>=25 and abs(usage['total_duration_ms']-elapsed)<10
    assert usage['retrieval_ms']>=15 and usage['answer_ms']>=5
    assert usage['indexing_ms']>=15, 'Include lexical reconciliation and semantic indexing'
    assert usage['answer_input_bytes']==usage['input_bytes'] and usage['embedding_input_bytes']>0
    history=await engine.history(scope)
    assert history[0]['usage']['total_requests']==3
    next_result=await engine.answer('architecture',scope)
    assert next_result['usage']['embedding_requests']==1
    assert next_result['usage']['answer_requests']==1 and next_result['usage']['total_requests']==2


def test_summary_index_does_not_include_internal_segment_ids():
    assert _summary_text({'overview':{'text':'Visible content','segment_ids':['opaque-source-id']}})=='Visible content'


@pytest.mark.asyncio
async def test_history_rechecks_authorization_after_reconciliation(store,monkeypatch):
    sid=meeting(store,['architecture'])
    scope=AskScope.resolve(store,'current',current_id=sid)
    store.accept_agreement(store.AGREEMENT_VERSION)
    await MeetingAsk(store,provider(cited)).answer('architecture',scope)
    work=SearchWork(authorize=store.search_authorization())
    original=MeetingSearch.sync_async
    async def revoke_at_return(search,scope=None):
        result=await original(search,scope)
        store.revoke_agreement(); store.accept_agreement(store.AGREEMENT_VERSION)
        return result
    monkeypatch.setattr(MeetingSearch,'sync_async',revoke_at_return)
    with pytest.raises(SearchAuthorizationError):
        await MeetingAsk(store,provider(cited),work=work).history(scope)


@pytest.mark.asyncio
async def test_ollama_metadata_await_cannot_bypass_revocation(store,monkeypatch):
    store.accept_agreement(store.AGREEMENT_VERSION)
    authorize=store.search_authorization(); calls=[]
    def respond(request):
        calls.append(request.url.path)
        store.revoke_agreement(); store.accept_agreement(store.AGREEMENT_VERSION)
        return httpx.Response(200,json={'capabilities':['completion']})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(**kw,transport=httpx.MockTransport(respond)))
    model=SummaryProvider(settings={'SUMMARY_PROVIDER':'Ollama','SUMMARY_BASE_URL':'http://localhost:11434/v1',
                                    'SUMMARY_MODEL':'audit'})
    with pytest.raises(SearchAuthorizationError):
        await model.generate([{'role':'user','content':'Synthetic text'}],authorize=authorize)
    assert calls==['/api/show']


@pytest.mark.parametrize('action',['ask_question','search_meetings','ask_evidence'])
def test_authenticated_revocation_stops_same_and_cross_socket_work(monkeypatch,action):
    class Vault:
        value=None
        def get_password(self,*_): return self.value
        def set_password(self,*args): self.value=args[-1]
    settings=DesktopSettings(Vault())
    settings.save({'search_mode':'hybrid','embedding_provider':'Custom',
        'embedding_endpoint':'http://localhost:1234/v1','embedding_model':'audit','embedding_dimensions':32})
    paused=threading.Event(); release=threading.Event(); calls=[]
    class Pending(Embedder):
        def __init__(self,config): super().__init__()
        async def embed(self,texts):
            calls.append('embedding'); paused.set()
            while not release.is_set():
                try: await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    # Exercise adapters which finish in-flight I/O despite cancellation.
                    continue
            return await super().embed(texts)
    async def request(messages): calls.append('answer'); return await cited(messages)
    monkeypatch.setattr('scribeloop_core.storage.search.CompatibleEmbeddingProvider',Pending)
    app,token=create_app(desktop=DesktopContract(settings,Path('/fake/helper')),summary_provider=provider(request))
    def send(ws,command): ws.send_json({'protocol':2,'request_id':uuid4().hex,'command':command})
    def receive(ws,kind):
        for _ in range(100):
            value=ws.receive_json()
            if value['type']==kind: return value
        raise AssertionError(kind)
    with TestClient(app) as client:
        def seed():
            app.state.store.accept_agreement(app.state.store.AGREEMENT_VERSION)
            return meeting(app.state.store,['architecture '+str(i)+' x'*600 for i in range(20)])
        sid=client.portal.call(seed)
        with client.websocket_connect('ws://127.0.0.1/ws') as one, client.websocket_connect('ws://127.0.0.1/ws') as two:
            one.send_text(token); receive(one,'snapshot'); two.send_text(token); receive(two,'snapshot')
            command=({'action':action,'query':'architecture','meeting_id':sid} if action=='search_meetings' else
                     {'action':action,'question':'architecture','scope':'current','current_id':sid})
            send(one,command)
            assert paused.wait(3)
            # Ask leaves its reader available; inline Search/evidence use another socket.
            control=one if action=='ask_question' else two
            send(control,{'action':'revoke_agreement'})
            assert not receive(control,'agreement')['agreement']['accepted']
            release.set()
            receive(one,'ask_cancelled' if action=='ask_question' else 'error')
            assert calls==['embedding']
            assert not client.portal.call(lambda:list(app.state.store.conn.execute('SELECT id FROM meeting_answers')))
