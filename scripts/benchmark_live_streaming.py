"""One live, paced production streaming trial of pinned public AMI speech.

No device capture/playback, private workspace, summaries or automatic retries.
"""
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
from types import SimpleNamespace

import numpy as np
import soundfile as sf
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.evaluation.metrics import score_words
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig, DeepgramRecordingRunner
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted
from scribeloop_core.storage.sessions import SessionStore
from websockets.sync.client import connect

FIXTURE = Path('data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav')
DIGEST = 'f4e64336510ce0076a8e313527d48737d0e952d3a262589823bac14c7acb922f'
CONFIG = Path('dist/macos/ScribeLoop.app/Contents/Resources/transcription.json')


class Keys:
    def __init__(self): self.values = {}
    def get(self, account): return self.values.get(account)
    def create(self, account):
        if account in self.values: raise ValueError('Key already exists')
        self.values[account] = secrets.token_bytes(32)
        return self.values[account]
    def delete(self, account): del self.values[account]


def distribution(values):
    return {name:round(float(np.percentile(values,q)),3) for name,q in [('p50',50),('p95',95),('max',100)]} if values else {}


def prepare(duration):
    hasher = hashlib.sha256()
    with FIXTURE.open('rb') as source:
        while chunk := source.read(1024*1024): hasher.update(chunk)
    if hasher.hexdigest() != DIGEST: raise ValueError('Public fixture hash mismatch')
    pcm, rate = sf.read(FIXTURE,start=0,stop=duration*16000,dtype='float32')
    if rate!=16000 or pcm.shape!=(duration*16000,) or not np.isfinite(pcm).all():
        raise ValueError('Invalid public fixture')
    words=json.loads(Path('data/corpus-v1/investigation/reference-words.json').read_text())
    words=sorted((w for w in words if 300<=w['start']<300+duration),key=lambda w:(w['start'],w['speaker']))
    reference=' '.join(w['text'] for w in words)
    key=json.loads(CONFIG.read_text())['api_key']
    if not isinstance(key,str) or not 16<=len(key)<=512 or any(c.isspace() for c in key):
        raise ValueError('Invalid bundled transcription credential')
    return pcm, reference, words[0]['start']-300, key


