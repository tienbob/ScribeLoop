"""Long timelines and bounded SLPA readers, using generated PCM only."""
import asyncio
import os
import tracemalloc

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
from scribeloop_core.pipeline.model_selection import CloudRecordingRouter
from scribeloop_core.storage.audio import AudioIntegrityError, SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_deepgram_meeting import Socket
from test_recording import Keys


@pytest.fixture()
def state(tmp_path):
    conn=migrate(connect(tmp_path/'metadata.db'))
    store=SessionStore(conn)
    audio=SessionAudioStore(tmp_path/'audio',Keys())
    sid=store.create_recording('Long',{'duration_s':10800,'provider':'deepgram'})['id']
    audio.create_session(sid)
    yield store,audio,sid
    conn.close()


def test_incremental_cursor_partial_append_and_footer(state):
    _,audio,sid=state
    writer=audio.writer(sid,'mic')
    with audio.cursor(sid,'mic') as cursor:
        assert cursor.read()[0]==[]
        for i in range(40):
            writer.write(AudioFrame(np.ones(1600,dtype='float32'),'mic',i*100,0))
            frames,report=cursor.read(max_frames=1)
            assert len(frames)==1 and frames[0].t_ms==i*100
            assert report['samples']==1600*(i+1)
        writer.close()
        assert cursor.read()[1]['complete']
        offset=cursor.offset
        assert cursor.read()[0]==[] and cursor.offset==offset


def test_cursor_rejects_replacement_truncation_and_tamper(state):
    _,audio,sid=state
    with audio.writer(sid,'mic') as writer:
        writer.write(AudioFrame(np.ones(1600,dtype='float32'),'mic',0,0))
    with audio.cursor(sid,'mic') as cursor:
        cursor.read(max_frames=1)
        path=audio.path(sid,'mic')
        replacement=path.with_suffix('.replacement')
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement,path)
        with pytest.raises(AudioIntegrityError,match='replaced'):
            cursor.read()
    with audio.cursor(sid,'mic') as cursor:
        path.write_bytes(path.read_bytes()[:-1])
        with pytest.raises(AudioIntegrityError,match='truncated'):
            cursor.read()


def test_long_prefix_recovery_has_bounded_pcm_memory(state):
    _,audio,sid=state
    pcm=np.ones(16000,dtype='float32')*.1
    for stream in ('mic','loopback'):
        writer=audio.writer(sid,stream)
        for second in range(400):
            writer.write(AudioFrame(pcm,stream,second*1000,0))
        writer.abort()
    tracemalloc.start()
    try:
        report=audio.recover_session(sid)
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak<20_000_000
    assert report['mic']['samples']==400*16000
    assert audio.scan(sid,'mic')['complete']
    with audio.cursor(sid,'mic') as cursor:
        total=0
        while True:
            frames,report=cursor.read(max_frames=4)
            assert len(frames)<=4
            total+=len(frames)
            if report['complete']:
                break
        assert total==400
    assert audio.window(sid,'mic',390000,400000).shape==(160000,)
    with pytest.raises(ValueError):
        audio.window(sid,'mic',0,400000)


@pytest.mark.asyncio
async def test_three_hour_resume_uses_cloud_once_per_track_without_full_pcm(state):
    store,audio,sid=state
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.ones(16000,dtype='float32'),stream,0,0))
            writer.write(AudioFrame(np.ones(16000,dtype='float32'),stream,10_799_000,0))
        store.checkpoint(sid,stream,recorded_ms=10800000)
        store.commit_window(sid,stream,[],10799000)
    store.transition(sid,'completed')
    sockets=[]
    def connector(*args,**kwargs):
        sock=Socket()
        sockets.append(sock)
        return sock
    async def publish(event):
        pass
    runner=CloudRecordingRouter(store,audio,None,publish,cloud_config=DeepgramConfig(api_key='test',connector=connector))
    runner.resume(sid,language='en',cloud_consent=True)
    async with asyncio.timeout(5):
        await runner.task
    assert len(sockets)==2
    assert all(s.sent_ms==1000 for s in sockets)
    assert runner.max_s==10800 and runner.cloud.budget_usd==8
    assert all(c['confirmed_ms']==10800000 for c in store.checkpoints(sid).values())
    assert store.get(sid)['status']=='completed'
    await runner.close()
