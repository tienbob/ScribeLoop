"""Real local ASR + grounded notes smoke test; synthetic speech, no devices/cloud.

Uses an isolated encrypted database/audio store, never the user's workspace.
Requires installed macOS voices, Ollama and already downloaded models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.evaluation.metrics import score_words
from scribeloop_core.llm.grounded import export_markdown, transcript_hash
from scribeloop_core.llm.rolling import RollingNotes
from scribeloop_core.llm.summary_provider import SummaryProvider
from scribeloop_core.pipeline.local_transcription import (
    LocalRecordingRunner,
    LocalTranscriptionConfig,
)
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted

ASR = 'hf.co/mradermacher/Qwen3-ASR-0.6B-GGUF:Q4_K_M'
SPEECH = [
    ('en', 'Samantha', 'We agreed to test the calendar feature on Friday. Alex will send the report tomorrow. The budget is fifty dollars. We have not decided the launch date.'),
    ('vi', 'Linh', 'Chúng ta sẽ kiểm tra tính năng lịch vào thứ sáu. Nam sẽ gửi báo cáo vào ngày mai. Ngân sách là năm mươi đô la. Chúng ta chưa quyết định ngày ra mắt.'),
]


class Keys:
    def __init__(self):
        self.rows = {}
    def create(self, key):
        self.rows[key] = secrets.token_bytes(32)
        return self.rows[key]
    def get(self, key):
        return self.rows.get(key)
    def delete(self, key):
        del self.rows[key]


class SpeechSource:
    def __init__(self, pcm):
        self.pcm = pcm
        self.done = threading.Event()
        self.stopped = threading.Event()
    def frames(self):
        for i in range(0, len(self.pcm), 1600):
            if self.stopped.is_set():
                break
            yield AudioFrame(self.pcm[i:i+1600], 'mic', i//16, time.monotonic(), i)
        self.done.set()
        self.stopped.wait(60)
    def request_stop(self):
        self.stopped.set()
        return True
    def close(self):
        self.stopped.set()


async def run(output, asr, notes):
    from scribeloop_core.storage.sessions import SessionStore
    output.mkdir(parents=True, exist_ok=True)
    requests = []
    def audit(request):
        if request.url.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('Non-loopback request blocked')
        if request.url.path == '/api/pull':
            raise RuntimeError('Models must be downloaded before this offline test')
        requests.append(str(request.url))
    async def async_audit(request):
        audit(request)
    original_sync, original_async = httpx.Client, httpx.AsyncClient
    def sync_client(**kwargs):
        return original_sync(event_hooks={'request': [audit]}, **kwargs)
    def async_client(**kwargs):
        return original_async(event_hooks={'request': [async_audit]}, **kwargs)
    httpx.AsyncClient = async_client
    config = LocalTranscriptionConfig(model=asr, client_factory=sync_client)
    provider = SummaryProvider(settings={'SUMMARY_PROVIDER':'Ollama',
        'SUMMARY_BASE_URL':'http://127.0.0.1:11434/v1','SUMMARY_MODEL':notes,
        'SUMMARY_INSTRUCTIONS':'Keep the original conversation language. Preserve decisions, owners, deadlines and uncertainty. Do not invent facts.'})
    report = {'asr_model':asr,'notes_model':notes,'synthetic':True,
              'physical_capture':False,'playback':False,'cases':[]}
    try:
        for language, voice, reference in SPEECH:
            with tempfile.TemporaryDirectory(prefix='scribeloop-local-check-') as temp:
                temp = Path(temp)
                speech = temp/'speech.aiff'
                await asyncio.to_thread(subprocess.run,
                    ['say','-v',voice,'-r','165','-o',str(speech),reference],check=True)
                pcm, rate = sf.read(speech,dtype='float32')
                if pcm.ndim > 1:
                    pcm = pcm.mean(axis=1)
                divisor = math.gcd(rate,16000)
                pcm = resample_poly(pcm,16000//divisor,rate//divisor).astype(np.float32)
                sf.write(output/f'{language}-synthetic.wav',pcm,16000,subtype='PCM_16')
                conn = connect_encrypted(temp/'workspace.db',secrets.token_bytes(32))
                store = SessionStore(migrate(conn))
                store.accept_agreement(store.AGREEMENT_VERSION)
                audio = SessionAudioStore(temp/'audio',Keys())
                source = SpeechSource(pcm)
                events = []
                async def publish(event, events=events, language=language):
                    events.append(event)
                    if event['type']=='local_model_status':
                        print(language,event.get('state'),event.get('status'),flush=True)
                runner = LocalRecordingRunner(store,audio,lambda *a,source=source,**kw:source,publish,config=config)
                began = time.monotonic()
                session = runner.start(SimpleNamespace(scope='microphone'),
                    {'scope':'microphone','language':language},f'Synthetic local {language}',consent_confirmed=True)
                await asyncio.wait_for(asyncio.to_thread(source.done.wait),20)
                await asyncio.sleep(.2)
                assert not any(e['type']=='segment' for e in events), 'Unexpected live transcription'
                runner.stop(session['id'])
                await asyncio.wait_for(runner.task,600)
                sid = session['id']
                status = store.get(sid)['status']
                rows = store.segments(sid)
                if status != 'completed' or not rows:
                    raise RuntimeError(f'ASR failed: {status}; events={events[-6:]}')
                asr_s = time.monotonic()-began
                text = ' '.join(row['text'] for row in rows)
                (output/f'{language}-transcript.txt').write_text(text+'\n')
                revision = store.get(sid)['transcript_revision']
                began = time.monotonic()
                engine = RollingNotes(store,provider)
                value = await engine.run(sid,rows,revision=revision)
                notes_s = time.monotonic()-began
                saved = store.save_grounded_summary(sid,revision,transcript_hash(rows),value,
                    provider='Ollama',model=notes)
                (output/f'{language}-notes.md').write_text(export_markdown(store.get(sid),rows,store.revisions(sid),saved))
                case = {'language':language,'reference':reference,'transcript':text,
                    'audio_s':len(pcm)/16000,'asr_s':asr_s,'notes_s':notes_s,
                    'wer':score_words(reference,text).wer,'segments':len(rows),
                    'status':status,'citations_validated':True,'notes':value}
                report['cases'].append(case)
                print(json.dumps(case,ensure_ascii=False),flush=True)
                await runner.close()
                conn.close()
    finally:
        httpx.AsyncClient = original_async
        report['requests'] = requests
        (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('build/ollama-local-check'))
    parser.add_argument('--asr',default=ASR)
    parser.add_argument('--notes',default='gemma4:e2b')
    args = parser.parse_args()
    asyncio.run(run(args.output,args.asr,args.notes))
