"""Long transcript notes: bounded requests, durable cache, retries and citations."""
import json

import pytest
from scribeloop_core.llm.rolling import RollingNotes
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


@pytest.fixture()
def state(tmp_path):
    conn=migrate(connect(tmp_path/'notes.db'))
    store=SessionStore(conn)
    sid=store.create_recording('Long meeting',{'scope':'microphone','duration_s':10800})['id']
    yield store,sid
    conn.close()


class Provider:
    provider='Test'
    model='test-model'
    def __init__(self,*,fail=False,bad=False):
        self.calls=[]
        self.fail=fail
        self.bad=bad
    def public(self):
        return {'endpoint':'https://test.invalid/v1','configured':True}
    async def generate(self,messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError('Simulated ambiguous provider error')
        payload=json.loads(messages[1]['content'])
        identity=(payload['untrusted_transcript'][0]['id'] if 'untrusted_transcript' in payload
                  else payload['allowed_original_segment_ids'][0])
        if self.bad:
            identity='invented-id'
        return {'overview':{'text':'Supported meeting notes','segment_ids':[identity]},'key_points':[],
                'decisions':[],'action_items':[],'open_questions':[],'uncertainties':[]}


def add_rows(store,sid,count=1000):
    chunks=[{'stream':'mic','start_ms':i*5000,'end_ms':i*5000+4000,
        'text':'Discuss the project. '*45,'is_final':True} for i in range(count)]
    store.revise_transcript(sid,chunks,provider='deepgram',model='nova-3')
    store.transition(sid,'completed')
    return store.segments(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['once','always','network'])
async def test_automatic_repairs_only_one_received_invalid_summary(state,fault):
    store,sid=state
    rows=add_rows(store,sid,count=2)
    class RepairProvider(Provider):
        async def generate(self,messages):
            if fault=='network':
                self.calls.append(messages)
                raise RuntimeError('Unknown delivery')
            self.bad=fault=='always' or not self.calls
            return await super().generate(messages)
    provider=RepairProvider()
    engine=RollingNotes(store,provider)
    if fault=='once':
        assert await engine.run(sid,rows,revision=1,repair_invalid=True)
    else:
        with pytest.raises((RuntimeError,ValueError)):
            await engine.run(sid,rows,revision=1,repair_invalid=True)
    assert len(provider.calls)==(1 if fault=='network' else 2)
    job=store.conn.execute('SELECT state,attempts FROM summary_jobs WHERE session_id=?',(sid,)).fetchone()
    assert job['state']==('completed' if fault=='once' else 'failed')
    assert job['attempts']==len(provider.calls)


@pytest.mark.asyncio
async def test_hierarchical_notes_over_one_hundred_kb_cache_and_reuse(state):
    store,sid=state
    rows=add_rows(store,sid)
    assert len(json.dumps(rows))>100000
    provider=Provider()
    engine=RollingNotes(store,provider)
    result=await engine.run(sid,rows,revision=1)
    assert 2<len(provider.calls)<128
    assert all(len(json.dumps(m).encode())<=100000 for m in provider.calls)
    original_ids={r['id'] for r in rows}
    assert set(result['overview']['segment_ids'])<=original_ids
    calls=len(provider.calls)
    restarted=RollingNotes(SessionStore(store.conn),provider)
    assert await restarted.run(sid,rows,revision=1)==result
    assert len(provider.calls)==calls, 'Restart uses completed durable jobs without upload'
    _,fingerprint,_=store.summary_input(sid,1,build_messages=False)
    store.save_grounded_summary(sid,1,fingerprint,result,provider=provider.provider,model=provider.model)
    assert store.grounded_summary(sid)['stale'] is False


@pytest.mark.asyncio
async def test_rolling_page_cache_survives_future_audio_but_invalidates_source_edits(state,monkeypatch):
    store,sid=state
    rows=add_rows(store,sid,count=2)
    engine=RollingNotes(store,Provider())
    await engine.run(sid,rows,revision=1)
    first=engine.latest(sid)
    assert first
    original=store.segments
    def forbidden(*a,**k): raise AssertionError('Warm page must not scan transcript')
    monkeypatch.setattr(store,'segments',forbidden)
    store.checkpoint(sid,'mic',recorded_ms=2000000)
    store.append(sid,{'text':'New later section','start_ms':1800000,'end_ms':1801000,'is_final':True},stream='mic')
    assert RollingNotes(store,Provider()).latest(sid)==first
    monkeypatch.setattr(store,'segments',original)
    with store.conn:
        store.conn.execute('UPDATE segments SET text=? WHERE id=?',('Changed source',rows[0]['id']))
    assert engine.latest(sid) is None
    monkeypatch.setattr(store,'segments',forbidden)
    assert engine.latest(sid) is None, 'Stale-note reads are cached without reviving invalid notes'


@pytest.mark.asyncio
async def test_grounded_hash_cache_ignores_audio_watermarks_and_detects_text_changes(state,monkeypatch):
    store,sid=state
    rows=add_rows(store,sid,count=2)
    provider=Provider()
    result=await RollingNotes(store,provider).run(sid,rows,revision=1)
    _,fingerprint,_=store.summary_input(sid,1,build_messages=False)
    store.save_grounded_summary(sid,1,fingerprint,result,provider=provider.provider,model=provider.model)
    assert store.grounded_summary(sid)['stale'] is False
    original=store.segments
    def forbidden(*a,**k): raise AssertionError('Audio progress must not rehash transcript')
    monkeypatch.setattr(store,'segments',forbidden)
    store.checkpoint(sid,'mic',recorded_ms=10000)
    assert store.grounded_summary(sid)['stale'] is False
    extra=store.append(sid,{'text':'Later words','start_ms':11000,'end_ms':12000,'is_final':True},stream='mic')
    assert store.grounded_summary(sid)['stale'] is True, 'An append changes the source count without a full rehash'
    monkeypatch.setattr(store,'segments',original)
    with store.conn:
        store.conn.execute('DELETE FROM segments WHERE id=?',(extra['id'],))
    assert store.grounded_summary(sid)['stale'] is False
    with store.conn:
        store.conn.execute('UPDATE segments SET text=? WHERE id=?',('Changed source',rows[0]['id']))
    assert store.grounded_summary(sid)['stale'] is True


@pytest.mark.asyncio
async def test_note_read_caches_invalidate_and_restore_for_speaker_and_person_edits(state):
    from uuid import uuid4
    store,sid=state
    source=store.append(sid,{'text':'Synthetic statement','start_ms':0,'end_ms':1000,
        'is_final':True,'speaker_key':'deepgram:'+'b'*32+':mic:0'},stream='mic')
    store.transition(sid,'completed')
    rows=store.segments(sid)
    engine=RollingNotes(store,Provider())
    summary=await engine.run(sid,rows,revision=1)
    _,fingerprint,_=store.summary_input(sid,1,build_messages=False)
    store.save_grounded_summary(sid,1,fingerprint,summary,provider='Test',model='test-model')
    assert engine.latest(sid) and not store.grounded_summary(sid)['stale']
    speaker=source['local_speaker_id']
    with store.conn:
        store.conn.execute('UPDATE session_speakers SET local_label=? WHERE id=?',('Changed name',speaker))
    assert engine.latest(sid) is None and store.grounded_summary(sid)['stale']


    person=uuid4().hex
    with store.conn:
        store.conn.execute('INSERT INTO speakers(id,canonical_name) VALUES (?,?)',(person,'Different person'))
        store.conn.execute('INSERT INTO person_links(session_id,revision,speaker_id,person_id) VALUES (?,1,?,?)',(sid,speaker,person))
    assert engine.latest(sid) is None and store.grounded_summary(sid)['stale']
    with store.conn:
        store.conn.execute('UPDATE speakers SET canonical_name=? WHERE id=?',('Speaker 1',person))
    assert engine.latest(sid) and not store.grounded_summary(sid)['stale']
    with store.conn:
        store.conn.execute('INSERT INTO revision_speaker_edits(session_id,revision,speaker_id,label) VALUES (?,1,?,?)',(sid,speaker,'Edited label'))
        store.conn.execute('DELETE FROM person_links WHERE session_id=?',(sid,))
    assert engine.latest(sid) is None and store.grounded_summary(sid)['stale']


@pytest.mark.asyncio
async def test_note_validation_works_when_shared_cache_is_disabled(state):
    from scribeloop_core.storage.memory_cache import MemoryCache
    store,sid=state
    store.memory_cache=MemoryCache(0)
    rows=add_rows(store,sid,count=2)
    provider=Provider()
    result=await RollingNotes(store,provider).run(sid,rows,revision=1)
    _,fingerprint,_=store.summary_input(sid,1,build_messages=False)
    store.save_grounded_summary(sid,1,fingerprint,result,provider='Test',model='test-model')
    assert not store.grounded_summary(sid)['stale']
    assert RollingNotes(store,provider).latest(sid)


@pytest.mark.asyncio
async def test_dispatch_failure_is_never_automatically_retried(state):
    store,sid=state
    rows=add_rows(store,sid,count=2)
    provider=Provider(fail=True)
    engine=RollingNotes(store,provider)
    with pytest.raises(RuntimeError):
        await engine.run(sid,rows,revision=1)
    provider.fail=False
    with pytest.raises(ValueError,match='explicit retry'):
        await RollingNotes(store,provider).run(sid,rows,revision=1)
    assert len(provider.calls)==1
    await engine.run(sid,rows,revision=1,retry=True)
    assert len(provider.calls)==2
    assert engine.public(sid)['requests']==2


@pytest.mark.asyncio
async def test_citation_rejection_and_authorization_budget_precede_dispatch(state):
    store,sid=state
    rows=add_rows(store,sid,count=1)
    provider=Provider(bad=True)
    engine=RollingNotes(store,provider)
    with pytest.raises(ValueError,match='unknown'):
        await engine.run(sid,rows,revision=1)
    assert store.conn.execute('SELECT state FROM summary_jobs').fetchone()[0]=='failed'
    provider.bad=False
    engine=RollingNotes(store,provider,authorized=lambda:False)
    with pytest.raises(ValueError):
        await engine.run(sid,rows,revision=1,retry=True)
    assert len(provider.calls)==1


@pytest.mark.asyncio
async def test_rolling_only_summarizes_confirmed_closed_sections(state):
    store,sid=state
    rows=add_rows(store,sid,count=190)
    store.transition(sid,'recording')
    provider=Provider()
    engine=RollingNotes(store,provider)
    assert await engine.run(sid,rows,revision=1,rolling=True)==[]
    store.checkpoint(sid,'mic',recorded_ms=950000)
    store.commit_window(sid,'mic',[],900000)
    notes=await engine.run(sid,rows,revision=1,rolling=True)
    assert notes
    calls=len(provider.calls)
    store.transition(sid,'completed')
    await engine.run(sid,rows,revision=1)
    assert len(provider.calls)>calls
    completed_sections=store.conn.execute("SELECT COUNT(*) FROM summary_jobs WHERE node_key LIKE 'section:%' AND state='completed'").fetchone()[0]
    assert completed_sections<len(provider.calls), 'Final reduction reuses section notes'


@pytest.mark.asyncio
async def test_summary_quota_survives_removal_of_derived_jobs(state):
    store,sid=state
    rows=add_rows(store,sid,count=1)
    provider=Provider()
    engine=RollingNotes(store,provider)
    await engine.run(sid,rows,revision=1)
    before=engine.public(sid)
    with store.conn:
        store.conn.execute('DELETE FROM summary_jobs WHERE session_id=?',(sid,))
    assert engine.public(sid)['requests']==before['requests']
    assert engine.public(sid)['input_bytes']==before['input_bytes']
    with store.conn:
        store.conn.execute('UPDATE summary_usage SET requests=128 WHERE session_id=?',(sid,))
    with pytest.raises(ValueError,match='budget'):
        await engine.run(sid,rows,revision=1,retry=True)
    assert len(provider.calls)==1


@pytest.mark.asyncio
async def test_v9_migration_preserves_spent_quota_and_is_idempotent(state):
    store,sid=state
    rows=add_rows(store,sid,count=1)
    engine=RollingNotes(store,Provider())
    await engine.run(sid,rows,revision=1)
    with store.conn:
        store.conn.execute('UPDATE summary_jobs SET attempts=3 WHERE session_id=?',(sid,))
        size=store.conn.execute('SELECT input_bytes FROM summary_jobs WHERE session_id=?',(sid,)).fetchone()[0]
        store.conn.execute('DROP TABLE summary_usage')
        store.conn.execute('PRAGMA user_version=9')
    migrate(store.conn)
    migrate(store.conn)
    budget=engine.public(sid)
    assert budget['requests']==3 and budget['input_bytes']==3*size


@pytest.mark.asyncio
async def test_editorial_instructions_apply_to_sections_reduction_and_cache(state):
    store,sid=state
    rows=add_rows(store,sid,count=80)
    provider=Provider()
    provider.instructions='Focus on commercial risks, owners and explicit deadlines.'
    engine=RollingNotes(store,provider)
    await engine.run(sid,rows,revision=1)
    assert len(provider.calls)>1
    assert all(json.loads(call[1]['content'])['summary_preferences']==provider.instructions for call in provider.calls)
    assert any('untrusted_section_notes' in call[1]['content'] for call in provider.calls)
    count=len(provider.calls)
    await engine.run(sid,rows,revision=1)
    assert len(provider.calls)==count
    provider.instructions='Write detailed engineering handover notes.'
    await engine.run(sid,rows,revision=1)
    assert len(provider.calls)>count
    assert all(json.loads(call[1]['content'])['summary_preferences']==provider.instructions for call in provider.calls[count:])
