"""Groq Turbo meeting path: local encrypted recorder, explicit cloud consent.

Short file batches provide updates during recording. There is no local speech
model, implicit paid second pass, automatic retry or fabricated speaker identity.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import numpy as np

from .groq_asr import MODELS, GroqError, load_groq_key, transcribe_groq
from .language import LanguageDetectionError
from .recording import RecordingRunner

ROOT = Path(__file__).resolve().parents[3]
MODEL = 'whisper-large-v3-turbo'
STREAMS = ('loopback','mic')


class GroqConfig:
    def __init__(self, *, api_key=None, request=None, budget_usd=.05, max_requests=80):
        if not 0 < budget_usd <= 1 or not 1 <= max_requests <= 200:
            raise ValueError('Invalid cloud transcription budget')
        if api_key is None:
            try:
                api_key = load_groq_key(ROOT/'.env')
            except GroqError:
                api_key = ''
        self.api_key = api_key
        self.request = request or transcribe_groq
        self.budget_usd, self.max_requests = budget_usd,max_requests

    def public(self):
        return {'provider':'groq','model':MODEL,'configured':bool(self.api_key),
                'cloud':True,'updates':'short batches, approximately 10–20 seconds plus network delay',
                'speaker_labels':False,'budget_usd':self.budget_usd,
                'max_requests':self.max_requests,'nominal_usd_per_audio_hour':MODELS[MODEL]}


class Budget:
    def __init__(self, config, prior):
        self.config = config
        self.requests = prior.get('requests',0)
        self.usd = prior.get('estimated_usd',0)

    def reserve(self, stream, seconds, emit, cancelled):
        billed_s = max(10,seconds)
        usd = billed_s/3600*MODELS[MODEL]
        if self.requests >= self.config.max_requests or self.usd+usd > self.config.budget_usd:
            raise GroqError('Application cloud request/cost cap reached. Recording stays encrypted; no further request was made.')
        receipt = {'provider':'groq','model':MODEL,'stream':stream,'billed_audio_s':billed_s,
                   'estimated_usd':usd,'reserved_at_ms':round(time.time()*1000)}
        ack = threading.Event()
        emit({'type':'cloud_request','reservation':receipt,'ack':ack})
        while not ack.wait(.05):
            if cancelled.is_set():
                raise InterruptedError('Cloud request cancelled before dispatch')
        if cancelled.is_set():
            raise InterruptedError('Cloud request cancelled before dispatch')
        self.requests += 1
        self.usd += usd


def pcm_window(frames,start,end):
    """Absolute recorder timeline with zero-filled gaps; no VAD/model imports."""
    if not 0 <= start < end <= 305000:
        raise ValueError('Invalid recording window')
    pcm = np.zeros(round((end-start)*16),dtype=np.float32)
    for frame in frames:
        offset = round((frame.t_ms-start)*16)
        lo,hi = max(0,offset),min(len(pcm),offset+len(frame.pcm))
        if hi>lo:
            pcm[lo:hi] = frame.pcm[lo-offset:hi-offset]
    return pcm


def language_code(value):
    return {'english':'en','en':'en','vietnamese':'vi','vi':'vi'}.get(str(value).lower())


def run_cloud(audio,sid,emit,cancelled,done, *, config,budget,language='auto',
              locked_language=None,checkpoints=None,confirmed_text=None, **_):
    if not config.api_key:
        raise GroqError('Configure GROQ_API_KEY locally before cloud transcription')
    confirmed = {s:(checkpoints or {}).get(s,{}).get('confirmed_ms',0) for s in STREAMS}
    previous = {s:(confirmed_text or {}).get(s,'') for s in STREAMS}
    lock = locked_language
    if language in {'vi','en'}:
        lock = {'language':language,'mode':'manual','confidence':None,'sample_s':0}
    if lock:
        emit({'type':'language_locked',**lock})
    began = time.monotonic()
    deferred = set()
    # A bounded HTTP timeout bounds shutdown even if an upstream stalls. Capture
    # Stop is independent and never waits for this network operation.
    with httpx.Client(timeout=httpx.Timeout(25,connect=5),follow_redirects=False,trust_env=False) as client:
        while not cancelled.is_set():
            finished = done.is_set()
            advanced = False
            ends = {}
            for stream in STREAMS:
                if cancelled.is_set():
                    return
                try:
                    frames,report = audio.prefix(sid,stream,durable=True)
                except FileNotFoundError:
                    if finished:
                        raise
                    continue
                if finished and not report['complete']:
                    raise ValueError('Recover incomplete encrypted audio before cloud resume')
                available = int(report['end_ms'])
                ends[stream] = available
                start = confirmed[stream]
                emit({'type':'recorded_checkpoint','stream':stream,'end_ms':available})
                lag = max(0,available-start)
                if not finished and (lag>=60000 or stream in deferred):
                    deferred.add(stream)
                    emit({'type':'transcription_progress','stream':stream,'mode':'deferred',
                        'lag_ms':lag,'recorded_ms':available,'confirmed_ms':start})
                    continue
                if available<=start or (not finished and lag<10000):
                    continue
                end = min(available,start+20000)
                context_start = max(0,start-1500)
                pcm = pcm_window(frames,context_start,end)
                new_audio = pcm[round((start-context_start)*16):]
                rows = []
                if np.max(np.abs(new_audio),initial=0)>1e-5:
                    budget.reserve(stream,len(pcm)/16000,emit,cancelled)
                    result = config.request(pcm,api_key=config.api_key,model=MODEL,
                        language=lock['language'] if lock else 'auto',client=client)
                    if cancelled.is_set():
                        return
                    if not lock:
                        code = language_code(result.get('language'))
                        if code is None:
                            raise LanguageDetectionError('Cloud language is uncertain; choose Vietnamese or English')
                        lock = {'language':code,'mode':'auto','confidence':None,'sample_s':len(pcm)/16000}
                        emit({'type':'language_locked',**lock})
                    from ..evaluation.metrics import normalize
                    for raw in result['segments']:
                        a,b = context_start+raw['start_ms'],context_start+raw['end_ms']
                        if not context_start<=a<b<=end:
                            raise GroqError('Cloud transcript exceeds supplied audio')
                        if b<=start:
                            continue
                        text = raw['text'].strip()
                        if a<start:
                            tokens, prior = text.split(),previous[stream].split()[-64:]
                            overlap = 0
                            for count in range(1,min(len(tokens),len(prior))+1):
                                if normalize(' '.join(tokens[:count])) == normalize(' '.join(prior[-count:])):
                                    overlap = count
                            text = ' '.join(tokens[overlap:])
                            a = start
                        if text:
                            rows.append({'stream':stream,'start_ms':a,'end_ms':b,'text':text,
                                'is_final':True,'language':lock['language'],'confidence':None})
                emit({'type':'confirmed_window','stream':stream,'end_ms':end,'segments':rows})
                confirmed[stream] = end
                previous[stream] = (previous[stream]+' '+' '.join(r['text'] for r in rows))[-4096:]
                emit({'type':'transcription_progress','stream':stream,
                    'mode':'catching_up' if available-end>12000 else 'cloud_batches',
                    'recorded_ms':available,'confirmed_ms':end,'lag_ms':max(0,available-end),
                    'requests':budget.requests,'estimated_usd':budget.usd,'wall_s':time.monotonic()-began})
                advanced = True
            if finished and len(ends)==2 and all(confirmed[s]>=ends[s] for s in STREAMS):
                return
            if not advanced:
                cancelled.wait(.25)


class CloudRecordingRunner(RecordingRunner):
    def __init__(self,store,audio,source_factory,publish, *, config=None, **kwargs):
        self.cloud = config or GroqConfig()
        self.budget = Budget(self.cloud,{})
        super().__init__(store,audio,source_factory,publish,transcriber=self._batch,
            live_transcriber=self._live,diarizer=False,finalize_transcript=False,**kwargs)

    def _authorize(self, confirmed):
        if confirmed is not True:
            raise ValueError('Explicit consent to send audio to Groq is required')
        if not self.cloud.api_key:
            raise ValueError('Groq key is not configured')

    def start(self,selection,metadata,title, *, consent_confirmed):
        self._authorize(metadata.get('cloud_consent'))
        self.budget = Budget(self.cloud,{})
        return super().start(selection,{**metadata,'provider':'groq','model':MODEL,
            'cloud_budget_usd':self.cloud.budget_usd},title,consent_confirmed=consent_confirmed)

    def _saved_authorization(self,sid,cloud_consent):
        self._saved(sid)
        self._authorize(cloud_consent)
        self.store.grant_cloud(sid,MODEL)
        self.budget = Budget(self.cloud,self.store.cloud_usage(sid))

    def resume(self,sid, *, language='auto',cloud_consent=False):
        self._saved_authorization(sid,cloud_consent)
        return super().resume(sid,language=language)

    def reprocess(self,sid, *, language,cloud_consent=False):
        self._saved_authorization(sid,cloud_consent)
        return super().reprocess(sid,language=language)

    def _live(self,audio,sid,emit,cancelled,done,**kwargs):
        return run_cloud(audio,sid,emit,cancelled,done,config=self.cloud,budget=self.budget,**kwargs)

    def _batch(self,audio,sid,emit,cancelled):
        # Strict complete files are verified by run_cloud before upload. Full
        # reprocessing is explicit, and uses replacement revision atomically.
        for stream in STREAMS:
            for _frame in audio.frames(sid,stream):
                if cancelled.is_set():
                    return
        done = threading.Event()
        done.set()
        def convert(event):
            if event['type']=='confirmed_window':
                for row in event['segments']:
                    emit(row)
            else:
                emit(event)
        return run_cloud(audio,sid,convert,cancelled,done,config=self.cloud,budget=self.budget,
                         language=self.language,locked_language=self.language_lock)
