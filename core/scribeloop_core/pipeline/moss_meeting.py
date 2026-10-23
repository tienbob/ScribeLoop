"""Explicit local Q6_K transcription after capture, without cloud fallback."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import numpy as np

from .cloud_meeting import pcm_window
from .recording import RecordingRunner

ROOT = Path(__file__).resolve().parents[3]
MODEL_SHA256 = '7fd37409dbec9c732f912717f3cd8f4cd2c5083841edfab2f3b670f816512b44'


class MossConfig:
    def __init__(self, *, runtime=None, model=None, request=None):
        self.runtime = Path(runtime or os.environ.get('MOSS_RUNTIME_DIR',ROOT/'models/transcribe.cpp')).resolve()
        self.model = Path(model or os.environ.get('MOSS_MODEL_PATH',ROOT/'models/MOSS-Transcribe-Diarize-Q6_K.gguf')).resolve()
        self.request = request or self.infer

    def public(self):
        library = self.runtime/'build/src'/('libtranscribe.dylib' if sys.platform == 'darwin' else 'libtranscribe.so')
        ready = self.model.is_file() and library.is_file() and (self.runtime/'bindings/python/src/transcribe_cpp').is_dir()
        return {'cloud':False,'configured':ready,'provider':'local-moss','model':'MOSS Q6_K',
            'streaming':False,'speaker_separation':True,'languages':{'auto':'Auto · English / Chinese','en':'English','zh':'Chinese'},
            'default_language':'auto','auto_language':True,'auto_languages':['en','zh'],
            'setup':'Run .venv/bin/python scripts/setup_moss_local.py, then restart ScribeLoop.'}

    def infer(self, pcm, *, language, cancelled):
        # Separate process allows cancellation even inside a blocked native kernel.
        # No credentials or transcript/audio disk files are passed to the worker.
        if cancelled.is_set():
            raise RuntimeError('Local transcription cancelled')
        env = {k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD'))}
        proc = subprocess.Popen([sys.executable,'-m','scribeloop_core.pipeline.moss_gguf_worker',
            str(self.runtime),str(self.model),language],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
        started = time.monotonic()
        output,errors = [],[]
        finished = threading.Event()
        def transfer():
            try:
                output.append(proc.communicate(pcm.astype('<f4').tobytes())[0])
            except Exception as exc:  # noqa: BLE001 - pipe failure is reported on caller thread
                errors.append(exc)
            finally:
                finished.set()
        io = threading.Thread(target=transfer,name='moss-pcm-transfer',daemon=True)
        io.start()
        try:
            while not finished.wait(.1):
                if cancelled.is_set():
                    raise RuntimeError('Local transcription cancelled')
                if time.monotonic()-started > 600:
                    raise RuntimeError('Local transcription timed out')
            if errors:
                raise RuntimeError('Local MOSS pipe failed') from None
            out = output[0]
            if proc.returncode or len(out)>8_000_000:
                raise RuntimeError('Local MOSS failed; encrypted audio retained')
            return json.loads(out)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            io.join(timeout=2)


class MossRecordingRunner(RecordingRunner):
    supported_languages = frozenset({'auto','en','zh'})
    provider = 'local-moss'

    def __init__(self,*args,config=None,**kwargs):
        self.config = config or MossConfig()
        super().__init__(*args,transcriber=self._batch,diarizer=False,finalize_transcript=False,**kwargs)

    def _validate(self,language):
        if language not in self.supported_languages:
            raise ValueError('Local MOSS supports Auto, English or Chinese')
        if not self.config.public()['configured']:
            raise ValueError('Local MOSS is not installed. Run scripts/setup_moss_local.py')

    def start(self,selection,metadata,title, *, consent_confirmed):
        self._validate(metadata.get('language','auto'))
        return super().start(selection,{**metadata,'provider':self.provider,'model':'MOSS Q6_K',
            'engine':'moss-q6-k','cloud_consent':False},title,consent_confirmed=consent_confirmed)

    def reprocess(self,sid, *, language):
        self._validate(language)
        return super().reprocess(sid,language=language)

    def resume(self,sid, *, language='auto'):
        # Offline models cannot extend a previous generation with stable speaker
        # identities. Replace atomically, retaining the old revision on failure.
        return self.reprocess(sid,language=language)

    def _batch(self,audio,sid,emit,cancelled):
        # Authenticate both complete tracks before inference on either.
        ends = {}
        for stream in ('loopback','mic'):
            end = 0
            for frame in audio.frames(sid,stream):
                if cancelled.is_set():
                    return
                end = max(end,round(frame.t_ms+len(frame.pcm)/16))
            ends[stream] = end
        for stream,end in ends.items():
            if cancelled.is_set():
                return
            emit({'type':'recorded_checkpoint','stream':stream,'end_ms':end})
            if not end:
                continue
            emit({'type':'transcription_progress','stream':stream,'mode':'local_batch',
                  'recorded_ms':end,'confirmed_ms':0,'lag_ms':end})
            pcm = pcm_window(audio.frames(sid,stream),0,end)
            # Empty/zero-filled tracks cannot contain usable speech. Still emit
            # completion progress so the UI doesn't retain a phantom backlog.
            result = ({'text':'','segments':[]} if np.max(np.abs(pcm),initial=0) <= 1e-5 else
                      self.config.request(pcm,language=self.language,cancelled=cancelled))
            epoch = uuid4().hex
            language = result.get('language') if self.language == 'auto' else self.language
            if language not in {'en','zh'}:
                language = None
            rows = []
            for row in result.get('segments',[]):
                a,b,speaker = row['start_ms'],row['end_ms'],row.get('speaker')
                if not all(isinstance(t,int) and not isinstance(t,bool) for t in (a,b)) or not 0 <= a < b <= end:
                    raise ValueError('Local MOSS returned invalid timestamps')
                if speaker is not None and (not isinstance(speaker,int) or isinstance(speaker,bool) or not 0 <= speaker <= 9999):
                    raise ValueError('Local MOSS returned invalid speakers')
                text = row['text'].strip()
                if text:
                    rows.append({'stream':stream,'start_ms':a,'end_ms':b,'text':text,'is_final':True,
                        'language':language,'confidence':None,
                        'speaker_key':f'moss:{epoch}:{stream}:{speaker}' if speaker else None})
            if result.get('text','').strip() and not rows:
                raise ValueError('Local MOSS did not return timed speaker segments')
            if cancelled.is_set():
                return
            for row in rows:
                emit(row)
            emit({'type':'transcription_progress','stream':stream,'mode':'local_batch',
                  'recorded_ms':end,'confirmed_ms':end,'lag_ms':0})
