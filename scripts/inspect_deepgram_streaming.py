"""One bounded, paced Deepgram public English trial; never retries or captures devices."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import html
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import soundfile as sf
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words
from websockets.asyncio.client import connect

SOURCE = Path('data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav')
OUTPUT = Path('data/phase1/deepgram-streaming-inspection')
CONFIG = {'model':'nova-3', 'language':'en', 'encoding':'linear16',
    'sample_rate':'16000', 'channels':'1', 'interim_results':'true',
    'punctuate':'true', 'endpointing':'300', 'diarize_model':'v1'}
ENDPOINT = 'wss://api.deepgram.com/v1/listen?' + urlencode(CONFIG)


def load_key():
    value = os.environ.get('DEEPGRAM_API_KEY','').strip()
    if not value and Path('.env').is_file():
        for line in Path('.env').read_text().splitlines():
            name,sep,candidate = line.strip().removeprefix('export ').partition('=')
            if sep and name.strip() == 'DEEPGRAM_API_KEY':
                value = candidate.strip()
                if len(value)>=2 and value[0]==value[-1] and value[0] in {'"',"'"}:
                    value=value[1:-1]
    if not 16<=len(value)<=512 or not value.isascii() or any(c.isspace() for c in value):
        raise ValueError('Configure DEEPGRAM_API_KEY locally; never paste it in chat')
    return value


def prepare():
    digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if digest!='f4e64336510ce0076a8e313527d48737d0e952d3a262589823bac14c7acb922f':
        raise ValueError('Pinned public AMI audio changed')
    pcm,sr=sf.read(SOURCE,start=0,stop=20*16000,dtype='float32')
    if sr!=16000 or pcm.shape!=(20*16000,) or not np.isfinite(pcm).all():
        raise ValueError('Expected twenty seconds of finite mono 16 kHz public audio')
    words=json.loads(Path('data/corpus-v1/investigation/reference-words.json').read_text())
    words=sorted((w for w in words if 300<=w['start']<320),key=lambda w:(w['start'],w['speaker']))
    reference=' '.join(w['text'] for w in words)
    return reference,pcm,digest,words[0]["start"]-300


async def trial(key,pcm,report):
    started=time.monotonic()
    report['events']=[]
    final=[]
    metadata=False
    async with connect(ENDPOINT,additional_headers={'Authorization':'Token '+key},
            open_timeout=10,close_timeout=2,max_size=1_000_000,proxy=None) as ws:
        report['connected_s']=time.monotonic()-started
        stream_start=time.monotonic()
        report['stream_started_s']=stream_start-started
        async def send():
            for offset in range(0,len(pcm),1600):
                await asyncio.sleep(max(0,stream_start+offset/16000-time.monotonic()))
                chunk=np.clip(pcm[offset:offset+1600]*32767,-32768,32767).astype('<i2')
                await ws.send(chunk.tobytes())
                report['audio_sent_s']=min(offset+1600,len(pcm))/16000
            await asyncio.sleep(max(0,stream_start+len(pcm)/16000-time.monotonic()))
            await ws.send(json.dumps({'type':'CloseStream'}))
            report['end_audio_sent_s']=time.monotonic()-started
        sender=asyncio.create_task(send())
        try:
            async for raw in ws:
                event=json.loads(raw)
                elapsed=time.monotonic()-started
                if event.get('type')=='Error':
                    raise RuntimeError('Provider rejected trial; no retry made')
                if event.get('type')=='Results':
                    alt=event['channel']['alternatives'][0]
                    text=alt.get('transcript','').strip()
                    is_final=event.get('is_final',False)
                    if text:
                        report.setdefault('first_text_s',elapsed)
                        report.setdefault('first_confirmed_s' if is_final else 'first_provisional_s',elapsed)
                        if is_final:
                            final.append(text)
                    report['events'].append({'received_s':elapsed,'start':event.get('start'),
                        'duration':event.get('duration'),'is_final':is_final,
                        'speech_final':event.get('speech_final'),'text':text,'words':alt.get('words',[])})
                elif event.get('type')=='Metadata':
                    metadata=True
                    report['provider_audio_s']=event.get('duration')
            await sender
            if not metadata or report.get('audio_sent_s')!=len(pcm)/16000:
                raise RuntimeError('Incomplete streaming response')
            report['finished_s']=time.monotonic()-started
            report['text']=' '.join(final)
            for field in ('first_text','first_provisional','first_confirmed'):
                if field+'_s' in report:
                    report[field+'_after_stream_s']=report[field+'_s']-report['stream_started_s']
        finally:
            sender.cancel()
            await asyncio.gather(sender,return_exceptions=True)


def render(report):
    esc=html.escape
    ref=report['reference']
    audio=base64.b64encode((OUTPUT/'public-english.wav').read_bytes()).decode()
    score=f'{report["wer"]:.2%}' if report.get('wer') is not None else 'Not scored'
    text=report.get('text','No completed transcript')
    details={k:v for k,v in report.items() if k not in {'events','final_tokens','reference','text','sources'}}
    (OUTPUT/'results.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Deepgram English streaming trial</title><style>body{font:17px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#18342b}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccd6cf;padding:16px;text-align:left;vertical-align:top}audio{width:250px}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{display:block}</style>
<h1>Deepgram English streaming trial</h1><p>One real-time paced connection. Public AMI meeting excerpt, seconds 300–320. English. Tiny sample; speaker and overlap accuracy are not validated.</p>
<table><tr><th>Sound file name</th><th>Transcription result</th><th>WER</th></tr><tr><td>public-english.wav<small>'''+esc(', '.join(report['sources']))+'''</small><audio controls src="data:audio/wav;base64,'''+audio+'''"></audio></td><td>'''+esc(text)+'''<details><summary>Reference</summary>'''+esc(ref)+'''</details></td><td>'''+score+'''</td></tr></table><h2>Measurements</h2><p>Times are seconds from connection attempt, including handshake. First text is not per-word latency. Provisional text may change; only final text is scored. The clip begins with 6.38 seconds before the first annotated reference word. First-text delay after that word is reported separately. WER uses the existing lowercase/punctuation normalization. References include overlapping speakers and disfluencies; this tiny excerpt is not a representative quality benchmark. Times after stream start exclude handshake.</p><pre>'''+esc(json.dumps(details,ensure_ascii=False,indent=2))+'''</pre></html>''')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consent-public-upload',action='store_true',required=True)
    parser.parse_args()
    key=load_key()
    reference,pcm,digest,speech_start=prepare()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    # Permanent local attempt reservation prevents accidental repeat usage even on failure.
    with (OUTPUT/'attempt.json').open('x') as handle:
        json.dump({'reserved_at_unix':time.time(),'max_connections':1,'max_audio_s':20,
            'max_wall_s':45,'automatic_retries':0},handle)
    sf.write(OUTPUT/'public-english.wav',pcm,16000,subtype='PCM_16')
    report={'status':'started','config':CONFIG,'audio_s':len(pcm)/16000,'audio_sent_s':0,
        'sources':['AMI ES2004a mixed-headset 300–320 seconds'],
        'reference':reference, 'source_sha256':digest,
        'first_reference_word_start_s':speech_start,
        'normalization':NORMALIZATION,'connections_max':1,'automatic_retries':0,
        'nominal_estimate_usd':len(pcm)/16000/3600*.29,
        'billing_note':'Approximate published hourly equivalent; free balance/actual billing unverified'}
    try:
        asyncio.run(asyncio.wait_for(trial(key,pcm,report),timeout=45))
        report['first_text_after_reference_speech_start_s']=report['first_text_after_stream_s']-speech_start
        report['finish_after_audio_s']=report['finished_s']-report['end_audio_sent_s']
        score=score_words(report['reference'],report['text'])
        report.update(status='completed',wer=score.wer,word_errors=score.errors,
                      reference_words=score.reference_words)
    except Exception as exc:  # noqa: BLE001 — sanitize network failures that may hold credentials
        report.update(status='failed',failure_type=type(exc).__name__,
                      message='Trial did not complete; no automatic retry. Credentials and upstream body omitted.')
    (OUTPUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    render(report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'config','events','final_tokens','reference','sources'}},ensure_ascii=False,indent=2))
    if report['status']!='completed':
        raise SystemExit(1)


if __name__=='__main__':
    main()
