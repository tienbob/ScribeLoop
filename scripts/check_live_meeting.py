"""Paced encrypted live-path check on an explicitly supplied public WAV.

No device opens, downloads, native keys, plaintext output, or saved transcript.
The WAV is duplicated to two test streams, not an own-voice attribution test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import resource
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path

import soundfile as sf
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.recording import RecordingRunner
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


class EphemeralKeys:
    def __init__(self):
        self.rows = {}
    def get(self, key):
        return self.rows.get(key)
    def create(self, key):
        self.rows[key] = secrets.token_bytes(32)
        return self.rows[key]
    def delete(self, key):
        self.rows.pop(key,None)


class PublicSource:
    def __init__(self, pcm):
        self.pcm = pcm
        self.stopped = threading.Event()
    def frames(self):
        started = time.monotonic()
        for start in range(0,len(self.pcm),1600):
            delay = start/16000-(time.monotonic()-started)
            if self.stopped.wait(max(0,delay)):
                break
            for stream,offset in (('loopback',0),('mic',150)):
                yield AudioFrame(self.pcm[start:start+1600].copy(),stream,start//16+offset,0)
    def request_stop(self):
        self.stopped.set()
    def close(self):
        self.stopped.set()


async def check(args):
    pcm,sr = sf.read(args.audio,dtype='float32',always_2d=True)
    if sr != 16000 or not 0 < args.seconds <= 300 or args.start_s < 0:
        raise ValueError('Use 16 kHz public audio, positive duration <=300 s, and nonnegative start')
    pcm = pcm[round(args.start_s*sr):round((args.start_s+args.seconds)*sr)].mean(axis=1)
    if len(pcm) < args.seconds*sr:
        raise ValueError('Public WAV is shorter than the requested excerpt')
    started = time.monotonic()
    events = []
    times = {}
    partial_delays, confirmed_delays = [], []
    async def publish(event):
        kind = event['type']
        events.append(event)
        if kind == 'partial' and event['segment']['text']:
            times.setdefault('first_partial_s',time.monotonic()-started)
            if 'end_ms' in event['segment']:
                partial_delays.append(max(0,time.monotonic()-started-event['segment']['end_ms']/1000))
        if kind == 'segment':
            times.setdefault('first_confirmed_s',time.monotonic()-started)
            confirmed_delays.append(max(0,time.monotonic()-started-event['segment']['end_ms']/1000))
        if kind == 'session':
            status = event['session']['status']
            times.setdefault(status+'_s',time.monotonic()-started)
            print('state:',status,flush=True)
        if kind in {'recording_error','transcription_error','speaker_warning'}:
            print(kind+': '+event['message'],flush=True)
    with tempfile.TemporaryDirectory(prefix='scribeloop-live-public-') as temp:
        conn = migrate(connect(':memory:'))
        store = SessionStore(conn)
        audio = SessionAudioStore(Path(temp),EphemeralKeys())
        runner = RecordingRunner(store,audio,lambda *a,**kw:PublicSource(pcm),publish,
                                 diarizer=False if args.no_speakers else None)
        session = runner.start(None,{'language':args.language},'Public paced test',consent_confirmed=True)
        sid = session['id']
        try:
            await asyncio.wait_for(runner.task,timeout=args.seconds+600)
            result = store.get(sid)
            segments = store.segments(sid)
            turns = store.turns(sid)
            report = {'mode':'paced public WAV duplicated to two encrypted test tracks; no devices',
                'audio_s':args.seconds,'status':result['status'], 'language':store.language_lock(sid),
                'times':times,'wall_s':time.monotonic()-started,
                'confirmed_before_capture_stopped':times.get('first_confirmed_s',float('inf')) < times.get('processing_s',0),
                'segments_by_stream':{s:sum(r['stream']==s for r in segments) for s in ('loopback','mic')},
                'speaker_turns':len(turns),'speaker_labels':len({t['label'] for t in turns if t['label']}),
                'checkpoints':{s:{k:v for k,v in c.items() if k not in {'session_id','stream'}}
                               for s,c in store.checkpoints(sid).items()},
                'errors':[e['type'] for e in events if e['type'] in {'recording_error','transcription_error','speaker_warning'}],
                'transcript_saved':False,'physical_alignment_validated':False}
            progress = [e for e in events if e['type']=='transcription_progress']
            if progress:
                report['inference_s'] = progress[-1]['inference_s']
                report['decodes'] = progress[-1]['decodes']
                report['model_metrics'] = progress[-1].get('model_metrics',{})
            for name,values in (('partial_delay_s',partial_delays),('confirmed_delay_s',confirmed_delays)):
                if values:
                    values.sort()
                    report[name] = {'median':values[len(values)//2],'p95':values[min(len(values)-1,int(len(values)*.95))]}
            if args.reference_words:
                from scribeloop_core.evaluation.metrics import score_words
                words = json.loads(args.reference_words.read_text())
                start = args.reference_start_s
                reference = ' '.join(w['text'] for w in words if start <= w['start'] < start+args.seconds)
                hypothesis = ' '.join(r['text'] for r in segments if r['stream']=='loopback')
                score = score_words(reference,hypothesis)
                report['wer'] = score.wer
                live_hypothesis = ' '.join(e['segment']['text'] for e in events
                    if e['type']=='segment' and e['segment']['stream']=='loopback')
                report['live_wer'] = score_words(reference,live_hypothesis).wer
                report['reference_words'] = score.reference_words
            if args.reference_turns and turns:
                from pyannote.core import Annotation, Segment
                from scribeloop_core.evaluation.diarization import evaluate
                reference, hypothesis = Annotation(), Annotation()
                start_ms = round(args.reference_start_s*1000)
                for i,turn in enumerate(json.loads(args.reference_turns.read_text())):
                    a,b = max(0,turn['start_ms']-start_ms),min(args.seconds*1000,turn['end_ms']-start_ms)
                    if b>a:
                        reference[Segment(a/1000,b/1000),i] = turn['speaker']
                for i,turn in enumerate(turns):
                    hypothesis[Segment(turn['start_ms']/1000,turn['end_ms']/1000),i] = turn['label'] or f'unknown-{i}'
                score,_,_ = evaluate(reference,hypothesis,args.seconds)
                report['diagnostic_diarization'] = score
            final_metrics = [e['metrics'] for e in events if e['type']=='model_metrics']
            if final_metrics:
                report['final_model_metrics'] = final_metrics[-1]
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            report['model_process_cpu_s'] = usage.ru_utime+usage.ru_stime
            report['largest_model_process_peak_rss_mb'] = usage.ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
            report['limitations'] = ['Public mixed-headset fixture, not hardware or Vietnamese acceptance.',
                'RSS is largest individual child, not simultaneous total or GPU memory.',
                'Short-run WER/DER are diagnostic; not release-target sign-off.']
        finally:
            await runner.close()
            conn.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2,allow_nan=False),flush=True)
    if report['status'] != 'completed' or report['errors']:
        raise SystemExit('Live-path check did not pass; inspect aggregate report')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=20)
    parser.add_argument('--start-s',type=float,default=0)
    parser.add_argument('--language',choices=['auto','en','vi'],default='en')
    parser.add_argument('--no-speakers',action='store_true')
    parser.add_argument('--reference-words',type=Path)
    parser.add_argument('--reference-turns',type=Path)
    parser.add_argument('--reference-start-s',type=float,default=0)
    parser.add_argument('--output',type=Path,default=Path('data/phase1/live-public-check.json'))
    asyncio.run(check(parser.parse_args()))


if __name__ == '__main__':
    main()
