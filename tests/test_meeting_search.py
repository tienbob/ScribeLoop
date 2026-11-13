import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.storage.db import SCHEMA_VERSION, connect, migrate
from scribeloop_core.storage.embeddings import EmbeddingConfig, pack_embedding
from scribeloop_core.storage.search import MeetingSearch, _summary_text
from scribeloop_core.storage.sessions import SessionStore


class Vault:
    value=None
    def get_password(self,_service,_account):
        return self.value
    def set_password(self,_service,_account,value):
        self.value=value


def meeting(store,title,words,*,at=1_700_000_000_000):
    row=store.create_recording(title,{})
    store.conn.execute('UPDATE sessions SET started_at=?,status=? WHERE id=?',(at,'completed',row['id']))
    for index,value in enumerate(words):
        store.append(row['id'],{'text':value,'start_ms':index*1000,'end_ms':index*1000+800,
                     'is_final':True,'speaker_key':f"deepgram:{row['id']}:mic:1"},stream='mic')
    store.conn.commit()
    return row['id']


def test_search_migration_and_selected_revision_invalidation():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    sid=meeting(store,'Pricing review',['The enterprise price is forty dollars'])
    search=MeetingSearch(store)
    result=search.query('enterprise price')
    assert result['index']['complete'] and result['results'][0]['session_id']==sid
    assert result['results'][0]['source_type']=='transcript'
    assert result['results'][0]['revision']==1
    old=store.segments(sid)[0]
    with conn:
        conn.execute('INSERT INTO transcript_revisions(session_id,revision,origin) VALUES (?,?,?)',(sid,2,'replacement'))
        conn.execute('INSERT INTO segments(id,session_id,stream,local_speaker_id,start_ms,end_ms,text,language,confidence,source,revision) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?)',('b'*32,sid,'mic',old['local_speaker_id'],0,800,
                      'The approved price is fifty dollars','en',1.0,'offline',2))
    # Unversioned notes from the old transcript must not be promoted to revision 2.
    with conn:
        conn.execute('INSERT INTO summaries(id,session_id,kind,content_md,created_at) VALUES (?,?,?,?,?)',
                     ('d'*32,sid,'provisional','obsolete confidential codename ORCHID',1))
    assert not search.query('forty dollars')['results']
    assert not search.query('ORCHID')['results']
    updated=search.query('fifty dollars')['results']
    assert updated and updated[0]['revision']==2
    assert conn.execute('SELECT COUNT(*) FROM search_documents WHERE session_id=?',(sid,)).fetchone()[0]==2


def test_search_title_summary_accent_filters_and_delete():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    old=meeting(store,'Kế hoạch quý',['Chúng ta thống nhất ngân sách'],at=1_600_000_000_000)
    new=meeting(store,'Customer planning',['Alice owns the launch'],at=1_800_000_000_000)
    revision=store.get(new)['transcript_revision']
    with conn:
        conn.execute('INSERT INTO grounded_summaries(id,session_id,revision,input_hash,provider,model,summary_json,created_at) '
                     'VALUES (?,?,?,?,?,?,?,?)',('c'*32,new,revision,'wrong','test','test',
                      json.dumps({'overview':{'text':'Renewal decision approved','citation_ids':[]}}),1))
    search=MeetingSearch(store)
    # A stale grounded summary is intentionally excluded.
    assert not search.query('Renewal decision')['results']
    title_results=search.query('ke hoach')['results']
    assert title_results[0]['session_id']==old
    assert [row['source_type'] for row in title_results]==['title']
    assert not search.query('ngan sach',date_from=1_700_000_000_000)['results']
    assert search.query('Alice',meeting_id=new)['results']
    store.rename_conversation(old,'Kế hoạch năm')
    assert not search.query('quý')['results']
    assert [row['source_type'] for row in search.query('ke hoach nam')['results']]==['title']
    store.begin_delete(old); store.finish_delete(old)
    assert not search.query('ke hoach nam')['results']
    if search.fts:
        assert not conn.execute("SELECT rowid FROM search_documents_fts WHERE search_documents_fts MATCH 'ke'").fetchall()


def test_interrupted_sync_is_idempotent():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    meeting(store,'One',['alpha']); meeting(store,'Two',['beta'])
    search=MeetingSearch(store)
    first=search.sync(limit=1)
    assert not first['complete'] and first['pending']==1
    second=search.sync()
    assert second['complete'] and second['pending']==0
    count=conn.execute('SELECT COUNT(*) FROM search_documents').fetchone()[0]
    search.sync()
    assert conn.execute('SELECT COUNT(*) FROM search_documents').fetchone()[0]==count


def test_summary_search_text_excludes_internal_ids_and_citations():
    value={'overview':{'id':'private-node-id','text':'The launch is approved',
                       'citation_ids':['private-citation-id']},'decision_id':'internal'}
    assert _summary_text(value) == 'The launch is approved'


@pytest.mark.asyncio
async def test_hybrid_search_retrieves_vietnamese_with_english_question_and_keeps_scope():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    relevant=meeting(store,'Thiết kế',['Ứng dụng được nhắc đến như một trợ lý ghi chú cuộc họp'])
    outside=meeting(store,'Other',['Ứng dụng cũng được nhắc ở đây'])
    class FakeMultilingual:
        async def embed(self,texts):
            values=[]
            for text in texts:
                vector=[0.0]*32
                vector[0]=1 if ('how did the app' in text.casefold() or 'trợ lý ghi chú' in text.casefold()) else .1
                vector[1]=1 if 'cũng được nhắc' in text.casefold() else 0
                values.append(pack_embedding(vector,32))
            return values
    config=EmbeddingConfig('Custom','http://localhost:1234/v1','test-multilingual',32)
    result=await MeetingSearch(store,embedding_config=config,embedder=FakeMultilingual()).query_async(
        'How did the app work in the conversation?',meeting_id=relevant,match_all=False)
    assert result['retrieval_mode']=='hybrid'
    assert result['results'] and result['results'][0]['session_id']==relevant
    assert all(row['session_id'] != outside for row in result['results'])


@pytest.mark.asyncio
async def test_async_reconciliation_yields_and_desktop_requires_agreement():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    for index in range(30):
        meeting(store,f'Meeting {index}',[f'roadmap item {index}'])
    search=MeetingSearch(store)
    yielded=False
    def mark_yielded():
        nonlocal yielded
        yielded=True
    asyncio.get_running_loop().call_soon(mark_yielded)
    result=await search.query_async('roadmap')
    assert yielded and result['results']

    desktop=DesktopContract(DesktopSettings(Vault()),Path('/fake/helper'))
    app=SimpleNamespace(state=SimpleNamespace(store=store,recording=None))
    command={'action':'search_meetings','query':'roadmap'}
    with pytest.raises(ValueError,match='after setup'):
        await desktop.read(app,command,desktop.settings.value.summary(),None,None)
    store.accept_agreement(store.AGREEMENT_VERSION)
    running=SimpleNamespace(done=lambda:False)
    app.state.recording=SimpleNamespace(task=running)
    with pytest.raises(ValueError,match='while recording is idle'):
        await desktop.read(app,command,desktop.settings.value.summary(),None,None)
    app.state.recording=None
    allowed=await desktop.read(app,command,desktop.settings.value.summary(),None,None)
    assert allowed['type']=='search_results' and allowed['results']
