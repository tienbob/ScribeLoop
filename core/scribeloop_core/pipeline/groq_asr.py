"""Explicit Groq cloud transcription of bounded in-memory audio.

No implicit retries, model downloads, local inference, or configurable third-party
endpoint. Callers own cloud consent and input selection. Secrets never enter logs.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

ENDPOINT = 'https://api.groq.com/openai/v1/audio/transcriptions'
MODELS = {'whisper-large-v3-turbo':.04, 'whisper-large-v3':.111}
MAX_RESPONSE = 4_000_000


class GroqError(RuntimeError):
    pass


def load_groq_key(path=Path('.env')):
    """Read one literal assignment, never evaluate/source a credential file."""
    value = os.environ.get('GROQ_API_KEY','').strip()
    if not value and Path(path).is_file():
        for line in Path(path).read_text().splitlines():
            line = line.strip().removeprefix('export ')
            name, separator, candidate = line.partition('=')
            if separator and name.strip() == 'GROQ_API_KEY':
                value = candidate.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"',"'"}:
                    value = value[1:-1]
    if not 16 <= len(value) <= 512 or not value.isascii() or any(c.isspace() for c in value):
        raise GroqError('Set GROQ_API_KEY locally in the environment or git-ignored .env; never paste it into chat.')
    return value


def transcribe_groq(pcm, *, api_key, model='whisper-large-v3-turbo', language='en', client=None):
    if model not in MODELS or language not in {'en','vi','auto'}:
        raise ValueError('Unsupported Groq model or language')
    if not isinstance(api_key,str) or not api_key.strip() or any(c.isspace() for c in api_key):
        raise GroqError('A locally configured Groq key is required')
    pcm = np.asarray(pcm)
    if (pcm.ndim != 1 or pcm.dtype != np.dtype(np.float32) or not 0 < pcm.size <= 300*16000
            or not np.isfinite(pcm).all()):
        raise ValueError('Expected at most five minutes of mono float32 16 kHz audio')
    payload = io.BytesIO()
    sf.write(payload,pcm,16000,format='FLAC',subtype='PCM_16')
    data = {'model':model,'response_format':'verbose_json','temperature':'0',
            'timestamp_granularities[]':'segment'}
    if language != 'auto':
        data['language'] = language
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(90,connect=10),follow_redirects=False,trust_env=False)
    try:
        # stream the response to impose an actual byte limit before JSON parsing.
        with client.stream('POST',ENDPOINT,headers={'Authorization':'Bearer '+api_key},
                           data=data,files={'file':('public-test.flac',payload.getvalue(),'audio/flac')}) as response:
            if response.status_code != 200:
                messages = {401:'Groq rejected the API key.',403:'Groq access was denied.',
                    413:'Groq rejected the audio size.',429:'Groq free quota/rate limit reached. No retry was made.'}
                raise GroqError(messages.get(response.status_code,
                    f'Groq returned HTTP {response.status_code}; no retry was made.'))
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    raise GroqError('Groq response exceeded the safety bound.')
        result = json.loads(raw)
        text = result.get('text')
        if not isinstance(text,str) or len(text) > 500000:
            raise GroqError('Groq returned an invalid transcript.')
        segments = []
        duration_ms = round(pcm.size/16)
        for row in result.get('segments',[]):
            start,end = float(row['start']),float(row['end'])
            if not np.isfinite([start,end]).all() or not 0 <= start < end or not isinstance(row['text'],str):
                raise GroqError('Groq returned invalid segment metadata.')
            a,b = round(start*1000),min(duration_ms,round(end*1000))
            if b > a and row['text'].strip():
                segments.append({'start_ms':a,'end_ms':b,'text':row['text'].strip(),
                                 'language':result.get('language'),'is_final':True})
        return {'text':text,'segments':segments,'language':result.get('language'),
                'audio_s':pcm.size/16000,'upload_bytes':len(payload.getbuffer()),'model':model}
    except httpx.HTTPError as exc:
        raise GroqError(f'Network request to Groq failed ({type(exc).__name__}); no retry or provider fallback was made.') from None
    except (KeyError,TypeError,ValueError):
        raise GroqError('Groq returned malformed transcription data.') from None
    finally:
        if owned:
            client.close()
