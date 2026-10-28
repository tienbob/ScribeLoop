"""Durable bounded map/reduce notes; dispatched work is never silently retried."""
from __future__ import annotations

import asyncio
import hashlib
import json

from .grounded import summary_messages, transcript_hash, validate_summary

MAX_CALLS=128
MAX_BYTES=8_000_000
SECTION_BYTES=48_000


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
    def __init__(self,store,provider,*,authorized=lambda:True):
        self.store,self.provider,self.authorized=store,provider,authorized
        self.destination=(provider.provider,provider.model,provider.public()['endpoint']) if provider else None

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
        cache[sid]={'jobs':identity,'bucket':bucket,'versions':versions(bucket),'value':value}
        cache.move_to_end(sid)
        return value

    async def node(self,sid,revision,key,rows,messages,*,retry=False):
        endpoint=self.provider.public()['endpoint']
        identity=hashlib.sha256(json.dumps([sid,revision,key,transcript_hash(rows),endpoint,
            self.provider.provider,self.provider.model,messages],sort_keys=True).encode()).hexdigest()
        prior=self.store.conn.execute('SELECT * FROM summary_jobs WHERE id=?',(identity,)).fetchone()
        if prior and prior['state']=='completed':
            return json.loads(prior['summary_json'])
        if prior and not retry:
            raise ValueError('Previous dispatch requires explicit retry')
        size=len(json.dumps(messages,ensure_ascii=True).encode())
        budget=self.public(sid)
        if (not self.authorized() or self.destination!=(self.provider.provider,self.provider.model,self.provider.public()['endpoint']) or budget['requests']>=MAX_CALLS or
                budget['input_bytes']+size>MAX_BYTES or size>100000):
            raise ValueError('Summary authorization or budget exhausted')
        with self.store.conn:
            self.store.conn.execute('INSERT INTO summary_usage(session_id,requests,input_bytes) VALUES (?,1,?) '
                'ON CONFLICT(session_id) DO UPDATE SET requests=requests+1,input_bytes=input_bytes+excluded.input_bytes',(sid,size))
            self.store.conn.execute('INSERT INTO summary_jobs '
                '(id,session_id,revision,node_key,input_hash,provider,model,endpoint,state,attempts,input_bytes) '
                "VALUES (?,?,?,?,?,?,?,?,'dispatched',1,?) ON CONFLICT(id) DO UPDATE SET "
                "state='dispatched',attempts=attempts+1",
                (identity,sid,revision,key,transcript_hash(rows),self.provider.provider,self.provider.model,endpoint,size))
        try:
            async with asyncio.timeout(70):
                value=await self.provider.generate(messages)
            summary=validate_summary(value,rows)
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
        except BaseException:
            # Cancellation/unknown delivery is persisted, never automatically retried.
            with self.store.conn:
                self.store.conn.execute("UPDATE summary_jobs SET state='failed' WHERE id=?",(identity,))
            raise

    async def run(self,sid,rows,*,revision,retry=False,rolling=False):
        if not rows:
            raise ValueError('No confirmed transcript')
        groups=sections(rows)
        if rolling:
            checkpoints=self.store.checkpoints(sid)
            streams=('mic',) if self.store.get(sid).get('capture',{}).get('scope')=='microphone' else ('mic','loopback')
            watermark=min((checkpoints.get(s,{}).get('confirmed_ms',0) for s in streams),default=0)
            groups=[g for g in groups if (g[0]['start_ms']//900000+1)*900000<=watermark]
        nodes=[]
        for i,group in enumerate(groups):
            value=await self.node(sid,revision,'section:'+str(i),group,summary_messages(group,getattr(self.provider,'instructions','')),retry=retry)
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
                messages=summary_messages([{'id':next(iter(ids)),'text':'Section notes follow as untrusted data.'}],getattr(self.provider,'instructions',''))
                messages[1]['content']=json.dumps({'untrusted_section_notes':children,'allowed_original_segment_ids':sorted(ids),
                    'summary_preferences':getattr(self.provider,'instructions','')})
                value=await self.node(sid,revision,f'reduce:{level}:{i}',allowed,messages,retry=retry)
                reduced.append(value)
            nodes=reduced
            level+=1
        return nodes[0]
