"""No paid calls or devices: scheduler, cancellation and bounded detail reads."""
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest
from scribeloop_core.pipeline.deepgram_meeting import DeepgramError, StreamWrites


def harness():
    path = Path(__file__).resolve().parents[1]/'scripts/audit_streaming_performance.py'
    spec = importlib.util.spec_from_file_location('stream_audit_fixture',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('chunk',[170,171,320,1600])
def test_stream_scheduler_catches_up_independent_of_native_packet_size(tmp_path,chunk):
    result = harness().scheduler_bench(tmp_path,chunk,duration=3)
    assert result['scheduler_elapsed_s'] < .5
    assert result['sent_audio_ms'] == 3000


@pytest.mark.parametrize('cancel',[False,True])
def test_real_blocked_socket_write_obeys_deadline_and_cancellation(cancel):
    left, right = socket.socketpair()
    left.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
    cancelled = threading.Event()
    class Socket:
        socket = left
        def send(self,data): self.socket.sendall(data)
    timer = threading.Timer(.05,cancelled.set) if cancel else None
    started = time.monotonic()
    try:
        if timer: timer.start()
        with StreamWrites(Socket(),cancelled,.1) as writes, pytest.raises(InterruptedError if cancel else DeepgramError):
            writes.send(b'x'*(1024*1024))
        assert time.monotonic()-started < 1
    finally:
        if timer: timer.cancel(); timer.join()
        left.close()
        right.close()


def test_long_meeting_pages_have_bounded_sql_count(tmp_path):
    results = harness().page_bench(tmp_path)
    assert all(r['returned_segments']==100 for r in results)
    assert max(r['sql_statements_per_page'] for r in results) < 80


@pytest.mark.parametrize('overrun,closing,start_outside,valid',[
    (20,True,False,True),(100,True,False,True),(101,True,False,False),
    (20,False,False,False),(20,True,True,False)])
def test_only_closing_word_end_rounding_is_clamped(tmp_path,overrun,closing,start_outside,valid):
    import numpy as np
    from scribeloop_core.capture.source import AudioFrame
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig, _stream_cursor
    from scribeloop_core.storage.audio import SessionAudioStore
    module=harness()
    audio=SessionAudioStore(tmp_path/'audio',module.Keys())
    sid='a'*32
    audio.create_session(sid)
    with audio.writer(sid,'mic') as writer:
        writer.write(AudioFrame(np.zeros(16000,dtype='float32'),'mic',0,0))
    class TailSocket(module.Socket):
        def recv(self,timeout):
            if self.sent>self.reported and (self.closed or not closing):
                self.reported=self.sent
                return json.dumps({'type':'Results','is_final':True,'start':0,'duration':self.sent/1000,
                    'channel':{'alternatives':[{'transcript':'tail','words':[
                        {'word':'tail','start':1. if start_outside else .8,'end':1+overrun/1000,'speaker':0}]}]}})
            if not self.closed:
                time.sleep(.001)
                raise TimeoutError
            return super().recv(timeout)
    done,cancelled=threading.Event(),threading.Event()
    if closing: done.set()
    events=[]
    with audio.cursor(sid,'mic') as cursor:
        def run():
            return _stream_cursor(cursor,sid,'mic',events.append,cancelled,done,
                config=DeepgramConfig(api_key='synthetic',connector=lambda *a,**k:TailSocket()),
                budget=module.Budget(),language='en',checkpoint=0)
        if valid:
            run()
            saved=[row for e in events if e['type']=='confirmed_window' for row in e['segments']]
            assert saved and saved[-1]['end_ms']==1000
        else:
            with pytest.raises(DeepgramError): run()


@pytest.mark.parametrize('chunk',[170,171,320])
def test_streaming_preserves_every_sample_of_fractional_ms_tail(tmp_path,chunk):
    import numpy as np
    from scribeloop_core.capture.source import AudioFrame
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig, _stream_cursor
    from scribeloop_core.storage.audio import SessionAudioStore
    module=harness()
    audio=SessionAudioStore(tmp_path/'audio',module.Keys())
    sid='a'*32
    audio.create_session(sid)
    pcm=np.random.default_rng(41).uniform(-.3,.3,16001).astype('float32')
    with audio.writer(sid,'mic') as writer:
        for offset in range(0,len(pcm),chunk):
            writer.write(AudioFrame(pcm[offset:offset+chunk],'mic',round(offset/16),0))
    payload=[]
    class Socket(module.Socket):
        def send(self,value):
            if isinstance(value,bytes): payload.append(value)
            super().send(value)
    done,cancelled=threading.Event(),threading.Event()
    done.set()
    with audio.cursor(sid,'mic') as cursor:
        _stream_cursor(cursor,sid,'mic',lambda _:None,cancelled,done,
            config=DeepgramConfig(api_key='synthetic',connector=lambda *a,**k:Socket()),
            budget=module.Budget(),language='en',checkpoint=0)
    sent=np.frombuffer(b''.join(payload),dtype='<i2')
    assert len(sent)==16016  # Only the final partial millisecond is zero-padded.
    np.testing.assert_array_equal(sent[:len(pcm)],(pcm*32767).astype('<i2'))
    assert np.all(sent[len(pcm):]==0)
