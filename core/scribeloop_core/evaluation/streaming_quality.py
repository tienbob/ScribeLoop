"""Bounded, paced recognition comparison; caller owns consent and durable attempts."""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter
from urllib.parse import urlencode

import numpy as np
from websockets.asyncio.client import connect

from ..async_compat import timeout
from ..pipeline.languages import validate_language_hints
from ..pipeline.transcription_quality import TranscriptionQuality
from .metrics import score_words


def score_verified(reference, result, *, clip_start_s=0):
    if reference.get('human_verified') is not True or not reference.get('text','').strip():
        return None
    if result.get('status') != 'completed':
        return None
    hypothesis=result['text']
    scope={}
    if 'start_s' in reference or 'end_s' in reference:
        start,end=reference.get('start_s'),reference.get('end_s')
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end))
                or not clip_start_s<=start<end<=clip_start_s+result['sent_audio_s']):
            raise ValueError('Verified phrase must be inside the tested audio interval')
        selected=[]
        for word in result.get('words',[]):
            a,b=(word.get('start'),word.get('end')) if result['provider']=='deepgram' else (
                word.get('start_ms'),word.get('end_ms'))
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)):
                continue
            midpoint=(a+b)/2/(1000 if result['provider']=='soniox' else 1)+clip_start_s
            if start<=midpoint<end:
                selected.append(word.get('punctuated_word',word.get('word','')) if result['provider']=='deepgram'
                                else word.get('text',''))
        hypothesis=('' if result['provider']=='soniox' else ' ').join(selected)
        scope={'scope':'verified_phrase','start_s':start,'end_s':end}
    score=score_words(reference['text'],hypothesis)
    return {'word_errors':score.errors,'reference_words':score.reference_words,'wer':score.wer,**scope}


async def stream_trial(provider, pcm, *, api_key, languages, vocabulary=(), connector=connect):
    """One connection, no retries, at most 60 seconds, no playback or disk audio."""
    options=TranscriptionQuality(vocabulary=list(vocabulary))
    languages=validate_language_hints(languages)
    pcm=np.asarray(pcm)
    if pcm.ndim != 1 or not 0 < len(pcm) <= 60*16000 or not np.isfinite(pcm).all():
        raise ValueError('Expected at most sixty seconds of finite mono 16 kHz PCM')
    if provider not in {'deepgram','soniox'} or not api_key:
        raise ValueError('Select a configured comparison provider')
    duration=len(pcm)/16000
    params={'model':'nova-3','language':'multi','language_hint':languages,'encoding':'linear16',
        'sample_rate':16000,'channels':1,'interim_results':'true','punctuate':'true',
        'endpointing':300,'diarize_model':'v1',**options.deepgram_options()}
    url=('wss://api.deepgram.com/v1/listen?'+urlencode(params,doseq=True) if provider=='deepgram'
         else 'wss://stt-rt.soniox.com/transcribe-websocket')
    headers={'Authorization':('Token ' if provider=='deepgram' else 'Bearer ')+api_key}
    result={'provider':provider,'status':'started','sent_audio_s':0}
    began=time.monotonic(); words=[]; texts=[]; received=0; finished=False
    try:
        async with timeout(duration+25):  # noqa: SIM117 - keep deadline separate from transport
            async with connector(url,additional_headers=headers,open_timeout=10,close_timeout=2,
                                 max_size=1_000_000,proxy=None) as ws:
                if provider=='soniox':
                    await ws.send(json.dumps({'model':'stt-rt-v5','audio_format':'pcm_s16le',
                        'sample_rate':16000,'num_channels':1,'language_hints':languages,
                        'language_hints_strict':True,'enable_language_identification':True,
                        'enable_speaker_diarization':True,
                        'context':{'terms':options.vocabulary}}))
                origin=time.monotonic()
                async def send():
                    for start in range(0,len(pcm),1600):
                        await asyncio.sleep(max(0,origin+start/16000-time.monotonic()))
                        block=(np.clip(pcm[start:start+1600],-1,1)*32767).astype('<i2')
                        await ws.send(block.tobytes())
                        result['sent_audio_s']=(start+len(block))/16000
                    await asyncio.sleep(max(0,origin+duration-time.monotonic()))
                    await ws.send(json.dumps({'type':'CloseStream'}) if provider=='deepgram' else '')
                sender=asyncio.create_task(send())
                try:
                    async for raw in ws:
                        received+=len(raw)
                        if received>4_000_000: raise ValueError('Comparison response too large')
                        event=json.loads(raw)
                        if event.get('error_code') or event.get('type')=='Error':
                            code=event.get('error_code')
                            if type(code) is int and 400<=code<=599:
                                result['provider_error_code']=code
                            category=event.get('error_type')
                            if category in {'organization_balance_exhausted','organization_monthly_budget_exhausted',
                                            'project_monthly_budget_exhausted','unauthenticated','permission_denied',
                                            'model_not_available','invalid_request'}:
                                result['provider_error_type']=category
                            raise RuntimeError('Provider rejected trial')
                        if provider=='deepgram':
                            if event.get('type')=='Metadata':
                                result['model_info']=event.get('model_info')
                                result['acknowledged_audio_s']=event.get('duration')
                                finished=abs(event.get('duration',-100)-duration)<=.1
                            if event.get('type')!='Results': continue
                            alt=event['channel']['alternatives'][0]
                            if alt.get('transcript') and 'first_text_s' not in result:
                                result['first_text_s']=round(time.monotonic()-origin,3)
                            if event.get('is_final'):
                                texts.append(alt.get('transcript','')); words.extend(alt.get('words',[]))
                        else:
                            tokens=event.get('tokens',[])
                            if any(t.get('text','').strip() for t in tokens) and 'first_text_s' not in result:
                                result['first_text_s']=round(time.monotonic()-origin,3)
                            for token in tokens:
                                if token.get('is_final') and not token.get('text','').startswith('<'):
                                    words.append(token); texts.append(token.get('text',''))
                            if event.get('finished'):
                                finished=True
                                result['acknowledged_audio_s']=event.get('total_audio_proc_ms',0)/1000
                                break
                    await sender
                finally:
                    sender.cancel(); await asyncio.gather(sender,return_exceptions=True)
        if not finished or result['sent_audio_s'] != duration:
            raise RuntimeError('Incomplete provider stream')
        text=(' '.join(texts) if provider=='deepgram' else ''.join(texts)).strip()
        result.update(status='completed',text=text,words=words,
            word_language_counts=dict(Counter(w.get('language','not_reported') for w in words)))
    except Exception as exc:  # noqa: BLE001 - redact all provider errors, credentials and transcript bodies
        result.update(status='failed',failure_type=type(exc).__name__)
    result['elapsed_s']=round(time.monotonic()-began,3)
    return result
