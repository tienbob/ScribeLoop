"""One prerecorded request for saved audio; bounded PCM, no plaintext disk file."""
from __future__ import annotations

import json
import struct
from contextlib import ExitStack
from uuid import uuid4

import httpx
import numpy as np

from ..capture.source import SAMPLE_RATE, sample_position
from .deepgram_meeting import STREAMS, DeepgramError, speaker_rows
from .languages import deepgram_language_options


def _frames(cursor, cancelled):
    while not cursor.complete:
        if cancelled.is_set():
            raise InterruptedError('Batch transcription cancelled')
        frames, report = cursor.read(max_frames=1)
        yield from frames
        if report['caught_up'] and not report['complete']:
            raise DeepgramError('Recover incomplete saved audio before retranscription')


def _blocks(cursor, samples, cancelled):
    frames = iter(_frames(cursor, cancelled))
    frame = next(frames, None)
    for start in range(0, samples, SAMPLE_RATE):
        end = min(samples, start+SAMPLE_RATE)
        pcm = np.zeros(end-start, dtype=np.float32)
        while frame is not None:
            a = sample_position(frame)
            b = a+len(frame.pcm)
            if a >= end:
                break
            lo, hi = max(start,a), min(end,b)
            if hi > lo:
                pcm[lo-start:hi-start] = frame.pcm[lo-a:hi-a]
            if b > end:
                break
            frame = next(frames, None)
        yield (np.clip(pcm,-1,1)*32767).astype('<i2')
    if frame is not None or next(frames, None) is not None:
        raise DeepgramError('Saved audio changed during upload')


def _wav(cursors, samples, cancelled):
    channels = len(cursors)
    size = samples*channels*2
    yield struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF',36+size,b'WAVE',b'fmt ',16,
                      1,channels,SAMPLE_RATE,SAMPLE_RATE*channels*2,channels*2,16,b'data',size)
    blocks = [_blocks(cursor,samples,cancelled) for cursor in cursors]
    for parts in zip(*blocks, strict=True):
        yield np.stack(parts,axis=1).astype('<i2',copy=False).tobytes()


def run_batch(audio,sid,emit,cancelled,*,config,budget,language):
    language = 'multi' if language == 'auto' else language
    streams = [s for s in STREAMS if audio.path(sid,s).is_file()]
    if not streams:
        raise DeepgramError('No retained audio to transcribe')
    with ExitStack() as stack:
        cursors = [stack.enter_context(audio.cursor(sid,s)) for s in streams]
        # Validate all complete files before authorizing an upload. Keep each key
        # inside its cursor for both passes; no per-chunk Keychain access.
        for cursor in cursors:
            for _frame in _frames(cursor,cancelled):
                pass
        samples = max(c.aligned_end or 0 for c in cursors)
        if not 0 < samples <= 10_805*SAMPLE_RATE:
            raise DeepgramError('Saved audio duration is invalid')
        for cursor in cursors:
            cursor.sequence = cursor.samples = 0
            cursor.end_ms = 0.
            cursor.aligned_end = None
            cursor.complete = False
            cursor.offset = len(cursor.header)
        if cancelled.is_set():
            raise InterruptedError('Batch transcription cancelled')
        budget.reserve('multichannel',emit,cancelled,seconds=samples/SAMPLE_RATE*len(streams))
        emit({'type':'transcription_progress','mode':'batch_upload','stream':'all'})
        params = {'model':'nova-3',**deepgram_language_options(language),'multichannel':'true',
                  'diarize_model':'v1','smart_format':'true','mip_opt_out':'true'}
        headers = {'Authorization':'Token '+config.api_key,'Content-Type':'audio/wav',
                   'Content-Length':str(44+samples*len(streams)*2)}
        def content():
            yield from _wav(cursors,samples,cancelled)
            emit({'type':'transcription_progress','mode':'batch_wait','stream':'all'})
        client = stack.enter_context(httpx.Client(timeout=httpx.Timeout(120,connect=10),
            trust_env=False,follow_redirects=False,transport=config.batch_transport))
        with client.stream('POST','https://api.deepgram.com/v1/listen',params=params,
                           headers=headers,content=content()) as response:
            response.raise_for_status()
            raw = bytearray()
            for block in response.iter_bytes():
                if cancelled.is_set():
                    raise InterruptedError('Batch transcription cancelled')
                raw.extend(block)
                if len(raw) > 32_000_000:
                    raise DeepgramError('Batch response exceeds transcript limit')
        value = json.loads(raw)
        channels = value['results']['channels']
        if len(channels) != len(streams):
            raise DeepgramError('Batch response channel count does not match recording')
        epoch, rows = uuid4().hex, []
        for stream,channel in zip(streams,channels,strict=True):
            rows.extend(speaker_rows({'channel':channel,'start':0,'duration':samples/SAMPLE_RATE},
                stream=stream,epoch=epoch,base_ms=0,sent_ms=round(samples*1000/SAMPLE_RATE),
                # Prerecorded word ends can slightly exceed the cut file (85 ms
                # observed). Clamp inside sent audio; never permit outside starts.
                language=language,end_tolerance_ms=100))
        if cancelled.is_set():
            raise InterruptedError('Batch transcription cancelled')
        emit({'type':'language_locked','language':language,
              'mode':'multilingual' if language=='multi' else 'manual',
              'confidence':None,'sample_s':0})
        for row in sorted(rows,key=lambda r:(r['start_ms'],r['stream'])):
            emit(row)
