"""In-memory MOSS adapter contracts; no model downloads or GPU required."""
import sys
import threading
import time

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline import meeting_asr
from scribeloop_core.storage.audio import AudioIntegrityError


class Audio:
    def frames(self, sid, stream):
        yield AudioFrame(np.full(16000,.1,dtype=np.float32),stream,100,0)
        yield AudioFrame(np.full(16000,.2,dtype=np.float32),stream,2100,0)


def test_pcm_preserves_shared_offsets_and_gaps():
    tracks=meeting_asr.recording_pcm(Audio(),'unused')
    for pcm in tracks.values():
        assert pcm.size == 49600
        assert not pcm[:1600].any()
        assert np.allclose(pcm[1600:17600],.1)
        assert not pcm[17600:33600].any()
        assert np.allclose(pcm[33600:],.2)


def test_pcm_requires_authenticated_footer_before_inference():
    class Broken(Audio):
        def frames(self,sid,stream):
            yield from super().frames(sid,stream)
            raise AudioIntegrityError('Missing footer')
    with pytest.raises(AudioIntegrityError):
        meeting_asr.recording_pcm(Broken(),'unused')


def test_pcm_enforces_timeline_limit():
    class Long(Audio):
        def frames(self,sid,stream):
            yield AudioFrame(np.zeros(16000,dtype=np.float32),stream,305000,0)
    with pytest.raises(ValueError,match='limit'):
        meeting_asr.recording_pcm(Long(),'unused')


def worker(monkeypatch, script):
    original=meeting_asr.subprocess.Popen
    children=[]
    def launch(*args,**kwargs):
        kwargs.pop('env')
        child=original([sys.executable,'-c',script],**kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(meeting_asr.subprocess,'Popen',launch)
    return children


def test_slow_worker_stdin_does_not_lose_pending_input(monkeypatch):
    children=worker(monkeypatch,"""
import io,json,sys,time
import numpy as np
time.sleep(.35)
with np.load(io.BytesIO(sys.stdin.buffer.read()),allow_pickle=False) as data:
    assert str(data['language'])=='vi'
    assert len(data['mic'])==49600
print(json.dumps({'segments':[{'stream':'mic','text':'có chạy thật không vậy',
    'start_ms':100,'end_ms':1000,'is_final':True,'language':'vi'}]}))
""")
    rows=[]
    meeting_asr.transcribe_moss(Audio(),'unused',rows.append,threading.Event(),language='vi')
    assert rows[0]['text']=='có chạy thật không vậy'
    assert children[0].poll()==0
    assert children[0].stdin.closed and children[0].stdout.closed


def test_cancel_kills_worker_without_emitting_or_plaintext_files(monkeypatch):
    children=worker(monkeypatch,'import sys,time; sys.stdin.buffer.read(); time.sleep(30)')
    cancelled=threading.Event()
    timer=threading.Timer(.1,cancelled.set)
    rows=[]
    timer.start()
    started=time.monotonic()
    try:
        meeting_asr.transcribe_moss(Audio(),'unused',rows.append,cancelled)
    finally:
        timer.cancel()
    assert time.monotonic()-started<3
    assert not rows
    assert children[0].poll() is not None


def test_invalid_hint_does_not_read_audio():
    with pytest.raises(ValueError,match='language'):
        meeting_asr.transcribe_moss(None,'unused',None,threading.Event(),language='unknown')
