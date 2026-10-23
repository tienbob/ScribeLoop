"""Single-provider streaming transcription and anonymous word-level speakers.

Each source owns one connection. No automatic reconnection, second pass, local
speech model, or cross-connection identity matching. Capture remains independent.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import numpy as np
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.sync.client import connect

from .cloud_meeting import CloudRecordingRunner, pcm_window

STREAMS = ('loopback', 'mic')


class DeepgramError(RuntimeError):
    pass


class DeepgramConfig:
    provider = 'deepgram'
    model = 'nova-3'
    rate = .60  # Conservative estimate including diarization, not actual billing.

    def __init__(self, *, api_key=None, connector=None, budget_usd=.15, max_requests=80):
        if not 0 < budget_usd <= 1 or not 1 <= max_requests <= 200:
            raise ValueError('Invalid cloud budget')
        if api_key is None:
            api_key = os.environ.get('DEEPGRAM_API_KEY', '').strip()
            path = Path(__file__).resolve().parents[3]/'.env'
            if not api_key and path.is_file():
                for line in path.read_text().splitlines():
                    name, sep, value = line.strip().removeprefix('export ').partition('=')
                    if sep and name.strip() == 'DEEPGRAM_API_KEY':
                        api_key = value.strip()
                        if len(api_key) >= 2 and api_key[0] == api_key[-1] and api_key[0] in {'"', "'"}:
                            api_key = api_key[1:-1]
            if not 16 <= len(api_key) <= 512 or not api_key.isascii() or any(c.isspace() for c in api_key):
                api_key = ''
        self.api_key = api_key
        self.connector = connector or connect
        self.budget_usd, self.max_requests = budget_usd, max_requests

    def public(self):
        return {'provider':self.provider, 'model':self.model, 'cloud':True,
            'configured':bool(self.api_key), 'speaker_labels':True, 'streaming':True,
            'languages':['en','vi'], 'default_language':'en', 'auto_language':False,
            'budget_usd':self.budget_usd, 'max_requests':self.max_requests,
            'nominal_usd_per_audio_hour':self.rate, 'updates':'streaming provisional and confirmed text'}


class StreamingBudget:
    def __init__(self, config, prior):
        self.config = config
        self.requests, self.usd = prior.get('requests',0), prior.get('estimated_usd',0)
        self.lock = threading.Lock()

    def reserve(self, stream, emit, cancelled):
        with self.lock:
            usd = 10/3600*self.config.rate
            if self.requests >= self.config.max_requests or self.usd+usd > self.config.budget_usd+1e-12:
                raise DeepgramError('Cloud audio budget reached; recording remains encrypted')
            ack = threading.Event()
            emit({'type':'cloud_request','ack':ack,'reservation':{
                'provider':'deepgram','model':'nova-3','stream':stream,
                'billed_audio_s':10,'estimated_usd':usd,'reserved_at_ms':round(time.time()*1000)}})
            while not ack.wait(.05):
                if cancelled.is_set():
                    raise InterruptedError('Cancelled before upload')
            if cancelled.is_set():
                raise InterruptedError('Cancelled before upload')
            self.requests += 1
            self.usd += usd


def speaker_rows(event, *, stream, epoch, base_ms, sent_ms, language):
    """Final-only rows, split at every speaker change; missing labels stay unknown."""
    alternative = event['channel']['alternatives'][0]
    groups = []
    for word in alternative.get('words',[]):
        start, end = word.get('start'), word.get('end')
        if (isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,(int,float))
                or not isinstance(end,(int,float)) or not np.isfinite([start,end]).all()):
            raise DeepgramError('Invalid provider timestamps')
        a, b = base_ms+round(start*1000), base_ms+round(end*1000)
        if not base_ms <= a < b <= sent_ms+1:
            raise DeepgramError('Provider transcript exceeds sent audio')
        b = min(b,sent_ms)
        speaker = word.get('speaker')
        if speaker is not None and (type(speaker) is not int or not 0 <= speaker <= 9999):
            raise DeepgramError('Invalid provider speaker')
        key = f'deepgram:{epoch}:{stream}:{speaker}' if speaker is not None else None
        text = word.get('punctuated_word',word.get('word','')).strip()
        if not text:
            continue
        if groups and groups[-1]['speaker_key'] == key:
            groups[-1]['end_ms'] = max(groups[-1]['end_ms'],b)
            groups[-1]['text'] += ' '+text
        else:
            groups.append({'stream':stream,'start_ms':a,'end_ms':b,'text':text,
                'speaker_key':key,'is_final':True,'language':language,'confidence':None})
    if not groups and alternative.get('transcript','').strip():
        a = base_ms+round(event['start']*1000)
        b = min(sent_ms,base_ms+round((event['start']+event['duration'])*1000))
        if not base_ms <= a < b <= sent_ms:
            raise DeepgramError('Invalid provider transcript span')
        groups.append({'stream':stream,'start_ms':a,'end_ms':b,
            'text':alternative['transcript'].strip(),'speaker_key':None,
            'is_final':True,'language':language,'confidence':None})
    return groups


def stream_track(audio, sid, stream, emit, cancelled, done, *, config, budget, language, checkpoint):
    sent = confirmed = base = checkpoint
    epoch = uuid4().hex
    # Do not open a paid connection until there is recorded audio to send.
    while not cancelled.is_set():
        finished = done.is_set()
        try:
            frames, report = audio.prefix(sid,stream,durable=True)
        except FileNotFoundError:
            if done.is_set():
                raise
            cancelled.wait(.05)
            continue
        if finished and not report['complete']:
            raise DeepgramError('Recover incomplete audio before resume')
        if report['end_ms'] > sent:
            break
        if finished:
            return
        cancelled.wait(.05)
    if cancelled.is_set():
        return
    budget.reserve(stream,emit,cancelled)
    reserved_until = sent+10000
    params = {'model':'nova-3','language':language,'encoding':'linear16','sample_rate':16000,
        'channels':1,'interim_results':'true','punctuate':'true','endpointing':300,'diarize_model':'v1'}
    url = 'wss://api.deepgram.com/v1/listen?'+urlencode(params)
    # No redirect following: authorization must only reach this fixed host.
    from websockets.exceptions import SecurityError
    try:
        with config.connector(url,additional_headers={'Authorization':'Token '+config.api_key},
                open_timeout=10,close_timeout=2,max_size=1_000_000,proxy=None) as ws:
            closing = None
            metadata = False
            refresh = 0
            while not cancelled.is_set():
                if closing is None and time.monotonic() >= refresh:
                    finished = done.is_set()
                    frames,report = audio.prefix(sid,stream,durable=True)
                    available = int(report['end_ms'])
                    emit({'type':'recorded_checkpoint','stream':stream,'end_ms':available})
                    refresh = time.monotonic()+.1
                    if finished and not report['complete']:
                        raise DeepgramError('Encrypted audio is incomplete')
                    # Send at most one second before reading pending server events.
                    end = min(available,sent+1000,reserved_until)
                    if end > sent:
                        pcm = pcm_window(frames,sent,end)
                        ws.send(np.clip(pcm*32767,-32768,32767).astype('<i2').tobytes())
                        sent = end
                    if sent >= reserved_until and available > sent:
                        budget.reserve(stream,emit,cancelled)
                        reserved_until += 10000
                    if finished and sent >= available:
                        ws.send(json.dumps({'type':'CloseStream'}))
                        closing = time.monotonic()
                if closing is not None and time.monotonic()-closing > 15:
                    raise DeepgramError('Provider did not finalize in time')
                try:
                    raw = ws.recv(timeout=.05)
                except TimeoutError:
                    continue
                except ConnectionClosedOK:
                    if closing is None or not metadata:
                        raise DeepgramError('Stream ended before completion') from None
                    emit({'type':'confirmed_window','stream':stream,'segments':[],'end_ms':sent})
                    return
                event = json.loads(raw)
                if event.get('type') == 'Error':
                    raise DeepgramError('Provider rejected streaming audio; no retry was made')
                if event.get('type') == 'Metadata':
                    metadata = True
                if event.get('type') != 'Results':
                    continue
                alt = event['channel']['alternatives'][0]
                if not event.get('is_final'):
                    emit({'type':'partial','stream':stream,'segment':{'text':alt.get('transcript','')}})
                    continue
                end = base+round((event['start']+event['duration'])*1000)
                if not confirmed <= end <= sent+1:
                    raise DeepgramError('Invalid confirmation timeline')
                end = min(end,sent)
                rows = speaker_rows(event,stream=stream,epoch=epoch,base_ms=base,sent_ms=sent,language=language)
                if any(row['start_ms'] < confirmed or row['end_ms'] > end for row in rows):
                    raise DeepgramError('Overlapping final provider response')
                emit({'type':'confirmed_window','stream':stream,'segments':rows,'end_ms':end})
                confirmed = end
                emit({'type':'transcription_progress','stream':stream,'mode':'streaming',
                    'recorded_ms':available,'confirmed_ms':end,'lag_ms':max(0,available-end)})
    except InvalidStatus as exc:
        status = exc.response.status_code
        reason = {401:'Deepgram key was rejected',402:'Deepgram credits/budget unavailable',
                  403:'Deepgram access denied',429:'Deepgram quota reached'}.get(status,'Deepgram connection rejected')
        raise DeepgramError(reason+'; no retry was made') from None
    except SecurityError:
        raise DeepgramError('Deepgram connection security check failed') from None


def run_streaming(audio,sid,emit,cancelled,done, *, config,budget,language='en',checkpoints=None,**_):
    if language not in {'en','vi'} or not config.api_key:
        raise DeepgramError('Choose English or Vietnamese and configure Deepgram locally')
    if done.is_set():
        # Verify both saved tracks before sending either one on an explicit resume.
        for stream in STREAMS:
            for _frame in audio.frames(sid,stream):
                if cancelled.is_set():
                    return
    emit({'type':'language_locked','language':language,'mode':'manual','confidence':None,'sample_s':0})
    # A provider failure stops both transcription streams, never native recording.
    failed = threading.Event()
    class Cancel:
        def is_set(self):
            return cancelled.is_set() or failed.is_set()
        def wait(self, seconds):
            return failed.wait(seconds) or cancelled.is_set()
    def track(stream):
        try:
            stream_track(audio,sid,stream,emit,Cancel(),done,config=config,budget=budget,
                language=language,checkpoint=(checkpoints or {}).get(stream,{}).get('confirmed_ms',0))
        except Exception:
            failed.set()
            raise
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='deepgram-track') as pool:
        futures = [pool.submit(track,stream) for stream in STREAMS]
        for future in futures:
            future.result()


class DeepgramRecordingRunner(CloudRecordingRunner):
    def __init__(self,*args,config=None,**kwargs):
        config = config or DeepgramConfig()
        if not isinstance(config,DeepgramConfig):
            raise TypeError('The streaming app requires a Deepgram configuration')
        super().__init__(*args,config=config,**kwargs)
        self.budget = StreamingBudget(config,{})

    def _authorize(self,confirmed):
        if confirmed is not True:
            raise ValueError('Explicit consent to send audio to Deepgram is required')
        if not self.cloud.api_key:
            raise ValueError('Configure DEEPGRAM_API_KEY locally')

    def start(self,selection,metadata,title, *, consent_confirmed):
        self._authorize(metadata.get('cloud_consent'))
        if metadata.get('language') not in {'en','vi'}:
            raise ValueError('Select English or Vietnamese before recording')
        self.budget = StreamingBudget(self.cloud,{})
        from .recording import RecordingRunner
        return RecordingRunner.start(self,selection,{**metadata,'provider':'deepgram','model':'nova-3',
            'cloud_budget_usd':self.cloud.budget_usd},title,consent_confirmed=consent_confirmed)

    def _saved_authorization(self,sid,cloud_consent):
        self._saved(sid)
        self._authorize(cloud_consent)
        self.store.grant_cloud(sid,'nova-3',provider='deepgram')
        self.budget = StreamingBudget(self.cloud,self.store.cloud_usage(sid))

    def resume(self,sid, *, language='en',cloud_consent=False):
        if language not in {'en','vi'}:
            raise ValueError('Select English or Vietnamese')
        self._saved(sid)
        if self.store.segments(sid) and self.store.transcript_provider(sid) not in {None,'deepgram'}:
            raise ValueError('Saved transcript uses another provider; explicitly replace it instead of mixing backends')
        return super().resume(sid,language=language,cloud_consent=cloud_consent)

    def reprocess(self,sid, *, language,cloud_consent=False):
        if language not in {'en','vi'}:
            raise ValueError('Select English or Vietnamese')
        return super().reprocess(sid,language=language,cloud_consent=cloud_consent)

    def _live(self,audio,sid,emit,cancelled,done,**kwargs):
        return run_streaming(audio,sid,emit,cancelled,done,config=self.cloud,budget=self.budget,**kwargs)

    def _batch(self,audio,sid,emit,cancelled):
        for stream in STREAMS:
            for _ in audio.frames(sid,stream):
                if cancelled.is_set():
                    return
        done = threading.Event()
        done.set()
        def convert(event):
            if event['type'] == 'confirmed_window':
                for row in event['segments']:
                    emit(row)
            else:
                emit(event)
        return run_streaming(audio,sid,convert,cancelled,done,config=self.cloud,budget=self.budget,
                             language=self.language)
