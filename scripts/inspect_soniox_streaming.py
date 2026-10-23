"""One bounded, paced Soniox public Vietnamese trial; never retries or captures devices."""
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

import numpy as np
import soundfile as sf
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words
from websockets.asyncio.client import connect

SOURCE = Path('data/phase1/groq-vietnamese-inspection')
OUTPUT = Path('data/phase1/soniox-streaming-inspection')
ENDPOINT = 'wss://stt-rt.soniox.com/transcribe-websocket'
CONFIG = {'model':'stt-rt-v5', 'audio_format':'pcm_s16le', 'sample_rate':16000,
    'num_channels':1, 'language_hints':['vi'], 'enable_language_identification':True,
    'enable_speaker_diarization':True, 'enable_endpoint_detection':True}


def load_key():
    value = os.environ.get('SONIOX_API_KEY','').strip()
    if not value and Path('.env').is_file():
        for line in Path('.env').read_text().splitlines():
            name,sep,candidate = line.strip().removeprefix('export ').partition('=')
            if sep and name.strip() == 'SONIOX_API_KEY':
                value = candidate.strip()
                if len(value)>=2 and value[0]==value[-1] and value[0] in {'"',"'"}:
                    value=value[1:-1]
    if not 16<=len(value)<=512 or not value.isascii() or any(c.isspace() for c in value):
        raise ValueError('Configure SONIOX_API_KEY locally; never paste it in chat')
    return value


def prepare():
    manifest=json.loads((SOURCE/'samples.json').read_text())
    parts=[]
    for sample in manifest['samples']:
        path=SOURCE/'audio'/sample['filename']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sample['sha256']:
            raise ValueError('Public fixture hash mismatch')
        pcm,sr=sf.read(path,dtype='float32')
        if sr!=16000 or pcm.ndim!=1 or not np.isfinite(pcm).all():
            raise ValueError('Expected finite mono 16 kHz fixture')
        parts.extend((pcm,np.zeros(4000,dtype='float32')))
    parts.append(np.zeros(16000,dtype='float32'))
    pcm=np.concatenate(parts)
    if not 0<len(pcm)<=20*16000:
        raise ValueError('Trial must not exceed twenty audio seconds')
    return manifest,pcm


def readable(tokens):
    return ''.join(t['text'] for t in tokens if not t['text'].startswith('<')).strip()


async def trial(key,pcm,report):
    started=time.monotonic()
    final=[]
    report['events']=[]
    async with connect(ENDPOINT,additional_headers={'Authorization':'Bearer '+key},
            open_timeout=10,close_timeout=2,max_size=1_000_000,proxy=None) as ws:
        report['connected_s']=time.monotonic()-started
        await ws.send(json.dumps(CONFIG))
        stream_start=time.monotonic()
        report['stream_started_s']=stream_start-started
        async def send():
            for offset in range(0,len(pcm),1600):
                await asyncio.sleep(max(0,stream_start+offset/16000-time.monotonic()))
                chunk=np.clip(pcm[offset:offset+1600]*32767,-32768,32767).astype('<i2')
                await ws.send(chunk.tobytes())
                report['audio_sent_s']=min(offset+1600,len(pcm))/16000
            await asyncio.sleep(max(0,stream_start+len(pcm)/16000-time.monotonic()))
            await ws.send('')
            report['end_audio_sent_s']=time.monotonic()-started
        sender=asyncio.create_task(send())
        try:
            async for raw in ws:
                event=json.loads(raw)
                elapsed=time.monotonic()-started
                if event.get('error_code'):
                    # Never echo upstream error bodies or connection headers.
                    report['provider_error_code']=event['error_code']
                    error_type=event.get('error_type')
                    if error_type in {'organization_balance_exhausted', 'organization_monthly_budget_exhausted', 'project_monthly_budget_exhausted', 'unauthenticated', 'permission_denied', 'model_not_available', 'invalid_request'}:
                        report['provider_error_type']=error_type
                    raise RuntimeError('Soniox rejected or interrupted the trial; no retry made')
                tokens=event.get('tokens',[])
                provisional=[t for t in tokens if not t.get('is_final')]
                confirmed=[t for t in tokens if t.get('is_final')]
                final.extend(confirmed)
                if readable(provisional):
                    report.setdefault('first_provisional_s',elapsed)
                if readable(confirmed):
                    report.setdefault('first_confirmed_s',elapsed)
                if readable(tokens):
                    report.setdefault('first_text_s',elapsed)
                report['events'].append({'received_s':elapsed,'tokens':tokens,
                    'final_audio_proc_ms':event.get('final_audio_proc_ms'),
                    'total_audio_proc_ms':event.get('total_audio_proc_ms')})
                if event.get('finished'):
                    report['finished_s']=elapsed
                    report['final_tokens']=final
                    report['text']=readable(final)
                    return
            raise RuntimeError('Stream closed without finished response; no retry made')
        finally:
            sender.cancel()
            await asyncio.gather(sender,return_exceptions=True)

