"""Bounded notes; unknown deliveries never retry, known invalid output can repair once."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json

from ..async_compat import timeout
from .grounded import summary_messages, transcript_hash, validate_summary

MAX_CALLS=128
MAX_BYTES=8_000_000
SECTION_BYTES=48_000


class InvalidSummaryOutput(ValueError):
    """Provider returned a complete response that failed the grounding contract."""


def summary_failure_code(exc):
    from .summary_provider import IncompleteSummaryOutput
    if isinstance(exc,IncompleteSummaryOutput):
        return 'summary_incomplete_output'
    if isinstance(exc,InvalidSummaryOutput):
        return 'summary_invalid_output'
    if isinstance(exc,asyncio.CancelledError):
        return 'summary_cancelled'
    if isinstance(exc,TimeoutError):
        return 'summary_timeout'
    import httpx
    if isinstance(exc,httpx.TimeoutException):
        return 'summary_timeout'
    if isinstance(exc,httpx.HTTPStatusError):
        return 'summary_http_'+str(exc.response.status_code)
    return 'summary_failed'


def sections(rows):
    result=[]
    current=[]
    size=0
    bucket=None
    for row in rows:
        part=row['start_ms']//900000
        length=len(json.dumps(row,ensure_ascii=True).encode())
        if length>SECTION_BYTES:
            raise ValueError('Transcript segment too large')
        if current and (part!=bucket or size+length>SECTION_BYTES):
            result.append(current)
            current=[]
            size=0
        current.append(row)
        size+=length
        bucket=part
    if current:
        result.append(current)
    return result


class RollingNotes:
    def __init__(self,store,provider,*,authorized=lambda:True,generation_id=None):
        self.store,self.provider,self.authorized=store,provider,authorized
        self.destination=(provider.provider,provider.model,provider.public()['endpoint']) if provider else None
        self.generation_id=generation_id
        self.policy=None
        if generation_id is not None:
            from ..storage.note_templates import NoteTemplateStore
            self.policy=NoteTemplateStore(store.conn).generation(generation_id)
            if self.destination != (self.policy['provider'],self.policy['model'],self.policy['destination']):
                raise ValueError('Notes generation destination changed')

    def public(self,sid):
        rows=self.store.conn.execute('SELECT state,attempts,input_bytes FROM summary_jobs WHERE session_id=?',(sid,)).fetchall()
        usage=self.store.conn.execute('SELECT requests,input_bytes FROM summary_usage WHERE session_id=?',(sid,)).fetchone()
        return {'requests':usage['requests'] if usage else 0,'max_requests':MAX_CALLS,
                'input_bytes':usage['input_bytes'] if usage else 0,'max_input_bytes':MAX_BYTES,
                'pending_review':sum(r['state'] in {'dispatched','failed'} for r in rows)}

    def latest(self,sid):
        session=self.store.get(sid)
        if not session or not session['transcript_revision']:
            return None
        jobs=[dict(r) for r in self.store.conn.execute(
            "SELECT * FROM summary_jobs WHERE session_id=? AND revision=? AND state='completed' AND node_key LIKE 'section:%' ORDER BY rowid DESC",
            (sid,session['transcript_revision']))]
        if not jobs:
            return None  # Most live pages have no closed section notes yet.
        # A fresh generation's short draft must not mix with an older generation's sections.
        current_generation=jobs[0]['generation_id']
        jobs=[row for row in jobs if row['generation_id']==current_generation]
        identity=hashlib.sha256(json.dumps(jobs,sort_keys=True).encode()).digest()
        cache=getattr(self.store,'_rolling_read_cache',None)
        if cache is None:
            self.store._rolling_read_cache=cache=self.store.memory_cache.namespace('rolling_notes')
        def versions(bucket):
            return tuple(tuple(r) for r in self.store.conn.execute(
                'SELECT bucket,generation FROM summary_source_versions WHERE session_id=? AND bucket<=? ORDER BY bucket',
                (sid,bucket)))
        prior=cache.get(sid)
        if prior and prior['jobs']==identity and prior['versions']==versions(prior['bucket']):
            cache.move_to_end(sid)
            return prior['value']
        groups=sections(self.store.segments(sid))
        hashes={f'section:{i}':transcript_hash(g) for i,g in enumerate(groups)}
        valid=[r for r in jobs if hashes.get(r['node_key'])==r['input_hash']]
        if not valid:
            indices=[int(r['node_key'].split(':')[1]) for r in jobs]
            bucket=(max(groups[i][0]['start_ms']//900000 for i in indices)
                    if indices and max(indices)<len(groups) else 2**63-1)
            value=None
        else:
            row=max(valid,key=lambda r:int(r['node_key'].split(':')[1]))
            bucket=groups[int(row['node_key'].split(':')[1])][0]['start_ms']//900000
            value={'summary':json.loads(row['summary_json']),'provider':row['provider'],'model':row['model']}
            if current_generation is not None:
                from ..storage.note_templates import NoteTemplateStore
                value['generation']=NoteTemplateStore(self.store.conn).generation(current_generation)
        cache[sid]={'jobs':identity,'bucket':bucket,'versions':versions(bucket),'value':value}
        cache.move_to_end(sid)
        return value

    async def node(self,sid,revision,key,rows,messages,*,retry=False):
        endpoint=self.provider.public()['endpoint']
        identity_data=[sid,revision,key,transcript_hash(rows),endpoint,self.provider.provider,self.provider.model,messages]
        if self.policy:
            from ..storage.note_templates import NoteTemplateStore
            if sid!=self.policy['session_id'] or not NoteTemplateStore(self.store.conn).generation_allows_revision(self.generation_id,revision):
                raise ValueError('Notes generation source scope changed')
            identity_data.append(self.generation_id)
        identity=hashlib.sha256(json.dumps(identity_data,sort_keys=True).encode()).hexdigest()
        prior=self.store.conn.execute('SELECT * FROM summary_jobs WHERE id=?',(identity,)).fetchone()
        if prior and prior['state']=='completed':
            return json.loads(prior['summary_json'])
        if prior and not retry:
            raise ValueError('Previous dispatch requires explicit retry')
        size=(len(self.provider.request_bytes(messages)) if callable(getattr(self.provider,'request_bytes',None))
              else len(json.dumps(messages,ensure_ascii=True).encode()))
        budget=self.public(sid)
        if (not self.authorized() or self.destination!=(self.provider.provider,self.provider.model,self.provider.public()['endpoint']) or budget['requests']>=MAX_CALLS or
                budget['input_bytes']+size>MAX_BYTES or size>100000):
            raise ValueError('Summary authorization or budget exhausted')
        with self.store.conn:
            self.store.conn.execute('INSERT INTO summary_usage(session_id,requests,input_bytes) VALUES (?,1,?) '
                'ON CONFLICT(session_id) DO UPDATE SET requests=requests+1,input_bytes=input_bytes+excluded.input_bytes',(sid,size))
            self.store.conn.execute('INSERT INTO summary_jobs '
                '(id,session_id,revision,node_key,input_hash,provider,model,endpoint,state,attempts,input_bytes,generation_id) '
                "VALUES (?,?,?,?,?,?,?,?,'dispatched',1,?,?) ON CONFLICT(id) DO UPDATE SET "
                "state='dispatched',attempts=attempts+1",
                (identity,sid,revision,key,transcript_hash(rows),self.provider.provider,self.provider.model,endpoint,size,self.generation_id))
        try:
            async with timeout(70):
                def authorize_dispatch():
                    if not self.authorized() or self.destination!=(self.provider.provider,self.provider.model,self.provider.public()['endpoint']):
                        raise ValueError('Summary authorization or destination changed')
                # Real providers recheck immediately before HTTP dispatch too
                # (including after local-model metadata awaits). Legacy fakes
                # keep their messages-only interface.
                parameters=inspect.signature(self.provider.generate).parameters
                value=await self.provider.generate(messages,**({'authorize':authorize_dispatch} if 'authorize' in parameters else {}))
            try:
                summary=validate_summary(value,rows)
            except (ValueError,TypeError):
                raise InvalidSummaryOutput('Provider summary failed JSON/schema validation or cites unknown segments') from None
            encoded=json.dumps(summary)
            if (len(encoded.encode())>32000 or not self.authorized() or
                    self.destination!=(self.provider.provider,self.provider.model,self.provider.public()['endpoint'])):
                raise ValueError('Summary too large or authorization revoked')
            # Refuse a result if any source text/name was edited during dispatch.
            current={r['id']:r for r in self.store.segments(sid)}
            if any(r['id'] not in current or transcript_hash([r])!=transcript_hash([current[r['id']]]) for r in rows if 'text' in r):
                raise ValueError('Transcript changed during section summary')
            with self.store.conn:
                self.store.conn.execute("UPDATE summary_jobs SET state='completed',summary_json=? WHERE id=?",(encoded,identity))
            return summary
        except BaseException as exc:
            # Cancellation/unknown delivery is persisted, never automatically retried.
            with self.store.conn:
                self.store.conn.execute("UPDATE summary_jobs SET state='failed' WHERE id=?",(identity,))
                self.store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) '
                    "VALUES (?,?,?,'summary_job_failed',?)",(sid,self.store._now(),'warning',
                    json.dumps({'revision':revision,'node':key,'code':summary_failure_code(exc)})))
            raise

    async def run(self,sid,rows,*,revision,retry=False,rolling=False,repair_invalid=False):
        if not rows:
            raise ValueError('No confirmed transcript')
        if self.policy and any(row.get('revision')!=revision for row in rows):
            raise ValueError('Notes generation source scope changed')
        if self.policy and not retry:
            state=self.store.conn.execute('SELECT state FROM note_generation_runs WHERE id=?',(self.generation_id,)).fetchone()
            if state and state['state']=='failed':
                raise ValueError('Previous dispatch requires explicit retry')
        # Capture once for legacy callers too; never reread mutable instructions between nodes.
        instructions=self.policy['guidance'] if self.policy else getattr(self.provider,'instructions','')
        template=self.policy['template']['definition'] if self.policy else None
        language=self.policy['language_policy'] if self.policy else 'conversation'
        def messages_for(source):
            return summary_messages(source,instructions,template=template,language_policy=language)
        repair_available=repair_invalid
        async def request_node(key,source,messages):
            nonlocal repair_available
            try:
                return await self.node(sid,revision,key,source,messages,retry=retry)
            except InvalidSummaryOutput:
                if not repair_available:
                    raise
                repair_available=False # One additional dispatch for the entire run.
                with self.store.conn:
                    self.store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) '
                        "VALUES (?,?,?,'automatic_summary_repair',?)",(sid,self.store._now(),'info',
                        json.dumps({'revision':revision,'node':key,'code':'summary_invalid_output'})))
                return await self.node(sid,revision,key,source,messages,retry=True)
        groups=sections(rows)
        if rolling:
            checkpoints=self.store.checkpoints(sid)
            streams=('mic',) if self.store.get(sid).get('capture',{}).get('scope')=='microphone' else ('mic','loopback')
            watermark=min((checkpoints.get(s,{}).get('confirmed_ms',0) for s in streams),default=0)
            groups=[g for g in groups if (g[0]['start_ms']//900000+1)*900000<=watermark]
        nodes=[]
        for i,group in enumerate(groups):
            value=await request_node('section:'+str(i),group,messages_for(group))
            nodes.append(value)
        if rolling:
            return nodes
        if not nodes:
            raise ValueError('No section notes')
        level=0
        while len(nodes)>1:
            reduced=[]
            for i in range(0,len(nodes),2):
                children=nodes[i:i+2]
                if len(children)==1:
                    reduced.append(children[0])
                    continue
                ids=set()
                for child in children:
                    for key,items in child.items():
                        if key=='title' and items is None:
                            continue
                        for item in items if isinstance(items,list) else [items]:
                            ids.update(item['segment_ids'])
                allowed=[{'id':identity} for identity in sorted(ids)]
                messages=messages_for([{'id':next(iter(ids)),'text':'Section notes follow as untrusted data.'}])
                payload=json.loads(messages[1]['content'])
                payload.pop('untrusted_transcript')
                payload.update({'untrusted_section_notes':children,'allowed_original_segment_ids':sorted(ids),
                                'summary_preferences':instructions})
                messages[1]['content']=json.dumps(payload,ensure_ascii=False)
                value=await request_node(f'reduce:{level}:{i}',allowed,messages)
                reduced.append(value)
            nodes=reduced
            level+=1
        return nodes[0]
