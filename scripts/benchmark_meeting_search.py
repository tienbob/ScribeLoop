"""Reproducible encrypted F1 scale gate; synthetic text, no network or user data."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import secrets
import statistics
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted
from scribeloop_core.storage.search import MeetingSearch
from scribeloop_core.storage.sessions import SessionStore


async def benchmark(root, meetings=1000, segments=100, queries=100):
    conn=migrate(connect_encrypted(root/'synthetic.sqlcipher',secrets.token_bytes(32)))
    store=SessionStore(conn)
    try:
        for i in range(meetings):
            sid=store.create_recording(f'Synthetic project {i}',{})['id']
            with conn:
                conn.executemany('INSERT INTO segments(id,session_id,stream,start_ms,end_ms,text,source,revision) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    [(uuid4().hex,sid,'mic',j*1000,j*1000+900,
                      f'Project {i} discusses architecture budget delivery and milestone {j}.','stream',1)
                     for j in range(segments)])
                conn.execute("UPDATE sessions SET status='completed' WHERE id=?",(sid,))
        search=MeetingSearch(store)
        start=time.perf_counter();cold=await search.query_async('architecture');cold_ms=(time.perf_counter()-start)*1000
        times=[]
        for _ in range(queries):
            start=time.perf_counter();await search.query_async('architecture');times.append((time.perf_counter()-start)*1000)
        ordered=sorted(times);p95=ordered[max(0,math.ceil(.95*len(ordered))-1)]
        return {'fixture_version':1,'platform':platform.platform(),'machine':platform.machine(),
            'python':platform.python_version(),'storage':'real SQLCipher','mode':'keyword; no network or embeddings',
            'meetings':meetings,'segments':meetings*segments,'queries':queries,
            'documents':conn.execute('SELECT count(*) FROM search_documents').fetchone()[0],
            'cold_query_including_index_ms':round(cold_ms,3),'warm_p50_ms':round(statistics.median(times),3),
            'warm_p95_ms':round(p95,3),'warm_max_ms':round(max(times),3),
            'warm_times_ms':[round(x,3) for x in times],
            'gate_p95_under_500ms':p95<=500,'result_count':len(cold['results']),
            'limitations':'Single synthetic development-machine run; excludes native UI transport and cloud latency.'}
    finally:conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--meetings',type=int,default=1000)
    parser.add_argument('--segments',type=int,default=100)
    parser.add_argument('--queries',type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.meetings<=1000 or not 1<=args.segments<=100 or not 1<=args.queries<=100:
        parser.error('Use at most 1000 meetings, 100 segments and 100 queries')
    with tempfile.TemporaryDirectory(prefix='scribeloop-search-scale-') as folder:
        report=asyncio.run(benchmark(Path(folder),args.meetings,args.segments,args.queries))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='warm_times_ms'}))
    if not report['gate_p95_under_500ms']:raise SystemExit('Search p95 exceeded 500 ms')


if __name__=='__main__':main()
