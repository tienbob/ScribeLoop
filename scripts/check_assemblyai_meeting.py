"""Bounded real AssemblyAI smoke test on pinned public AMI audio only."""
from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from inspect_deepgram_streaming import prepare
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.evaluation.metrics import score_words
from scribeloop_core.pipeline import assemblyai_meeting
from scribeloop_core.pipeline.assemblyai_meeting import AssemblyAIConfig
from scribeloop_core.pipeline.deepgram_meeting import DeepgramError
from scribeloop_core.pipeline.model_selection import TranscriptionRouter
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from websockets.sync.client import connect as socket_connect


class Keys:
    def __init__(self):
        self.values = {}
    def create(self,key):
        self.values[key] = secrets.token_bytes(32)
    def get(self,key):
        return self.values.get(key)
    def delete(self,key):
        self.values.pop(key,None)


class PublicSource:
    def __init__(self,pcm):
        self.pcm = pcm
        self.stop = threading.Event()
    def frames(self):
        began = time.monotonic()
        for offset in range(0,len(self.pcm),1600):
            if self.stop.wait(max(0,offset/16000-(time.monotonic()-began))):
                return
            yield AudioFrame(self.pcm[offset:offset+1600].copy(),'mic',offset//16,0)
        self.stop.wait(max(0,len(self.pcm)/16000-(time.monotonic()-began)))
    def request_stop(self):
        self.stop.set()
    def close(self):
        self.stop.set()


async def main():
    original_track = assemblyai_meeting._track
    def observed_track(*args,**kwargs):
        try:
            return original_track(*args,**kwargs)
        except DeepgramError as exc:
            print('Adapter diagnostic:',str(exc),flush=True)
            raise
    assemblyai_meeting._track = observed_track
    reference,pcm,digest,_ = prepare()
    settings = DesktopSettings()
    key = settings.value.assemblyai_key
    if not key:
        raise SystemExit('Save an AssemblyAI key in the app first')
    def connector(*args,**kwargs):
        class Observed:
            def __enter__(self):
                self.context = socket_connect(*args,**kwargs)
                self.ws = self.context.__enter__()
                return self
            def __exit__(self,*exc):
                self.context.__exit__(*exc)
            @property
            def socket(self):
                return self.ws.socket
            def send(self,value):
                self.ws.send(value)
            def recv(self,**kwargs):
                raw = self.ws.recv(**kwargs)
                event = json.loads(raw)
                if event.get('type') == 'Turn' and event.get('end_of_turn'):
                    print('Final speaker labels:',repr(event.get('speaker_label')),repr(sorted({str(w.get('speaker')) for w in event.get('words',[])})),flush=True)
                if event.get('type') == 'Error' or event.get('error'):
                    print('Provider diagnostic:',json.dumps({k:event[k] for k in ('type','error','code','message') if k in event}),flush=True)
                return raw
        return Observed()
    config = AssemblyAIConfig(api_key=key,connector=connector,budget_usd=.10,max_requests=12)
    del key,settings
    events,times = [],{}
    began = time.monotonic()
    async def publish(event):
        events.append(event['type'])
        if event['type'] == 'segment':
            times.setdefault('first_text_s',round(time.monotonic()-began,3))
        if event['type'] == 'session':
            times.setdefault(event['session']['status']+'_s',round(time.monotonic()-began,3))
    with tempfile.TemporaryDirectory(prefix='scribeloop-assemblyai-public-') as temp:
        conn = migrate(connect(':memory:'))
        store = SessionStore(conn)
        audio = SessionAudioStore(Path(temp),Keys())
        runner = TranscriptionRouter(store,audio,lambda *a,**k:PublicSource(pcm),publish,cloud_config=config)
        sid = runner.start(SimpleNamespace(scope='microphone'),{'cloud_consent':True,'language':'en','duration_s':300},
            'Public AssemblyAI smoke test',consent_confirmed=True)['id']
        try:
            await asyncio.wait_for(runner.task,90)
            live = store.get(sid)
            rows = store.segments(sid)
            live_text = ' '.join(r['text'] for r in rows)
            report = {'public_only':True,'source_sha256':digest,'audio_seconds':len(pcm)/16000,
                'reference':reference,'live':{'status':live['status'],'times':times,
                'transcript':live_text,'wer':score_words(reference,live_text).wer,
                'speakers':len({r['local_speaker_id'] for r in rows if r['local_speaker_id']}),
                'revision':store.revisions(sid),'failure':live.get('failure')},
                'events':events.copy()}
            if live['status'] == 'completed':
                start = time.monotonic()
                runner.reprocess(sid,language='en',cloud_consent=True)
                await asyncio.wait_for(runner.task,150)
                batch_text = ' '.join(r['text'] for r in store.segments(sid))
                report['batch'] = {'status':store.get(sid)['status'],
                    'elapsed_s':round(time.monotonic()-start,3),'transcript':batch_text,
                    'wer':score_words(reference,batch_text).wer,'revision':store.revisions(sid)}
            report['usage'] = store.cloud_usage(sid)
            output = Path('data/phase1/assemblyai-inspection/results.json')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str)+'\n')
            print(json.dumps(report,indent=2,ensure_ascii=False,default=str),flush=True)
        finally:
            await runner.close()
            conn.close()


if __name__ == '__main__':
    asyncio.run(main())
