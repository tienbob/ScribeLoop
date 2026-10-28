"""Silent generated-audio cache benchmark; no devices, providers or user data."""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import tempfile
import time
from pathlib import Path

import numpy as np
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted
from scribeloop_core.storage.memory_cache import DEFAULT_CACHE_BYTES, MemoryCache
from scribeloop_core.storage.retained_audio import RetainedAudio
from scribeloop_core.storage.sessions import SessionStore


class Keys:
    def __init__(self): self.values={}
    def get(self,key): return self.values.get(key)
    def create(self,key): self.values[key]=secrets.token_bytes(32); return self.values[key]


async def benchmark(root):
    conn=migrate(connect_encrypted(root/'fixture.sqlcipher',secrets.token_bytes(32)))
    audio=SessionAudioStore(root/'audio',Keys())
    store=SessionStore(conn)
    sid=store.create_recording('Synthetic cache benchmark',{'scope':'application'})['id']
    audio.create_session(sid)
    store.set_audio_paths(sid,audio)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            for offset in range(0,120*16000,171):
                count=min(171,120*16000-offset)
                writer.write(AudioFrame(np.full(count,.1,dtype='float32'),stream,round(offset/16),0))
    store.transition(sid,'completed')
    results=[]
    original=audio.cursor
    for budget in (0,DEFAULT_CACHE_BYTES):
        cache=MemoryCache(budget)
        reader=RetainedAudio(SessionStore(conn,memory_cache=cache),audio)
        opened=[]
        def cursor(*args,opened=opened,**kwargs):
            opened.append(1)
            return original(*args,**kwargs)
        audio.cursor=cursor
        started=time.perf_counter()
        await reader.seek(sid,'conversation',0)
        cold=(time.perf_counter()-started)*1000
        await reader.seek(sid,'conversation',60000)
        before=len(opened)
        timings=[]
        for _ in range(5):
            started=time.perf_counter()
            await reader.seek(sid,'conversation',30000)
            timings.append((time.perf_counter()-started)*1000)
        results.append({'budget_bytes':budget,'first_seek_ms':round(cold,3),
            'overlapping_seek_median_ms':round(float(np.median(timings)),3),
            'cursor_opens_for_five_warm_seeks':len(opened)-before,'cache':cache.stats()})
        cache.clear()
    audio.cursor=original
    conn.close()
    assert results[1]['cursor_opens_for_five_warm_seeks']==0
    assert results[1]['cache']['used_bytes']<=DEFAULT_CACHE_BYTES
    return {'mode':'generated encrypted audio; WAV bytes created in memory and discarded; no sound output',
        'audio_s_per_track':120,'tracks':2,'results':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='scribeloop-cache-bench-') as folder:
        report=asyncio.run(benchmark(Path(folder)))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
