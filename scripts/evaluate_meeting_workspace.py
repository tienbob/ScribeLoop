"""Versioned F1/F2 evaluation; explicit cloud opt-in, synthetic-only encrypted workspace.

Produces answers, evidence and a blank human-review worksheet. It never turns
citation validity, token matching or an LLM judge into human grounding acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import platform
import secrets
import tempfile
import time
from pathlib import Path

import httpx
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.async_compat import timeout
from scribeloop_core.llm.meeting_ask import AskScope, MeetingAsk, ask_failure
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.embeddings import (
    CompatibleEmbeddingProvider,
    GeminiEmbeddingProvider,
    LocalEmbeddingProvider,
)
from scribeloop_core.storage.encrypted import connect_encrypted
from scribeloop_core.storage.search import MeetingSearch
from scribeloop_core.storage.sessions import SessionStore

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/meeting_workspace_acceptance_v1.json'


class CallBudget:
    def __init__(self,max_calls=420,max_input_bytes=5_000_000):
        self.calls=0;self.input_bytes=0
        self.max_calls=max_calls;self.max_input_bytes=max_input_bytes

    def charge(self,size):
        if self.calls>=self.max_calls or self.input_bytes+size>self.max_input_bytes:
            raise ValueError('Evaluation provider budget exhausted')
        self.calls+=1;self.input_bytes+=size


class BoundedEmbedder:
    def __init__(self,provider,budget):self.provider,self.budget=provider,budget
    async def embed(self,texts):return await self._call(texts,False)
    async def embed_query(self,texts):return await self._call(texts,True)
    async def _call(self,texts,query):
        self.budget.charge(sum(len(t.encode()) for t in texts))
        call=getattr(self.provider,'embed_query',self.provider.embed) if query else self.provider.embed
        return await call(texts)


class BoundedAnswers:
    def __init__(self,provider,budget):
        self._inner,self.budget=provider,budget
    def __getattr__(self,name):return getattr(self._inner,name)
    async def generate(self,messages,**kwargs):
        self.budget.charge(len(self._inner.request_bytes(messages,
            schema=kwargs.get('schema'),max_output_tokens=kwargs.get('max_output_tokens',2000))))
        return await self._inner.generate(messages,**kwargs)


def seed(store,fixture):
    identities={};sources={}
    for i,meeting in enumerate(fixture['meetings']):
        sid=store.create_recording(meeting['title'],{'scope':'microphone'})['id']
        identities[meeting['id']]=sid
        for j,text in enumerate(meeting['texts']):
            row=store.append(sid,{'text':text,'start_ms':j*2000,'end_ms':j*2000+1800,
                'is_final':True,'language':meeting['language'],
                'speaker_key':f'deepgram:{sid}:mic:1'},stream='mic')
            sources[(meeting['id'],j)]=row['id']
        store.transition(sid,'completed')
        store.correct_speaker(sid,1,row['local_speaker_id'],label=meeting['speaker'])
        with store.conn:store.conn.execute('UPDATE sessions SET started_at=? WHERE id=?',(1790000000000+i*86400000,sid))
    # Exercise deletion before backfill; the removed source must never reach retrieval.
    deleted=store.create_recording('Deleted test meeting',{})['id']
    store.append(deleted,{'text':'Deleted canary SHOULDNOTAPPEAR','start_ms':0,'end_ms':1000,'is_final':True})
    store.transition(deleted,'completed');store.begin_delete(deleted);store.finish_delete(deleted)
    return identities,sources


def write_report(output,fixture,records,metadata):
    output.mkdir(parents=True,exist_ok=True)
    (output/'results.json').write_text(json.dumps({**metadata,'results':records},ensure_ascii=False,indent=2)+'\n')
    with (output/'human-review.csv').open('w',newline='') as handle:
        fields=['run','question_id','kind','question','expected_facts','forbidden_facts','answer',
                'citation_ids_valid','support_recall_at_5','support_recall_at_20',
                'label_reviewer','label_approved','human_grounding_pass',
                'abstention_or_conflict_pass','reviewer','notes']
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        by_id={q['id']:q for q in fixture['questions']}
        for result in records:
            q=by_id[result['question_id']]
            writer.writerow({'run':result['run'],'question_id':q['id'],'kind':q['kind'],'question':q['question'],
                'expected_facts':' | '.join(q['expected_facts']),'forbidden_facts':' | '.join(q['forbidden_facts']),
                'answer':json.dumps(result.get('answer',{}),ensure_ascii=False),
                'citation_ids_valid':result.get('citation_ids_valid',False),
                'support_recall_at_5':result.get('support_recall_at_5',False),
                'support_recall_at_20':result.get('support_recall_at_20',False)})


async def evaluate(args,settings):
    fixture=json.loads(FIXTURE.read_text())
    config=settings.embedding()
    provider=settings.summary()
    if config is None or not provider.configured:raise ValueError('Configure Hybrid search and notes first')
    if not args.allow_cloud and (provider.cloud or config.provider not in {'Local','Ollama','LM Studio'}):
        raise ValueError('Live synthetic evaluation requires --allow-cloud')
    embedding=(LocalEmbeddingProvider(config) if config.provider=='Local' else
               GeminiEmbeddingProvider(config) if config.provider=='Gemini' else CompatibleEmbeddingProvider(config))
    budget=CallBudget();embedder=BoundedEmbedder(embedding,budget);answerer=BoundedAnswers(provider,budget)
    records=[]
    metadata={'fixture_version':fixture['version'],'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'platform':platform.platform(),'python':platform.python_version(),'runs_planned':args.runs,
        'questions_per_run':len(fixture['questions']),'answer_provider':provider.public(),
        'embedding':{'provider':config.provider,'model':config.model,'dimensions':config.dimensions},
        'storage':'temporary SQLCipher; random ephemeral key','human_grounding_review':'pending',
        'label_review':fixture['label_review'],'limitations':fixture['limitations'],
        'provider_max_calls':budget.max_calls,'provider_max_input_bytes':budget.max_input_bytes}
    with tempfile.TemporaryDirectory(prefix='scribeloop-workspace-eval-') as directory:
        conn=migrate(connect_encrypted(Path(directory)/'synthetic.sqlcipher',secrets.token_bytes(32)))
        store=SessionStore(conn);store.accept_agreement(store.AGREEMENT_VERSION)
        identities,sources=seed(store,fixture)
        try:
            for run in range(1,args.runs+1):
                for q in fixture['questions']:
                    started=time.monotonic()
                    ids=[identities[x] for x in q['meeting_ids']]
                    scope=AskScope.resolve(store,q['scope'],current_id=ids[0] if q['scope']=='current' else None,
                                           selected_ids=ids if q['scope']=='selected' else None)
                    item={'run':run,'question_id':q['id'],'kind':q['kind']}
                    stop=False
                    try:
                        async with timeout(120):
                            # Measure Search with the same explicit question scope, then Ask.
                            found=await MeetingSearch(store,embedding_config=config,embedder=embedder,
                                include_titles=False).query_async(q['question'][:200],meeting_ids=list(scope.meeting_ids),limit=20)
                            expected={sources[(r['meeting_id'],r['segment'])] for r in q['support']}
                            search_ranks=[i for i,p in enumerate(found['results'],1) if p['source_id'] in expected]
                            item.update(search_support_at_5=bool(search_ranks and min(search_ranks)<=5),
                                        search_support_at_20=bool(search_ranks))
                            packet=await MeetingAsk(store,answerer,embedding_config=config,embedder=embedder).answer(q['question'],scope)
                        evidence=packet['evidence']['passages'];allowed={p['citation_id'] for p in evidence}
                        expected={sources[(r['meeting_id'],r['segment'])] for r in q['support']}
                        ranks=[i for i,p in enumerate(evidence,1) if p['source_id'] in expected]
                        claims=packet['answer']['claims']
                        item.update(answer=packet['answer'],evidence=evidence,usage=packet['usage'],
                            retrieval_mode=packet['evidence']['retrieval_mode'],
                            citation_ids_valid=all(set(c['citation_ids'])<=allowed for c in claims),
                            support_recall_at_5=bool(ranks and min(ranks)<=5),support_recall_at_20=bool(ranks),
                            human_grounding_pass=None,abstention_or_conflict_pass=None)
                        if packet['evidence']['retrieval_mode']!='hybrid':
                            item['degraded_retrieval']=True;stop=True
                    except Exception as error:  # noqa: BLE001 - safe diagnostics only; no raw provider errors
                        item['error']=ask_failure(error)['code']
                        item['error_type']=type(error).__name__
                        stop=(isinstance(error,(httpx.HTTPError,TimeoutError,asyncio.TimeoutError)) or
                              budget.calls>=budget.max_calls or item['error']=='ask_internal_error')
                    item['elapsed_ms']=round((time.monotonic()-started)*1000)
                    records.append(item)
                    metadata.update(provider_calls=budget.calls,provider_input_bytes=budget.input_bytes,
                                    completed_cases=len(records),stopped_early=stop)
                    write_report(args.output_dir,fixture,records,metadata)
                    print(json.dumps({'run':run,'case':q['id'],'error':item.get('error'),
                        'elapsed_ms':item['elapsed_ms'],'calls':budget.calls}),flush=True)
                    if stop:return metadata
            return metadata
        finally:conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-cloud',action='store_true')
    parser.add_argument('--runs',type=int,choices=[1,2,3],default=3)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if (args.output_dir/'results.json').exists():
        parser.error('Output already contains a run. Choose a new directory to preserve prior attempts.')
    # Saved provider configuration only, no private workspace or audio is opened.
    settings=DesktopSettings().value
    report=asyncio.run(evaluate(args,settings))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
