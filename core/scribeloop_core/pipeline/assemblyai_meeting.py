"""AssemblyAI live turns and explicit prerecorded reruns; no provider fallback."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import numpy as np
from websockets.exceptions import ConnectionClosedOK

from ..capture.source import sample_position
from .cloud_meeting import pcm_window
from .deepgram_meeting import (
    STREAMS,
    DeepgramConfig,
    DeepgramError,
    StreamWrites,
    available_audio_ms,
    speaker_rows,
)

STREAMING_LANGUAGES = frozenset(['af', 'ar', 'yue', 'ca', 'da', 'nl', 'en', 'et', 'fi', 'fr', 'gl', 'de', 'he', 'hi', 'it', 'ja', 'ko', 'zh', 'mr', 'no', 'nn', 'fa', 'pt', 'ro', 'ru', 'es', 'sv', 'tr', 'ur', 'vi', 'xh', 'zu'])


class AssemblyAIConfig(DeepgramConfig):
    provider = 'assemblyai'
    model = 'universal-3-6-pro'
    # Conservative application estimate including diarization, not a price quote.
    rate = 1.20

    def __init__(self, *, api_key='', **kwargs):
        super().__init__(api_key=api_key, **kwargs)

    def public(self):
        return {**super().public(), 'updates':'streaming partial and final turns',
                'billing_basis':'connection duration per source',
                'language_hints_restrict':False}


def language_options(language, hints=None):
    # Prefer an explicit fixed language over the UI's default auto hints.
    codes = hints if language in {'auto','multi'} else [language]
    result = {'language_detection':'true'}
    if codes and any(code not in STREAMING_LANGUAGES for code in codes):
        raise ValueError('Choose languages supported by AssemblyAI streaming')
    if codes:
        result['language_codes'] = json.dumps(codes)
    return result


def rows_for_words(words, *, stream, epoch, base, sent, language, speaker=None):
    converted = []
    for word in words:
        label = word.get('speaker',speaker)
        if label == 'PENDING':
            label = None
        if label is not None and (not isinstance(label,str) or not re.fullmatch('[A-Z]{1,2}',label)):
            raise DeepgramError('Invalid AssemblyAI speaker label')
        number = None
        if label is not None:
            number = 0
            for char in label:
                number = number*26+ord(char)-64
        a,b = word.get('start'),word.get('end')
        if type(a) not in {int,float} or type(b) not in {int,float}:
            raise DeepgramError('Invalid AssemblyAI timestamps')
        converted.append({'start':a/1000,'end':b/1000,'word':word.get('text',''),
                          'speaker':number,'language':language})
    rows = speaker_rows({'channel':{'alternatives':[{'words':converted}]}},stream=stream,
        epoch=epoch,base_ms=base,sent_ms=sent,language=language or 'multi',end_tolerance_ms=100)
    for row in rows:
        if row['speaker_key']:
            row['speaker_key'] = row['speaker_key'].replace('deepgram:','assemblyai:',1)
    return rows


def _track(audio,sid,stream,emit,cancelled,done,*,config,budget,language,language_hints,checkpoint):
    while not cancelled.is_set():
        try:
            cursor = audio.cursor(sid,stream)
            break
        except FileNotFoundError:
            if done.is_set():
                raise
            cancelled.wait(.05)
    else:
        return
    with cursor:
        frames,report = [],None
        sent = confirmed = checkpoint
        epoch = uuid4().hex
        # Avoid a paid connection for an empty compatibility track.
        while not cancelled.is_set():
            batch,report = cursor.read(max_frames=64,max_samples=16000,durable=True)
            frames.extend(batch)
            frames[:] = [f for f in frames if sample_position(f)+f.pcm.size>sent*16]
            if available_audio_ms(report)>sent:
                break
            if done.is_set() and report['caught_up']:
                if not report['complete'] or checkpoint>available_audio_ms(report):
                    raise DeepgramError('Recover incomplete audio or invalid checkpoint before resume')
                return
            cancelled.wait(.05)
        if cancelled.is_set():
            return
        budget.reserve(stream,emit,cancelled)
        params = {'speech_model':config.model,'sample_rate':16000,'encoding':'pcm_s16le',
                  'speaker_labels':'true',**language_options(language,language_hints)}
        if config.quality.vocabulary:
            params['keyterms_prompt'] = json.dumps(config.quality.vocabulary)
        url = 'wss://streaming.assemblyai.com/v3/ws?'+urlencode(params)
        with config.connector(url,additional_headers={'Authorization':config.api_key},
                open_timeout=10,close_timeout=2,max_size=1_000_000,proxy=None) as ws, \
                StreamWrites(ws,cancelled,config.write_timeout_s) as writes:
            began = time.monotonic()
            reserved = 10
            closing = None
            seen = set()
            turns = {}
            last_write = began
            while not cancelled.is_set():
                now = time.monotonic()
                # Bill for connection time as well as audio, including silent waits.
                if max(now-began,(sent-checkpoint)/1000)>=reserved-1:
                    budget.reserve(stream,emit,cancelled)
                    reserved += 10
                if closing is None:
                    batch,report = cursor.read(max_frames=64,max_samples=16000,durable=True)
                    frames.extend(batch)
                    available = available_audio_ms(report)
                    if done.is_set() and report['caught_up'] and not report['complete']:
                        raise DeepgramError('Encrypted audio is incomplete')
                    end = min(available,sent+1000,checkpoint+reserved*1000)
                    if end>sent:
                        pcm = pcm_window(frames,sent,end)
                        writes.send(np.clip(pcm*32767,-32768,32767).astype('<i2').tobytes())
                        sent = end
                        last_write = now
                    frames[:] = [f for f in frames if sample_position(f)+f.pcm.size>sent*16]
                    if sum(f.pcm.size for f in frames)>4*16000:
                        raise DeepgramError('Audio timeline is not advancing')
                    emit({'type':'recorded_checkpoint','stream':stream,'end_ms':available})
                    if done.is_set() and report['complete'] and sent>=available:
                        writes.send(json.dumps({'type':'Terminate'}))
                        closing = now
                    elif now-last_write>=3:
                        writes.send(json.dumps({'type':'KeepAlive'}))
                        last_write = now
                if closing is not None and now-closing>15:
                    raise DeepgramError('AssemblyAI did not finalize in time')
                try:
                    event = json.loads(ws.recv(timeout=.05))
                except TimeoutError:
                    continue
                except ConnectionClosedOK:
                    raise DeepgramError('AssemblyAI stream closed without completion') from None
                kind = event.get('type')
                if kind == 'Termination':
                    duration = event.get('audio_duration_seconds')
                    if closing is None or type(duration) not in {int,float} or not np.isfinite(duration) or checkpoint+round(duration*1000)<sent-100:
                        raise DeepgramError('AssemblyAI stream ended before all audio was processed')
                    emit({'type':'confirmed_window','stream':stream,'segments':[],'end_ms':sent})
                    return
                if kind == 'Error' or event.get('error'):
                    raise DeepgramError('AssemblyAI rejected transcription; no retry was made')
                if kind == 'SpeakerRevision':
                    for revision in event.get('revisions',[]):
                        order = revision.get('turn_order')
                        original = turns.get(order)
                        if original is None:
                            raise DeepgramError('AssemblyAI revised an unknown turn')
                        revised = rows_for_words(revision.get('words',[]),stream=stream,epoch=epoch,
                            base=checkpoint,sent=sent,language=original['language'],
                            speaker=revision.get('speaker_label'))
                        emit({'type':'speaker_relabel','stream':stream,
                              'original':original['rows'],'revised':revised})
                        turns[order] = {**original,'rows':revised}
                    continue
                if kind != 'Turn':
                    continue
                if not event.get('end_of_turn'):
                    emit({'type':'partial','stream':stream,'segment':{'text':event.get('transcript','')}})
                    continue
                order = event.get('turn_order')
                if type(order) is not int or order<0:
                    raise DeepgramError('Invalid AssemblyAI turn')
                if order in seen:
                    continue
                seen.add(order)
                rows = rows_for_words(event.get('words',[]),stream=stream,epoch=epoch,
                    base=checkpoint,sent=sent,language=event.get('language_code'),speaker=event.get('speaker_label'))
                if event.get('transcript','').strip() and not rows:
                    raise DeepgramError('AssemblyAI final turn has no timestamped words')
                turns[order] = {'rows':rows,'language':event.get('language_code')}
                if len(turns)>50000:
                    raise DeepgramError('AssemblyAI turn limit reached')
                end = max([confirmed]+[r['end_ms'] for r in rows])
                if any(r['start_ms']<confirmed for r in rows):
                    raise DeepgramError('Overlapping AssemblyAI final turn')
                emit({'type':'confirmed_window','stream':stream,'segments':rows,'end_ms':end})
                confirmed = end
                emit({'type':'transcription_progress','stream':stream,'mode':'streaming',
                    'recorded_ms':available,'sent_ms':sent,'confirmed_ms':end,
                    'send_lag_ms':max(0,available-sent),'lag_ms':max(0,sent-end)})


def run_streaming(audio,sid,emit,cancelled,done,*,config,budget,language='auto',language_hints=None,checkpoints=None,**_):
    if not config.api_key:
        raise DeepgramError('Configure your AssemblyAI API key in Preferences')
    language_options(language,language_hints)
    if done.is_set():
        for stream in STREAMS:
            report = audio.scan(sid,stream)
            checkpoint = (checkpoints or {}).get(stream,{}).get('confirmed_ms',0)
            if not report['complete'] or type(checkpoint) is not int or not 0<=checkpoint<=available_audio_ms(report):
                raise DeepgramError('Recover incomplete audio or invalid checkpoints before resume')
    failed = threading.Event()
    class Cancel:
        def is_set(self):
            return cancelled.is_set() or failed.is_set()
        def wait(self,seconds):
            return failed.wait(seconds) or cancelled.is_set()
    def track(stream):
        try:
            return _track(audio,sid,stream,emit,Cancel(),done,config=config,budget=budget,
                language=language,language_hints=language_hints,
                checkpoint=(checkpoints or {}).get(stream,{}).get('confirmed_ms',0))
        except Exception:
            failed.set()
            raise
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='assemblyai-track') as pool:
        futures = [pool.submit(track,s) for s in STREAMS]
        errors = []
        for future in futures:
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - drain both workers before raising
                errors.append(exc)
        if errors:
            raise next((e for e in errors if not isinstance(e,InterruptedError)),errors[0])


def run_batch(audio,sid,emit,cancelled,*,config,budget,language,language_hints=None):
    from .deepgram_batch import _frames, _wav
    if not config.api_key:
        raise DeepgramError('Configure your AssemblyAI API key in Preferences')
    # Authenticate every retained track before the first upload.
    for stream in STREAMS:
        if audio.path(sid,stream).is_file():
            with audio.cursor(sid,stream) as cursor:
                for _ in _frames(cursor,cancelled):
                    pass
    epoch = uuid4().hex
    found = False
    for stream in STREAMS:
        if not audio.path(sid,stream).is_file():
            continue
        with ExitStack() as stack:
            cursor = stack.enter_context(audio.cursor(sid,stream))
            for _ in _frames(cursor,cancelled):
                pass
            samples = cursor.aligned_end or 0
            if not samples:
                continue
            if samples>10_805*16000:
                raise DeepgramError('Saved audio duration is invalid')
            cursor.sequence = cursor.samples = 0
            cursor.end_ms = 0.
            cursor.aligned_end = None
            cursor.complete = False
            cursor.offset = len(cursor.header)
            found = True
            budget.reserve(stream,emit,cancelled,seconds=max(10,samples/16000))
            client = stack.enter_context(httpx.Client(timeout=httpx.Timeout(120,connect=10),trust_env=False,
                follow_redirects=False,transport=config.batch_transport,
                headers={'Authorization':config.api_key}))
            def request(method,path,client=client,**kwargs):
                if cancelled.is_set():
                    raise InterruptedError('AssemblyAI rerun cancelled')
                response = client.request(method,'https://api.assemblyai.com/v2/'+path,**kwargs)
                response.raise_for_status()
                if len(response.content)>32_000_000:
                    raise DeepgramError('AssemblyAI response exceeds transcript limit')
                return response.json()
            emit({'type':'transcription_progress','mode':'batch_upload','stream':stream})
            upload = request('POST','upload',content=_wav([cursor],samples,cancelled),
                headers={'Content-Type':'audio/wav','Content-Length':str(44+samples*2)})
            # Universal-2 supports the app's broad prerecorded language choices.
            payload = {'audio_url':upload['upload_url'],'speech_models':['universal-2'],'speaker_labels':True}
            if language in {'auto','multi'}:
                payload['language_detection'] = True
            else:
                payload['language_code'] = language
            job = request('POST','transcript',json=payload)
            jid = job['id']
            if not isinstance(jid,str) or not re.fullmatch('[a-zA-Z0-9-]{1,100}',jid):
                raise DeepgramError('Invalid AssemblyAI job ID')
            deadline = time.monotonic()+600
            while job.get('status') not in {'completed','error'}:
                emit({'type':'transcription_progress','mode':'batch_wait','stream':stream})
                if cancelled.wait(1):
                    raise InterruptedError('AssemblyAI rerun cancelled')
                if time.monotonic()>deadline:
                    raise DeepgramError('AssemblyAI prerecorded job timed out; no retry was made')
                job = request('GET','transcript/'+jid)
            if job['status'] == 'error':
                raise DeepgramError('AssemblyAI prerecorded transcription failed; no retry was made')
            for row in rows_for_words(job.get('words') or [],stream=stream,epoch=epoch,base=0,
                    sent=round(samples/16),language=job.get('language_code')):
                if cancelled.is_set():
                    raise InterruptedError('AssemblyAI rerun cancelled')
                emit(row)

    if not found:
        raise DeepgramError('No retained audio to transcribe')
