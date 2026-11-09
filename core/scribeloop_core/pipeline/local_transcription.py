"""Local Ollama transcription: auto-prepared server, batch-only after recording.

The Ollama server is probed, started, and the requested model pulled and loaded
while capture runs; transcription itself starts only after capture completes.
Everything stays on loopback, no API key is involved, and no audio, model or
transcript file is written to disk. Server diagnostics are sanitized: failures
carry app-authored messages, never provider bodies.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import ipaddress
import json
import logging
import math
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import numpy as np
import soundfile as sf

from ..capture.source import SAMPLE_RATE, AudioFrame, sample_position
from ..llm.ollama_lifecycle import KEEP_ALIVE, release_if_small
from .recording import RecordingRunner

log = logging.getLogger(__name__)

STREAMS = ('loopback','mic')
DEFAULT_ENDPOINT = 'http://localhost:11434/v1'
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,60}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,60}){0,2}'
                   '(?::[A-Za-z0-9][A-Za-z0-9._-]{0,60})?$')
WINDOW_SAMPLES = 30*SAMPLE_RATE  # Bound coarse timestamps when the server returns text only.
MAX_RESPONSE = 4_000_000
READY_TIMEOUT_S = 60.
PROBE_TIMEOUT_S = 2.
CLI_PATHS = (Path('/usr/local/bin/ollama'), Path('/opt/homebrew/bin/ollama'))


class LocalTranscriptionError(RuntimeError):
    """App-authored local setup/transport failure; never contains a server body."""


def _loopback(hostname):
    try:
        return ipaddress.ip_address(hostname or '').is_loopback
    except ValueError:
        return hostname == 'localhost'


def _api_root(config):
    return config.base_url.removesuffix('/v1')


def _diarization_available():
    """Best-effort capability probe without importing heavy optional packages."""
    import importlib.util
    for name in ('pyannote','torch'):
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


class LocalTranscriptionConfig:
    """Loopback-only OpenAI-compatible transcription endpoint (Ollama)."""

    provider = 'local-ollama'
    cloud = False

    def __init__(self, *, model=None, endpoint=None, speaker_labels=False, client_factory=None, cleanup_provider=None):
        self.model = (model or '').strip()
        self.base_url = (endpoint or DEFAULT_ENDPOINT).rstrip('/')
        self.speaker_labels = bool(speaker_labels)
        self.cleanup_provider = cleanup_provider
        self.client_factory = client_factory or httpx.Client
        self.configured = False
        try:
            url = urlsplit(self.base_url)
            loopback = _loopback(url.hostname)
            valid = (url.scheme in {'https','http'} and loopback and
                     not url.username and not url.password and
                     not url.query and not url.fragment and bool(MODEL.fullmatch(self.model)) and
                     'cloud' not in self.model.rsplit(':',1)[-1].lower() and
                     not any(ord(c) < 32 for c in self.base_url+self.model))
            _ = url.port  # Reject malformed ports.
            self.configured = bool(valid)
        except ValueError:
            pass

    def public(self):
        return {'provider':'ollama','cloud':False,'live':False,'runs_after_recording':True,
                'model':self.model,'endpoint':self.base_url,'configured':self.configured,
                'speaker_labels':self.speaker_labels,'updates':'after the recording ends',
                'speaker_labels_available':_diarization_available()}



def _probe(config):
    with contextlib.suppress(Exception), config.client_factory(
            timeout=httpx.Timeout(PROBE_TIMEOUT_S,connect=1),
            trust_env=False,follow_redirects=False) as client:
        if client.get(_api_root(config)+'/api/version').status_code == 200:
            return True
    return False


def _launch_server(cancelled):
    """Start the installed Ollama app or CLI server; fail closed when absent."""
    if cancelled.is_set():
        raise InterruptedError('Ollama preparation cancelled')
    for candidate in (Path('/Applications/Ollama.app'), Path.home()/'Applications/Ollama.app'):
        if candidate.is_dir():
            try:
                subprocess.run(['open','-a','Ollama'],timeout=10,check=True,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                return
            except (OSError,subprocess.SubprocessError):
                break
    for cli in CLI_PATHS:
        if cli.is_file():
            try:
                subprocess.Popen([str(cli),'serve'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL,start_new_session=True)
                return
            except OSError:
                break
    raise LocalTranscriptionError('Ollama is not installed. Install it from ollama.com and try again.')


def _await_ready(config,cancelled,timeout_s):
    began = time.monotonic()
    while True:
        if cancelled.is_set():
            raise InterruptedError('Ollama preparation cancelled')
        if _probe(config):
            return
        if time.monotonic()-began > timeout_s:
            raise LocalTranscriptionError('Ollama did not become ready within 60 seconds. Check the Ollama app or server logs.')
        time.sleep(.5)




def _installed(config,cancelled):
    try:
        with config.client_factory(timeout=httpx.Timeout(5,connect=2),
                                   trust_env=False,follow_redirects=False) as client:
            response = client.get(_api_root(config)+'/api/tags')
    except httpx.HTTPError as exc:
        raise LocalTranscriptionError(f'Ollama did not report its models ({type(exc).__name__}).') from None
    if response.status_code != 200:
        raise LocalTranscriptionError('Ollama did not report its models; check the server.')
    try:
        payload = response.json()
        names = {row.get('name','') for row in payload.get('models',[]) if isinstance(row,dict)}
    except ValueError:
        raise LocalTranscriptionError('Ollama reported malformed model metadata.') from None
    return config.model in names or config.model+':latest' in names


def _pull(config,emit,cancelled):
    try:
        with config.client_factory(timeout=httpx.Timeout(30,connect=2),
                                   trust_env=False,follow_redirects=False) as client, client.stream(
                'POST',_api_root(config)+'/api/pull',json={'name':config.model,'stream':True}) as response:
                if response.status_code != 200:
                    raise LocalTranscriptionError(
                        f'Ollama rejected the model download (HTTP {response.status_code}).')
                for line in response.iter_lines():
                    if cancelled.is_set():
                        raise InterruptedError('Ollama preparation cancelled')
                    if not line:
                        continue
                    try:
                        report = json.loads(line)
                    except ValueError:
                        continue
                    if report.get('error'):
                        raise LocalTranscriptionError(
                            f"Model '{config.model}' is not available in the Ollama library. Check the model name.")
                    total,completed = report.get('total'),report.get('completed')
                    percent = (round(100*completed/total,1)
                               if isinstance(total,int) and isinstance(completed,int) and total > 0 else None)
                    status = report.get('status')
                    emit({'state':'pulling','status':status if isinstance(status,str) else '',
                          'percent':percent})
    except httpx.HTTPError as exc:
        raise LocalTranscriptionError(f'Ollama model download failed ({type(exc).__name__}).') from None


def _warm(config,cancelled):
    """One tiny silent transcription validates the endpoint and loads the model."""
    transcribe_local(np.zeros(1600,dtype=np.float32),config=config,language='en',cancelled=cancelled)


def _persist_warm(config,cancelled):
    """Hold the warmed model for the idle window; small models unload at task end.

    The compatibility transcription endpoint cannot carry keep_alive, so the
    residency policy is (re)applied with one native empty-prompt request.
    """
    if cancelled.is_set():
        return False
    try:
        with config.client_factory(timeout=httpx.Timeout(10,connect=2),
                                   trust_env=False,follow_redirects=False) as client:
            response = client.post(_api_root(config)+'/api/generate',
                json={'model':config.model,'keep_alive':KEEP_ALIVE})
        return response.status_code == 200
    except (httpx.HTTPError,OSError):
        return False


def _validate_audio_model(config):
    """A local server can proxy cloud models; reject those before sending audio."""
    try:
        with config.client_factory(timeout=httpx.Timeout(5,connect=2),
                                   trust_env=False,follow_redirects=False) as client:
            response = client.post(_api_root(config)+'/api/show',json={'model':config.model})
        if response.status_code != 200:
            raise LocalTranscriptionError('Ollama could not inspect this model. Check the model name and update Ollama.')
        value = response.json()
        if not isinstance(value,dict):
            raise ValueError  # noqa: TRY004 - malformed provider response follows the sanitized value-error path
        if value.get('remote_host') or value.get('remote_model'):
            raise LocalTranscriptionError('Choose a downloaded on-device model, not an Ollama cloud model.')
        if 'audio' not in (value.get('capabilities') or []):
            raise LocalTranscriptionError('This model cannot accept audio. Choose an audio model such as gemma4:e2b.')
    except (httpx.HTTPError,ValueError):
        raise LocalTranscriptionError('Ollama model capabilities could not be verified. Update Ollama and check the model.') from None


def ensure_ollama(config,emit,cancelled):
    """Idempotent lifecycle: probe → launch if down → pull if missing → warm-load."""
    if not config.configured:
        raise LocalTranscriptionError('Configure the Ollama endpoint and transcription model in Preferences.')
    emit({'state':'checking','status':'Looking for Ollama…','percent':None})
    if not _probe(config):
        emit({'state':'starting','status':'Starting Ollama…','percent':None})
        _launch_server(cancelled)
        _await_ready(config,cancelled,READY_TIMEOUT_S)
    emit({'state':'checking','status':'Checking the transcription model…','percent':None})
    if not _installed(config,cancelled):
        emit({'state':'pulling','status':f"Downloading {config.model}…",'percent':None})
        _pull(config,emit,cancelled)
        emit({'state':'checking','status':'Verifying the downloaded model…','percent':None})
        if not _installed(config,cancelled):
            raise LocalTranscriptionError('Ollama did not report the downloaded model; check the server.')
    emit({'state':'loading','status':'Loading the transcription model…','percent':None})
    _validate_audio_model(config)
    _warm(config,cancelled)
    with contextlib.suppress(Exception):
        _persist_warm(config,cancelled)
    emit({'state':'ready','status':f'Ollama is ready with {config.model}.','percent':None})




async def ensure_model_ready(config,publish,*,session_id=None,cancelled=None):
    """Off-loop Ollama preparation with status events; returns 'ready'/'unavailable'."""
    events = queue.Queue(maxsize=64)
    stop = threading.Event()
    cancelled = threading.Event() if cancelled is None else cancelled
    def emit(event):
        while not stop.is_set():
            try:
                events.put(event,timeout=.1)
                return
            except queue.Full:
                continue
    worker = asyncio.create_task(asyncio.to_thread(ensure_ollama,config,emit,cancelled))
    try:
        while not worker.done() or not events.empty():
            try:
                event = events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(.05)
                continue
            await publish({'type':'local_model_status','session_id':session_id,**event})
        await worker
        return 'ready'
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except Exception as exc:  # noqa: BLE001 - app-authored diagnostics only
        # A failed local setup is not a user cancellation: never touch the
        # caller's cancellation signal, only report the outcome.
        message = ('Local model preparation was interrupted.' if isinstance(exc,InterruptedError) or cancelled.is_set()
                   else str(exc) if isinstance(exc,LocalTranscriptionError)
                   else 'Ollama is unavailable. Start it manually or install it from ollama.com.')
        await publish({'type':'local_model_status','session_id':session_id,'state':'unavailable','status':message,'message':message})
        return 'unavailable'
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await worker




def transcribe_local(pcm, *, config, language='auto', cancelled=None, client=None):
    """One bounded verbose_json request against an OpenAI-compatible local server."""
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError('Local transcription cancelled')
    if not config.configured:
        raise LocalTranscriptionError('Configure the Ollama endpoint and transcription model in Preferences.')
    pcm = np.asarray(pcm)
    if (pcm.ndim != 1 or pcm.dtype != np.dtype(np.float32) or not 0 < pcm.size <= WINDOW_SAMPLES
            or not np.isfinite(pcm).all()):
        raise ValueError('Expected at most 30 seconds of mono float32 16 kHz audio')
    payload = io.BytesIO()
    sf.write(payload,pcm,SAMPLE_RATE,format='WAV',subtype='PCM_16')
    # Ollama does not support timestamp_granularities. Current versions may
    # return plain text without segments even for verbose_json.
    data = {'model':config.model,'response_format':'verbose_json','temperature':'0'}
    if language != 'auto':
        data['language'] = language
    owned = client is None
    client = client or config.client_factory(timeout=httpx.Timeout(300,connect=5),
                                             trust_env=False,follow_redirects=False)
    try:
        with client.stream('POST',config.base_url+'/audio/transcriptions',
                           data=data,files={'file':('audio.wav',payload.getvalue(),'audio/wav')}) as response:
            if response.status_code != 200:
                messages = {400:'The selected Ollama model did not accept this audio; make sure it is a transcription model.',
                            404:'The Ollama transcription endpoint or model was not found; check the model name in Preferences.',
                            500:'Ollama could not transcribe this audio; the encrypted recording is retained.'}
                raise LocalTranscriptionError(messages.get(response.status_code,
                    f'Ollama returned HTTP {response.status_code}; no retry was made.'))
            raw = bytearray()
            for block in response.iter_bytes():
                if cancelled is not None and cancelled.is_set():
                    raise InterruptedError('Local transcription cancelled')
                raw.extend(block)
                if len(raw) > MAX_RESPONSE:
                    raise LocalTranscriptionError('Ollama response exceeded the safety bound.')
        value = json.loads(raw)
        text = value.get('text')
        if not isinstance(text,str) or len(text) > 500_000:
            raise LocalTranscriptionError('Ollama returned an invalid transcript.')
        # Qwen ASR can expose its language header through the compatible API.
        # Strip only its anchored protocol form; preserve ordinary spoken text.
        header = re.compile(r'^\s*language\s+([A-Za-z][A-Za-z -]{0,60})<asr_text>')
        match = header.match(text)
        detected_language = match.group(1).strip().lower() if match else None
        text = header.sub('',text,count=1)
        window_ms = round(pcm.size*1000/SAMPLE_RATE)
        segments = []
        for row in value.get('segments') or []:
            start,end = float(row['start']),float(row['end'])
            if (not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
                    or not isinstance(row.get('text'),str)):
                raise LocalTranscriptionError('Ollama returned invalid segment metadata.')
            a,b = round(start*1000),min(window_ms,round(end*1000))
            stripped = header.sub('',row['text'],count=1).strip()
            if b > a and stripped:
                segments.append({'start_ms':a,'end_ms':b,'text':stripped})
        if not segments and text.strip():
            segments.append({'start_ms':0,'end_ms':window_ms,'text':text.strip()})
        reported = value.get('language')
        if not reported and detected_language:
            reported = {'english':'en','vietnamese':'vi'}.get(detected_language,detected_language)
        return {'text':text,'segments':segments,
                'language':reported if isinstance(reported,str) else None,
                'audio_s':pcm.size/SAMPLE_RATE,'upload_bytes':len(payload.getbuffer()),'model':config.model}
    except httpx.HTTPError as exc:
        raise LocalTranscriptionError(f'Local transcription request failed ({type(exc).__name__}); no retry was made.') from None
    except (KeyError,TypeError,ValueError):
        raise LocalTranscriptionError('Ollama returned malformed transcription data.') from None
    finally:
        if owned:
            client.close()




class LocalRecordingRunner(RecordingRunner):
    """Batch transcription after capture ends; no live transcript while recording."""

    supported_languages = frozenset({'auto','en','vi'})
    provider = 'local-ollama'

    def __init__(self,store,audio,source_factory,publish, *, config=None,
                 diarizer_factory=None, **kwargs):
        self.config = config or LocalTranscriptionConfig()
        self._diarizer_factory = diarizer_factory
        self._turn_source = []
        self._turn_next = 1
        self._labels_unavailable = False
        self._prepare_task = None
        self._prepare_done = threading.Event()
        self._prepare_state = 'ready'  # No preparation is pending until start().
        self._prepare_message = None
        super().__init__(store,audio,source_factory,publish,transcriber=self._batch,
                         live_transcriber=None,diarizer=False,finalize_transcript=False,**kwargs)

    @property
    def cloud(self):
        return self.config

    def start(self,selection,metadata,title, *, consent_confirmed):
        language = metadata.get('language','auto')
        if language not in self.supported_languages:
            raise ValueError('Local Ollama transcription supports Auto, English or Vietnamese')
        if not self.config.configured:
            raise ValueError('Configure the Ollama endpoint and transcription model in Preferences.')
        session = super().start(selection,{**metadata,'provider':'local-ollama','model':self.config.model,
            'engine':'ollama','cloud_consent':False,
            'local_speaker_labels':self.config.speaker_labels},title,
            consent_confirmed=consent_confirmed)
        self._labels_unavailable = False
        self._prepare_done.clear()
        self._prepare_state = 'pending'
        self._prepare_message = None
        self._prepare_task = asyncio.create_task(self._prepare(session['id']))
        return session

    def resume(self,sid,*,language='auto',cloud_consent=False):
        session = self._saved(sid)
        if session['status'] == 'deleting':
            raise ValueError('Cannot resume this meeting')
        if language not in self.supported_languages:
            raise ValueError('Local Ollama transcription supports Auto, English or Vietnamese')
        if self.store.segments(sid) and self.store.transcript_provider(sid) not in {None,'local-ollama'}:
            raise ValueError('Saved transcript uses another provider; choose Transcribe again to replace it')
        return self.reprocess(sid,language=language)

    def reprocess(self,sid,*,language,cloud_consent=False):
        if language not in self.supported_languages:
            raise ValueError('Local Ollama transcription supports Auto, English or Vietnamese')
        if not self.config.configured:
            raise ValueError('Configure the Ollama endpoint and transcription model in Preferences.')
        self._saved(sid)
        return super().reprocess(sid,language=language)

    async def close(self):
        await super().close()
        task,self._prepare_task = self._prepare_task,None
        if task is not None:
            with contextlib.suppress(Exception):
                await task

    async def _finish(self, session_id):
        provider = self.config.cleanup_provider
        if provider is not None and not self.closed and not self.cancel_processing.is_set():
            from ..llm.transcript_cleanup import apply_cleanup
            await apply_cleanup(self.store,session_id,provider,asr_model=self.config.model,
                publish=self.publish,cancelled=self.cancel_processing)
        await super()._finish(session_id)
        # The transcription task is over: free small models immediately; larger
        # ones remain for the idle window and the shutdown release.
        with contextlib.suppress(Exception):
            await release_if_small(self.config.base_url,self.config.model)

    async def _prepare(self,session_id):
        # Preparation overlaps capture: probe/start/pull/load while the user talks.
        state = await ensure_model_ready(self.config,self.publish,session_id=session_id,
                                         cancelled=self.cancel_processing)
        self._prepare_state = state
        self._prepare_done.set()

    async def _process(self,session_id,*,replace=False,live=False,finalize=True):
        if self.config.speaker_labels and not replace:
            turns = self.store.turns(session_id)
            self._turn_source = turns
            numbers = []
            for turn in turns:
                label = turn.get('label') or ''
                if label.startswith('Speaker '):
                    with contextlib.suppress(ValueError,IndexError):
                        numbers.append(int(label.split()[-1]))
            self._turn_next = max(numbers,default=0)+1
        try:
            return await super()._process(session_id,replace=replace,live=live,finalize=finalize)
        finally:
            if self._labels_unavailable and not replace and not self.cancel_processing.is_set():
                self._labels_unavailable = False
                await self.publish({'type':'speaker_warning','session_id':session_id,
                    'message':'Speaker labels are unavailable for this transcript. The transcript was saved without labels; try Transcribe again when the diarization model is available.'})




    def _await_preparation(self,emit,cancelled):
        if self._prepare_task is None:
            ensure_ollama(self.config,
                lambda event: emit({'type':'transcription_progress','stream':'all',
                                    'mode':'preparing_ollama',**event}),cancelled)
            return
        while not self._prepare_done.wait(.1):
            if cancelled.is_set():
                return
        if self._prepare_state != 'ready':
            raise LocalTranscriptionError(self._prepare_message or 'Ollama transcription is unavailable')

    def _batch(self,audio,sid,emit,cancelled):
        """Post-capture transcription: authenticate both tracks, then bounded windows."""
        self._await_preparation(emit,cancelled)
        if cancelled.is_set():
            return
        streams = [stream for stream in STREAMS if audio.path(sid,stream).is_file()]
        if not streams:
            raise ValueError('No retained audio to transcribe')
        ends = {}
        for stream in streams:
            end_ms = 0
            for frame in audio.frames(sid,stream):
                if cancelled.is_set():
                    return
                end_ms = max(end_ms,frame.t_ms+len(frame.pcm)//16)
            ends[stream] = end_ms
            emit({'type':'recorded_checkpoint','stream':stream,'end_ms':end_ms})
        if cancelled.is_set():
            return
        turns = self._turn_source
        if self.config.speaker_labels and 'loopback' in streams and not turns:
            turns = self._speaker_turns(audio,sid,emit,cancelled)
            self._turn_source = turns
        if self.language != 'auto':
            emit({'type':'language_locked','language':self.language,'mode':'manual',
                  'confidence':None,'sample_s':0})
        keys,epoch,lock = {},uuid4().hex,{'language':None}
        for stream in streams:
            self._transcribe_track(audio,sid,stream,ends[stream],turns,keys,epoch,lock,emit,cancelled)
            if cancelled.is_set():
                return

    def _transcribe_track(self,audio,sid,stream,end_ms,turns,keys,epoch,lock,emit,cancelled):
        frames,start,total = [],None,0
        def flush():
            nonlocal frames,start
            if frames and not cancelled.is_set():
                self._transcribe_window(sid,stream,round(start*1000/SAMPLE_RATE),
                    round(total*1000/SAMPLE_RATE),frames,turns,keys,epoch,lock,emit,cancelled)
            frames,start = [],None
        for frame in audio.frames(sid,stream):
            if cancelled.is_set():
                return
            position = sample_position(frame)
            offset = 0
            while offset < len(frame.pcm):
                if cancelled.is_set():
                    return
                # Bound the timeline span, not just the number of stored samples:
                # saved device gaps become silence in the request. A gap beyond
                # this window starts a new one without allocating the whole gap.
                if start is not None and position >= start+WINDOW_SAMPLES:
                    flush()
                if start is None:
                    start = position
                count = min(len(frame.pcm)-offset,start+WINDOW_SAMPLES-position)
                frames.append(AudioFrame(frame.pcm[offset:offset+count],stream,
                    round(position*1000/SAMPLE_RATE),frame.wall_ts,position))
                offset += count
                position += count
                total = position
                if total == start+WINDOW_SAMPLES:
                    flush()
        flush()
        if cancelled.is_set():
            return
        emit({'type':'transcription_progress','stream':stream,'mode':'local_batch',
              'recorded_ms':end_ms,'confirmed_ms':end_ms,'lag_ms':0})



    def _transcribe_window(self,sid,stream,start_ms,stop_ms,frames,turns,keys,epoch,lock,emit,cancelled):
        pcm = np.zeros(round((stop_ms-start_ms)*SAMPLE_RATE//1000),dtype=np.float32)
        for frame in frames:
            offset = sample_position(frame)-round(start_ms*SAMPLE_RATE//1000)
            lo,hi = max(0,offset),min(len(pcm),offset+len(frame.pcm))
            if hi > lo:
                pcm[lo:hi] = frame.pcm[lo-offset:hi-offset]
        if np.max(np.abs(pcm),initial=0) <= 1e-5:
            return
        result = transcribe_local(pcm,config=self.config,language=self.language,cancelled=cancelled)
        if lock['language'] is None and result.get('language') in {'en','vi'}:
            lock['language'] = result['language']
            emit({'type':'language_locked','language':lock['language'],'mode':'auto',
                  'confidence':None,'sample_s':round(pcm.size/SAMPLE_RATE,3)})
        for segment in result['segments']:
            begin,end = max(0,start_ms+segment['start_ms']),min(stop_ms,start_ms+segment['end_ms'])
            if end <= begin:
                continue
            row = {'stream':stream,'start_ms':begin,'end_ms':end,'text':segment['text'],
                   'is_final':True,'language':lock['language'],'confidence':None}
            key = self._speaker_key(row,turns,keys,epoch,stream)
            if key:
                row['speaker_key'] = key
            emit(row)
        emit({'type':'transcription_progress','stream':stream,'mode':'local_batch',
              'recorded_ms':stop_ms,'confirmed_ms':stop_ms,'lag_ms':0})

    def _speaker_key(self,row,turns,keys,epoch,stream):
        if not turns:
            return None
        if stream == 'mic':
            return f'ollama:{epoch}:mic:{keys.setdefault("mic",1)}'
        from .diar import attribution
        name = attribution(row,turns)
        if not name or not name.startswith('Speaker '):
            return None
        number = keys.get(name)
        if number is None:
            try:
                number = int(name.split()[-1])
            except ValueError:
                return None
            keys[name] = number
        return f'ollama:{epoch}:{stream}:{number}'

    def _speaker_turns(self,audio,sid,emit,cancelled):
        """Diarize the retained loopback track; identities stay anonymous."""
        from .diar import NearOnlineChunked, diarize_live
        turns = []
        def collect(event):
            turns.extend(turn for turn in event['turns'] if turn.get('label'))
            emit({'type':'transcription_progress','stream':'loopback','mode':'speaker_diarization',
                  'recorded_ms':round(event['end_ms']),'confirmed_ms':round(event['end_ms']),'lag_ms':0})
        emit({'type':'transcription_progress','stream':'loopback','mode':'speaker_diarization',
              'recorded_ms':0,'confirmed_ms':0,'lag_ms':0})
        try:
            diarize_live(audio,sid,collect,cancelled,self.capture_done,checkpoint=0,
                         backend_factory=self._diarizer_factory or NearOnlineChunked,
                         next_label=self._turn_next)
        except Exception as exc:  # noqa: BLE001 - labels are optional; transcription is not
            if not cancelled.is_set():
                log.warning('Local diarization unavailable (%s)',type(exc).__name__)
                self._labels_unavailable = True
            return []
        if turns and not cancelled.is_set():
            emit({'type':'speaker_turns_saved','stream':'loopback','turns':turns,
                  'end_ms':max(turn['end_ms'] for turn in turns)})
        return turns

