"""Offline acceptance-harness checks; no Keychain or provider requests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from scribeloop_core.llm.meeting_ask import AskScope, MeetingAsk
from scribeloop_core.llm.summary_provider import SummaryProvider
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.embeddings import EmbeddingConfig, pack_embedding
from scribeloop_core.storage.sessions import SessionStore

spec=importlib.util.spec_from_file_location('workspace_eval',Path(__file__).parents[1]/'scripts/evaluate_meeting_workspace.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


def test_fixture_labels_are_versioned_and_resolve():
    fixture=json.loads(runner.FIXTURE.read_text())
    assert len(fixture['meetings'])==30 and len(fixture['questions'])==40
    assert len(fixture['recurring_topics'])==10
    assert sum(q['kind']=='answerable' for q in fixture['questions'])==30
    assert sum(q['kind']!='answerable' for q in fixture['questions'])==10
    assert 'pending' in fixture['label_review']
    conn=migrate(connect(':memory:'));store=SessionStore(conn)
    try:
        ids,sources=runner.seed(store,fixture)
        assert len(store.list())==30
        for q in fixture['questions']:
            for source in q['support']:assert (source['meeting_id'],source['segment']) in sources
        assert len(set(ids.values()))==30
    finally:conn.close()


async def test_budget_wrapper_preserves_provider_metadata_and_real_ask_persistence():
    async def reply(messages):
        payload=json.loads(messages[-1]['content']);citation=payload['untrusted_evidence'][0]['citation_id']
        return json.dumps({'claims':[{'text':'A proposal was discussed.','kind':'proposal','citation_ids':[citation]}],
                           'insufficient_evidence':False,'caveat':None})
    provider=SummaryProvider(settings={'SUMMARY_BASE_URL':'https://fake.example/v1','SUMMARY_PROVIDER':'Test',
        'SUMMARY_MODEL':'test','SUMMARY_API_KEY':'fake'},request=reply)
    budget=runner.CallBudget()
    wrapper=runner.BoundedAnswers(provider,budget)
    assert wrapper.provider=='Test' and wrapper.model=='test'
    conn=migrate(connect(':memory:'));store=SessionStore(conn)
    try:
        fixture=json.loads(runner.FIXTURE.read_text());ids,_=runner.seed(store,fixture)
        sid=ids['Atlas-discovery']
        value=await MeetingAsk(store,wrapper).answer('What was the proposal?',AskScope.resolve(store,'current',current_id=sid))
        assert value['saved'] and value['provider']=='Test' and value['model']=='test'
        assert budget.calls==1 and budget.input_bytes>0
    finally:conn.close()


async def test_runner_stops_on_quota_and_preserves_checkpoint(tmp_path,monkeypatch):
    fixture=json.loads(runner.FIXTURE.read_text())
    config=EmbeddingConfig('Custom','https://fake.example/v1','test',32,'fake')
    class Embeddings:
        def __init__(self,_config):pass
        async def embed(self,texts):return [pack_embedding([1.]+[0.]*31,32) for _ in texts]
    async def answer(*args,**kwargs):
        raise httpx.HTTPStatusError('private',request=httpx.Request('POST','https://fake.example'),response=httpx.Response(429))
    monkeypatch.setattr(runner,'CompatibleEmbeddingProvider',Embeddings)
    monkeypatch.setattr(runner,'connect_encrypted',lambda *args:migrate(connect(':memory:')))
    provider=SummaryProvider(settings={'SUMMARY_BASE_URL':'https://fake.example/v1',
        'SUMMARY_MODEL':'test','SUMMARY_API_KEY':'fake'})
    monkeypatch.setattr(provider,'generate',answer)
    settings=SimpleNamespace(embedding=lambda:config,summary=lambda:provider)
    metadata=await runner.evaluate(SimpleNamespace(runs=3,allow_cloud=True,output_dir=tmp_path),settings)
    assert metadata['completed_cases']==1 and metadata['stopped_early']
    report=json.loads((tmp_path/'results.json').read_text())
    assert report['results'][0]['error']=='ask_answer_http_429'
    assert report['human_grounding_review']=='pending'
    assert 'private' not in (tmp_path/'results.json').read_text()
    assert (tmp_path/'human-review.csv').is_file() and len(fixture['questions'])==40


def test_budget_cannot_dispatch_after_exhaustion():
    budget=runner.CallBudget(max_calls=1,max_input_bytes=10)
    budget.charge(10)
    with pytest.raises(ValueError):budget.charge(1)
    assert budget.calls==1 and budget.input_bytes==10
