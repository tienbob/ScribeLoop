"""Actual Groq-backed meeting coordinator on paced public VIVOS clips only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import resource
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.cloud_meeting import CloudRecordingRunner
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore

ROOT=Path('data/phase1/groq-vietnamese-inspection')


class Keys:
    def __init__(self):
        self.rows={}
    def create(self,key):
        self.rows[key]=secrets.token_bytes(32)
    def get(self,key):
        return self.rows.get(key)
    def delete(self,key):
        del self.rows[key]


class PacedPublic:
    def __init__(self,pcm):
        self.pcm=pcm
        self.stopped=threading.Event()
    def frames(self):
        began=time.monotonic()
        for start in range(0,len(self.pcm),1600):
            if self.stopped.wait(max(0,start/16000-(time.monotonic()-began))):
                return
            for stream in ('loopback','mic'):
                yield AudioFrame(self.pcm[start:start+1600].copy(),stream,start//16,0)
    def request_stop(self):
        self.stopped.set()
    def close(self):
        self.stopped.set()


async def check(args):
    manifest=json.loads((ROOT/'samples.json').read_text())
    parts=[]
    for sample in manifest['samples']:
        path=ROOT/'audio'/sample['filename']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sample['sha256']:
            raise ValueError('Pinned public audio changed')
        pcm,sr=sf.read(path,dtype='float32')
        if sr!=16000:
            raise ValueError('Expected 16 kHz public audio')
        parts.extend((pcm,np.zeros(4000,dtype=np.float32)))
    pcm=np.concatenate(parts)
    pcm=np.pad(pcm,(0,max(0,30*16000-len(pcm))))
    if len(pcm)>30*16000:
        raise ValueError('Public fixture exceeds thirty seconds')
    events,times=[],{}
    began=time.monotonic()
    async def publish(event):
        events.append(event)
        elapsed=time.monotonic()-began
        if event['type']=='segment':
            times.setdefault('first_text_s',elapsed)
        if event['type']=='session':
            status=event['session']['status']
            times.setdefault(status+'_s',elapsed)
            print('state:',status,flush=True)
        if event['type'] in {'transcription_error','recording_error'}:
            print(event['message'],flush=True)
    with tempfile.TemporaryDirectory(prefix='scribeloop-cloud-public-') as temp:
        conn=migrate(connect(':memory:'))
        store=SessionStore(conn)
        audio=SessionAudioStore(Path(temp),Keys())
        runner=CloudRecordingRunner(store,audio,lambda *a,**kw:PacedPublic(pcm),publish)
        sid=runner.start(None,{'cloud_consent':True,'language':'auto'},'Public cloud validation',
                         consent_confirmed=True)['id']
        try:
            await asyncio.wait_for(runner.task,150)
            session=store.get(sid)
            rows=store.segments(sid)
            report={'source':'five pinned VIVOS test clips with silence padding, duplicated test tracks',
                'public_only':True,'audio_s':30,'status':session['status'],'times':times,
                'text_before_capture_stopped':times.get('first_text_s',150)<times.get('processing_s',0),
                'language_lock':store.language_lock(sid),'usage':store.cloud_usage(sid),
                'segments_by_stream':{s:sum(r['stream']==s for r in rows) for s in ('mic','loopback')},
                'checkpoints':{s:{k:v for k,v in row.items() if k not in {'session_id','stream'}}
                               for s,row in store.checkpoints(sid).items()},
                'errors':[e['type'] for e in events if e['type'] in {'recording_error','transcription_error'}],
                'peak_client_rss_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024),
                'local_models_loaded':any(name in sys.modules for name in ('torch','faster_whisper','pyannote.audio')),
                'limitations':['No devices or private meetings opened.','Duplicated test tracks are not own-voice or speaker validation.']}
        finally:
            await runner.close()
            conn.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2,allow_nan=False),flush=True)
    if report['status']!='completed' or report['errors'] or report['local_models_loaded']:
        raise SystemExit('Cloud coordinator validation did not pass')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consent-public-upload',action='store_true',required=True)
    parser.add_argument('--output',type=Path,default=Path('data/phase1/cloud-coordinator-check.json'))
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Report exists; choose a new output to avoid duplicate API calls')
    asyncio.run(check(args))


if __name__=='__main__':
    main()
