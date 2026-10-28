"""Single-provider streaming transcription and anonymous word-level speakers.

Each source owns one connection. No automatic reconnection, second pass, local
speech model, or cross-connection identity matching. Capture remains independent.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic as wall_clock
from urllib.parse import urlencode
from uuid import uuid4

import numpy as np
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.sync.client import connect

from ..capture.source import sample_position
from .cloud_meeting import CloudRecordingRunner, pcm_window
from .languages import AUTO_LANGUAGES, LANGUAGE_CHOICES, LANGUAGE_NAMES, word_language

STREAMS = ('loopback', 'mic')


class DeepgramError(RuntimeError):
    pass


def available_audio_ms(report):
    """Use reconstructed sample time, preserving the final sub-ms audio tail.

    Streaming protocol/checkpoints use integer ms. Only a completed track may
    pad its last fractional millisecond; an unfinished track waits for more PCM.
    """
    samples = report.get('timeline_end_samples')
    if samples is None:
        return int(report['end_ms'])
    return (samples+15)//16 if report['complete'] else samples//16


class DeepgramConfig:
    provider = 'deepgram'
    model = 'nova-3'
    rate = .60  # Conservative estimate including diarization, not actual billing.

    def __init__(self, *, api_key=None, connector=None, batch_transport=None, budget_usd=.15, max_requests=80, write_timeout_s=5.):
        if not 0 < budget_usd <= 10 or not 1 <= max_requests <= 7200:
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
        self.batch_transport = batch_transport
        self.budget_usd, self.max_requests = budget_usd, max_requests
        if not isinstance(write_timeout_s,(int,float)) or isinstance(write_timeout_s,bool) or not 0<write_timeout_s<=30:
            raise ValueError('Invalid streaming write deadline')
        self.write_timeout_s = write_timeout_s

    def public(self):
        return {'provider':self.provider, 'model':self.model, 'cloud':True,
            'configured':bool(self.api_key), 'speaker_labels':True, 'streaming':True,
            'languages':{'auto':'Auto · multilingual',**{k:v for k,v in LANGUAGE_NAMES.items() if k in LANGUAGE_CHOICES}},
            'default_language':'auto', 'auto_language':True,
            'auto_languages':list(AUTO_LANGUAGES), 'language_hints':False,
            'budget_usd':self.budget_usd, 'max_requests':self.max_requests,
            'nominal_usd_per_audio_hour':self.rate, 'updates':'streaming provisional and confirmed text'}


class StreamingBudget:
    def __init__(self, config, prior):
        self.config = config
        self.requests, self.usd = prior.get('requests',0), prior.get('estimated_usd',0)
        self.lock = threading.Lock()

    def reserve(self, stream, emit, cancelled, *, seconds=10):
        with self.lock:
            usd = seconds/3600*self.config.rate
            if self.requests >= self.config.max_requests or self.usd+usd > self.config.budget_usd+1e-12:
                raise DeepgramError('Cloud audio budget reached; recording remains encrypted')
            ack = threading.Event()
            emit({'type':'cloud_request','ack':ack,'reservation':{
                'provider':'deepgram','model':'nova-3','stream':stream,
                'billed_audio_s':seconds,'estimated_usd':usd,'reserved_at_ms':round(time.time()*1000)}})
            while not ack.wait(.05):
                if cancelled.is_set():
                    raise InterruptedError('Cancelled before upload')
            if cancelled.is_set():
                raise InterruptedError('Cancelled before upload')
            self.requests += 1
            self.usd += usd


def speaker_rows(event, *, stream, epoch, base_ms, sent_ms, language, end_tolerance_ms=1):
    """Final-only rows, split at every speaker change; missing labels stay unknown."""
    alternative = event['channel']['alternatives'][0]
    groups = []
    for word in alternative.get('words',[]):
        start, end = word.get('start'), word.get('end')
        if (isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,(int,float))
                or not isinstance(end,(int,float)) or not np.isfinite([start,end]).all()):
            raise DeepgramError('Invalid provider timestamps')
        a, b = base_ms+round(start*1000), base_ms+round(end*1000)
        if not (base_ms <= a < min(b,sent_ms) and b <= sent_ms+end_tolerance_ms):
            raise DeepgramError('Provider transcript exceeds sent audio')
        b = min(b,sent_ms)
        speaker = word.get('speaker')
        if speaker is not None and (type(speaker) is not int or not 0 <= speaker <= 9999):
            raise DeepgramError('Invalid provider speaker')
        key = f'deepgram:{epoch}:{stream}:{speaker}' if speaker is not None else None
        text = word.get('punctuated_word',word.get('word','')).strip()
        if not text:
            continue
        detected = word_language(word.get('language')) if language == 'multi' else language
        if language == 'multi' and detected is None:
            candidates = alternative.get('languages',[])
            detected = word_language(candidates[0]) if len(candidates) == 1 else None
        if (groups and groups[-1]['speaker_key'] == key and groups[-1]['language'] == detected
                and a-groups[-1]['end_ms'] < 1500 and len(groups[-1]['text'])+len(text) < 2000):
            groups[-1]['end_ms'] = max(groups[-1]['end_ms'],b)
            groups[-1]['text'] += ' '+text
        else:
            groups.append({'stream':stream,'start_ms':a,'end_ms':b,'text':text,
                'speaker_key':key,'is_final':True,'language':detected,'confidence':None})
    if not groups and alternative.get('transcript','').strip():
        a = base_ms+round(event['start']*1000)
        b = min(sent_ms,base_ms+round((event['start']+event['duration'])*1000))
        if not base_ms <= a < b <= sent_ms:
            raise DeepgramError('Invalid provider transcript span')
        groups.append({'stream':stream,'start_ms':a,'end_ms':b,
            'text':alternative['transcript'].strip(),'speaker_key':None,
            'is_final':True,'language':None if language == 'multi' else language,'confidence':None})
    return groups


class StreamWrites:
    """Interrupt blocked socket writes without waiting for the protocol mutex."""
    def __init__(self, ws, cancelled, timeout):
        self.ws, self.cancelled, self.timeout = ws, cancelled, timeout
        self.stopped = threading.Event()
        self.timed_out = threading.Event()
        self.deadline = None

    def __enter__(self):
        self.worker = threading.Thread(target=self.watch, name='stream-write-deadline', daemon=True)
        self.worker.start()
        return self

    def watch(self):
        while not self.stopped.wait(.02):
            deadline = self.deadline
            expired = deadline is not None and wall_clock() >= deadline
            if self.cancelled.is_set() or expired:
                if expired:
                    self.timed_out.set()
                connection = getattr(self.ws,'socket',None)
                if connection is not None:
                    # close()/close_socket() can wait for a mutex held by sendall.
                    # shutdown wakes the write and receiver without acquiring it.
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                elif callable(getattr(self.ws,'abort',None)):
                    self.ws.abort()  # Injected transports implement the same abort contract.
                return

    def send(self, value):
        if self.cancelled.is_set():
            raise InterruptedError('Streaming cancelled; saved audio is preserved')
        self.deadline = wall_clock()+self.timeout
        try:
            self.ws.send(value)
        except Exception:
            if self.cancelled.is_set():
                raise InterruptedError('Streaming cancelled; saved audio is preserved') from None
            if self.timed_out.is_set():
                raise DeepgramError('Streaming upload timed out; saved audio is preserved; no retry was made') from None
            raise
        finally:
            self.deadline = None
        if self.timed_out.is_set():
            raise DeepgramError('Streaming upload timed out; saved audio is preserved; no retry was made')
        if self.cancelled.is_set():
            raise InterruptedError('Streaming cancelled; saved audio is preserved')

    def __exit__(self, *_):
        self.stopped.set()
        self.worker.join(.2)


def stream_track(audio, sid, stream, emit, cancelled, done, **kwargs):
    while not cancelled.is_set():
        try:
            cursor=audio.cursor(sid,stream)
            break
        except FileNotFoundError:
            if done.is_set():
                raise
            cancelled.wait(.05)
    else:
        return
    with cursor:
        return _stream_cursor(cursor,sid,stream,emit,cancelled,done,**kwargs)


def _stream_cursor(cursor, sid, stream, emit, cancelled, done, *, config, budget, language, checkpoint):
    sent = confirmed = base = checkpoint
    epoch = uuid4().hex
    frames=[]
    def remember(batch):
        frames.extend(f for f in batch if sample_position(f)+f.pcm.size>sent*16)
        if len(frames)>64000 or sum(f.pcm.size for f in frames)>4*16000:
            raise DeepgramError('Encrypted audio frame timeline is not advancing')
    # Do not open a paid connection until there is recorded audio to send.
    while not cancelled.is_set():
        finished = done.is_set()
        try:
            batch, report = cursor.read(max_frames=64,max_samples=16000,durable=True)
            remember(batch)
        except FileNotFoundError:
            if done.is_set():
                raise
            cancelled.wait(.05)
            continue
        if finished and report['caught_up'] and not report['complete']:
            raise DeepgramError('Recover incomplete audio before resume')
        if available_audio_ms(report) > sent:
            break
        if finished and report['complete']:
            if checkpoint>available_audio_ms(report):
                raise DeepgramError('Checkpoint exceeds authenticated audio')
            return
        if not report['caught_up']:
            continue
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
                open_timeout=10,close_timeout=2,max_size=1_000_000,proxy=None) as ws, StreamWrites(ws,cancelled,config.write_timeout_s) as writes:
            closing = None
            processed_ms = None
            provisional_end = None
            refresh = 0
            progress_refresh = 0
            while not cancelled.is_set():
                if closing is None and time.monotonic() >= refresh:
                    finished = done.is_set()
                    # Fill a duration-bounded window regardless of packet size.
                    # Large packets may overshoot by one frame; memory stays bounded.
                    while available_audio_ms(report)<sent+1000 and not report['complete'] and not cancelled.is_set():
                        batch,report = cursor.read(max_frames=64,max_samples=16000,durable=True)
                        remember(batch)
                        if report['caught_up']:
                            break
                    available = available_audio_ms(report)
                    emit({'type':'recorded_checkpoint','stream':stream,'end_ms':available})
                    refresh = time.monotonic()+.1
                    if finished and report['caught_up'] and not report['complete']:
                        raise DeepgramError('Encrypted audio is incomplete')
                    # Send at most one second before reading pending server events.
                    end = min(available,sent+1000,reserved_until)
                    if end > sent:
                        pcm = pcm_window(frames,sent,end)
                        writes.send(np.clip(pcm*32767,-32768,32767).astype('<i2').tobytes())
                        sent = end
                        frames[:]=[f for f in frames if sample_position(f)+f.pcm.size>sent*16]
                    if sent >= reserved_until and available > sent:
                        budget.reserve(stream,emit,cancelled)
                        reserved_until += 10000
                    if finished and report['complete'] and sent >= available:
                        writes.send(json.dumps({'type':'CloseStream'}))
                        closing = time.monotonic()
                    if closing is None and (not report['caught_up'] or available>sent):
                        refresh = 0  # Catch up promptly; recv below still gets a turn.
                    if time.monotonic() >= progress_refresh:
                        emit({'type':'transcription_progress','stream':stream,'mode':'streaming',
                            'recorded_ms':available,'sent_ms':sent,'confirmed_ms':confirmed,
                            'send_lag_ms':max(0,available-sent),'lag_ms':max(0,sent-confirmed)})
                        progress_refresh = time.monotonic()+.5
                if closing is not None and time.monotonic()-closing > 15:
                    raise DeepgramError('Provider did not finalize in time')
                try:
                    raw = ws.recv(timeout=.05)
                except TimeoutError:
                    continue
                except ConnectionClosedOK:
                    if closing is None or processed_ms is None or processed_ms < sent:
                        raise DeepgramError('Stream ended before completion') from None
                    if provisional_end is not None and provisional_end > confirmed:
                        raise DeepgramError('Stream ended with unconfirmed transcript; resume saved audio') from None
                    # Metadata may acknowledge trailing silence, but must never
                    # turn outstanding provisional speech into a saved checkpoint.
                    emit({'type':'confirmed_window','stream':stream,'segments':[],'end_ms':sent})
                    return
                event = json.loads(raw)
                if event.get('type') == 'Error':
                    raise DeepgramError('Provider rejected streaming audio; no retry was made')
                if event.get('type') == 'Metadata':
                    duration = event.get('duration')
                    if isinstance(duration,(int,float)) and not isinstance(duration,bool) and np.isfinite(duration) and duration >= 0:
                        processed_ms = base+round(duration*1000)
                if event.get('type') != 'Results':
                    continue
                alt = event['channel']['alternatives'][0]
                if not event.get('is_final'):
                    if alt.get('transcript','').strip():
                        provisional_end = base+round((event['start']+event['duration'])*1000)
                    emit({'type':'partial','stream':stream,'segment':{'text':alt.get('transcript','')}})
                    continue
                end = base+round((event['start']+event['duration'])*1000)
                # Final model word boundaries may slightly exceed the supplied
                # clip after CloseStream (observed 20 ms). Permit only bounded
                # closing-tail rounding; starts still must be inside sent audio.
                end_tolerance = 100 if closing is not None else 1
                if not confirmed <= end <= sent+end_tolerance:
                    raise DeepgramError('Invalid confirmation timeline')
                end = min(end,sent)
                rows = speaker_rows(event,stream=stream,epoch=epoch,base_ms=base,sent_ms=sent,language=language,
                                    end_tolerance_ms=end_tolerance)
                if any(row['start_ms'] < confirmed or row['end_ms'] > end for row in rows):
                    raise DeepgramError('Overlapping final provider response')
                emit({'type':'confirmed_window','stream':stream,'segments':rows,'end_ms':end})
                confirmed = end
                emit({'type':'transcription_progress','stream':stream,'mode':'streaming',
                    'recorded_ms':available,'sent_ms':sent,'confirmed_ms':end,
                    'send_lag_ms':max(0,available-sent),'lag_ms':max(0,sent-end)})
    except InvalidStatus as exc:
        status = exc.response.status_code
        reason = {401:'Deepgram key was rejected',402:'Deepgram credits/budget unavailable',
                  403:'Deepgram access denied',429:'Deepgram quota reached'}.get(status,'Deepgram connection rejected')
        raise DeepgramError(reason+'; no retry was made') from None
    except SecurityError:
        raise DeepgramError('Deepgram connection security check failed') from None


def run_streaming(audio,sid,emit,cancelled,done, *, config,budget,language='auto',checkpoints=None,**_):
    if language not in LANGUAGE_CHOICES or not config.api_key:
        raise DeepgramError('Choose Auto or a supported language and configure Deepgram locally')
    if done.is_set():
        # Verify both saved tracks before sending either one on an explicit resume.
        for stream in STREAMS:
            report=audio.scan(sid,stream)
            checkpoint=(checkpoints or {}).get(stream,{}).get('confirmed_ms',0)
            if cancelled.is_set():
                return
            if not report['complete'] or type(checkpoint) is not int or not 0<=checkpoint<=available_audio_ms(report):
                raise DeepgramError('Recover incomplete audio or invalid saved checkpoints before resume')
    automatic = language == 'auto'
    language = 'multi' if automatic else language
    emit({'type':'language_locked','language':language,'mode':'multilingual' if automatic else 'manual',
          'confidence':None,'sample_s':0})
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
    supported_languages = LANGUAGE_CHOICES
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
        if metadata.get('language','auto') not in LANGUAGE_CHOICES:
            raise ValueError('Select Auto or a supported conversation language')
        self.budget = StreamingBudget(self.cloud,{})
        from .recording import RecordingRunner
        return RecordingRunner.start(self,selection,{**metadata,'provider':'deepgram','model':'nova-3',
            'cloud_budget_usd':self.cloud.budget_usd},title,consent_confirmed=consent_confirmed)

    def _saved_authorization(self,sid,cloud_consent):
        self._saved(sid)
        self._authorize(cloud_consent)
        self.store.grant_cloud(sid,'nova-3',provider='deepgram')
        self.budget = StreamingBudget(self.cloud,self.store.cloud_usage(sid))

    def resume(self,sid, *, language='auto',cloud_consent=False):
        if language not in LANGUAGE_CHOICES:
            raise ValueError('Select Auto or a supported conversation language')
        self._saved(sid)
        if self.store.segments(sid) and self.store.transcript_provider(sid) not in {None,'deepgram'}:
            raise ValueError('Saved transcript uses another provider; explicitly replace it instead of mixing backends')
        return super().resume(sid,language=language,cloud_consent=cloud_consent)

    def reprocess(self,sid, *, language,cloud_consent=False):
        if language not in LANGUAGE_CHOICES:
            raise ValueError('Select Auto or a supported conversation language')
        return super().reprocess(sid,language=language,cloud_consent=cloud_consent)

    def _live(self,audio,sid,emit,cancelled,done,**kwargs):
        return run_streaming(audio,sid,emit,cancelled,done,config=self.cloud,budget=self.budget,**kwargs)

    def _batch(self,audio,sid,emit,cancelled):
        from .deepgram_batch import run_batch
        return run_batch(audio,sid,emit,cancelled,config=self.cloud,budget=self.budget,
                         language=self.language)