async def trial(pcm, reference, key, report):
    duration=len(pcm)/16000
    source_start = None
    send_times, send_lags, loop_lags = [], [], []
    first = {}
    connections = 0
    connections_lock = threading.Lock()
    provider = {}
    public_results = []
    public_result_bytes = 0
    stop = threading.Event()
    events = {}
    class Source:
        def frames(self):
            nonlocal source_start
            source_start=time.monotonic()
            for offset in range(0,len(pcm),171):
                delay=source_start+offset/16000-time.monotonic()
                if delay>0: stop.wait(delay)
                if stop.is_set(): break
                yield AudioFrame(pcm[offset:offset+171],'mic',round(offset/16),time.monotonic())
            stop.wait(max(0,source_start+duration-time.monotonic()))
        def request_stop(self): stop.set()
        def close(self): stop.set()
    class Socket:
        def __init__(self, ws):
            self.ws=ws
            self.sent_samples=0
        @property
        def socket(self): return self.ws.socket
        def __enter__(self): self.ws.__enter__(); return self
        def __exit__(self,*args): return self.ws.__exit__(*args)
        def send(self,value):
            start=time.monotonic()
            self.ws.send(value)
            send_times.append((time.monotonic()-start)*1000)
            if isinstance(value,bytes):
                self.sent_samples+=len(value)//2
                report['audio_sent_s']=self.sent_samples/16000
                send_lags.append(max(0.,time.monotonic()-source_start-self.sent_samples/16000)*1000)
            else:
                report['close_stream_after_capture_start_s']=time.monotonic()-source_start
        def recv(self,timeout):
            nonlocal public_result_bytes
            raw=self.ws.recv(timeout=timeout)
            event=json.loads(raw)
            elapsed=time.monotonic()-source_start
            if event.get('type')=='Results':
                alternative=event['channel']['alternatives'][0]
                selected={k:event.get(k) for k in ('type','start','duration','is_final','speech_final')}
                selected['alternative']={k:alternative.get(k) for k in ('transcript','words','languages')}
                size=len(json.dumps(selected).encode())
                if public_result_bytes+size<=2_000_000:
                    public_results.append(selected); public_result_bytes+=size
                if alternative.get('transcript','').strip():
                    first.setdefault('provider_first_text_s',elapsed)
                    first.setdefault('provider_first_final_s' if event.get('is_final') else 'provider_first_partial_s',elapsed)
                if event.get('is_final'):
                    provider['last_final_after_capture_start_s']=elapsed
            if event.get('type')=='Metadata':
                provider['acknowledged_audio_s']=event.get('duration')
                provider['metadata_after_capture_start_s']=elapsed
            return raw
    def connector(url,**kwargs):
        nonlocal connections
        with connections_lock:
            if connections: raise RuntimeError('Only one paid connection is allowed; no retry')
            connections+=1
            report['connection_attempts']=connections
        before=time.monotonic()
        ws=connect(url,**kwargs)
        report['handshake_s']=time.monotonic()-before
        report['successful_connections']=connections
        return Socket(ws)
    async def publish(event):
        kind=event['type']; events[kind]=events.get(kind,0)+1
        if kind=='partial' and event.get('segment',{}).get('text','').strip():
            first.setdefault('pipeline_first_partial_s',time.monotonic()-source_start)
        if kind=='segment': first.setdefault('pipeline_first_saved_segment_s',time.monotonic()-source_start)
    async def ticker():
        while True:
            before=time.monotonic()
            await asyncio.sleep(.01)
            loop_lags.append(max(0.,time.monotonic()-before-.01)*1000)
    with tempfile.TemporaryDirectory(prefix='scribeloop-public-live-') as folder:
        root=Path(folder)
        conn=migrate(connect_encrypted(root/'public.sqlcipher',secrets.token_bytes(32)))
        store=SessionStore(conn)
        audio=SessionAudioStore(root/'recordings',Keys())
        runner=DeepgramRecordingRunner(store,audio,lambda *a,**k:Source(),publish,
            config=DeepgramConfig(api_key=key,connector=connector,budget_usd=.03,max_requests=12),
            max_s=int(duration)+2)
        tick=asyncio.create_task(ticker())
        began=time.monotonic(); cpu=time.process_time()
        try:
            sid=runner.start(SimpleNamespace(scope='microphone'),{'scope':'microphone','cloud_consent':True,
                'language':'auto','microphone_processing':{'gain_db':0.,'noise_filter':'off'}},
                'Pinned public AMI streaming performance',consent_confirmed=True)['id']
            async with asyncio.timeout(duration+35): await runner.task
            rows=store.segments(sid)
            hypothesis=' '.join(r['text'] for r in rows)
            score=score_words(reference,hypothesis)
            report.update(status='completed' if store.get(sid)['status']=='completed' else 'failed',
                session_status=store.get(sid)['status'],connections=connections,
                pipeline_elapsed_s=time.monotonic()-began,process_cpu_s=time.process_time()-cpu,
                finish_after_capture_end_s=time.monotonic()-source_start-duration,
                first_events=first,provider=provider,send_call_ms=distribution(send_times),
                capture_clock_to_sent_end_lag_ms=distribution(send_lags),
                event_loop_extra_delay_ms=distribution(loop_lags),budget=store.cloud_usage(sid),
                segment_count=len(rows),anonymous_speakers=len({r['local_speaker_id'] for r in rows if r['local_speaker_id']}),
                wer=score.wer,word_errors=score.errors,reference_words=score.reference_words,
                checkpoints={s:{k:v for k,v in row.items() if k.endswith('_ms')} for s,row in store.checkpoints(sid).items()},
                events=events)
            return rows
        finally:
            report['public_provider_results']=public_results
            stop.set(); runner.cancel_processing.set(); runner.stop_requested.set()
            tick.cancel()
            await asyncio.gather(tick,return_exceptions=True)
            try: await runner.close()
            except asyncio.CancelledError: pass
            conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--duration-s',type=int,choices=(20,60),default=60)
    parser.add_argument('--consent-public-upload',action='store_true')
    parser.add_argument('--preflight',action='store_true')
    args=parser.parse_args()
    pcm,reference,speech_start,key=prepare(args.duration_s)
    report={'status':'preflight','source':'Pinned public AMI ES2004a mixed-headset, beginning at 300 s',
        'audio_s':args.duration_s,'first_reference_speech_s':speech_start,'source_sha256':DIGEST,
        'model':'nova-3','language':'auto/multi','packet_samples':171,
        'capture_devices_used':False,'speaker_output_used':False,'private_audio_uploaded':False,
        'summary_requests':0,'connections_max':1,'automatic_retries':0,'audio_sent_s':0}
    if args.preflight:
        print(json.dumps(report,indent=2)); return
    if not args.consent_public_upload: parser.error('Explicit public-upload authorization is required')
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'attempt.json').open('x') as attempt:
        json.dump({'max_connections':1,'max_audio_s':args.duration_s,'automatic_retries':0,
                   'reserved_at_unix':time.time()},attempt)
    report['status']='started'
    try:
        rows=asyncio.run(trial(pcm,reference,key,report))
        (args.output/'public-transcript.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
        first=report['first_events']
        if 'provider_first_text_s' in first:
            report['first_text_after_reference_speech_start_s']=first['provider_first_text_s']-speech_start
    except Exception as error:  # noqa: BLE001 - never print credentials, upstream bodies or exception text
        report.update(status='failed',failure_type=type(error).__name__,
            message='Streaming benchmark failed; no retry was made. Upstream details omitted.')
    usage=resource.getrusage(resource.RUSAGE_SELF)
    (args.output/'public-provider-results.json').write_text(json.dumps(report.pop('public_provider_results',[]),ensure_ascii=False,indent=2)+'\n')
    report['process_peak_rss_mib']=usage.ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
    (args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if report['status']!='completed': raise SystemExit(1)


if __name__=='__main__': main()
