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