def render(report):
    esc=html.escape
    ref=report['reference']
    audio=base64.b64encode((OUTPUT/'public-vietnamese.wav').read_bytes()).decode()
    score=f'{report["wer"]:.2%}' if report.get('wer') is not None else 'Not scored'
    text=report.get('text','No completed transcript')
    details={k:v for k,v in report.items() if k not in {'events','final_tokens','reference','text','sources'}}
    (OUTPUT/'results.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Soniox Vietnamese streaming trial</title><style>body{font:17px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#18342b}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccd6cf;padding:16px;text-align:left;vertical-align:top}audio{width:250px}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{display:block}</style>
<h1>Soniox Vietnamese streaming trial</h1><p>One real-time paced connection. Public VIVOS clips only. Language hint: Vietnamese. Small single-speaker sample; speaker and overlap accuracy are not validated.</p>
<table><tr><th>Sound file name</th><th>Transcription result</th><th>WER</th></tr><tr><td>public-vietnamese.wav<small>'''+esc(', '.join(report['sources']))+'''</small><audio controls src="data:audio/wav;base64,'''+audio+'''"></audio></td><td>'''+esc(text)+'''<details><summary>Reference</summary>'''+esc(ref)+'''</details></td><td>'''+score+'''</td></tr></table><h2>Measurements</h2><p>Times are seconds from connection attempt, including handshake. First text is not per-word latency. Provisional text may change; only final text is scored. WER counts Vietnamese whitespace units and does not equate digits with written numbers. Concatenated streaming differs from the earlier separate Groq requests.</p><pre>'''+esc(json.dumps(details,ensure_ascii=False,indent=2))+'''</pre></html>''')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consent-public-upload',action='store_true',required=True)
    parser.parse_args()
    key=load_key()
    manifest,pcm=prepare()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    # Permanent local attempt reservation prevents accidental repeat usage even on failure.
    with (OUTPUT/'attempt.json').open('x') as handle:
        json.dump({'reserved_at_unix':time.time(),'max_connections':1,'max_audio_s':20,
            'max_wall_s':45,'automatic_retries':0},handle)
    sf.write(OUTPUT/'public-vietnamese.wav',pcm,16000,subtype='PCM_16')
    report={'status':'started','config':CONFIG,'audio_s':len(pcm)/16000,'audio_sent_s':0,
        'sources':[s['filename'] for s in manifest['samples']],
        'reference':' '.join(s['reference'] for s in manifest['samples']),
        'normalization':NORMALIZATION,'connections_max':1,'automatic_retries':0,
        'nominal_estimate_usd':len(pcm)/16000/3600*.12,
        'billing_note':'Approximate published hourly equivalent; free balance/actual billing unverified'}
    try:
        asyncio.run(asyncio.wait_for(trial(key,pcm,report),timeout=45))
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
