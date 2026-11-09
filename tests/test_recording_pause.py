"""Synthetic PCM only: paused audio never reaches persistence or ASR."""
import asyncio
from itertools import pairwise

import numpy as np
import pytest
from scribeloop_core.capture.recording_gate import RecordingGate
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.recording import RecordingRunner
from test_recording import Source, wait_for
from test_recording import setup as recording_setup


@pytest.fixture()
def recording_state(tmp_path):
    yield from recording_setup.__wrapped__(tmp_path)


def frame(start, count=160, stream='mic', value=.25):
    return AudioFrame(np.full(count,value,dtype=np.float32),stream,start//16,0,start)


def test_pause_excludes_both_tracks_and_compacts_the_shared_clock():
    gate = RecordingGate()
    assert gate.filter(frame(0))[0].sample_offset == 0
    gate.set_paused(True)
    gate.set_paused(True)  # Idempotent command; never toggles unexpectedly.
    assert gate.filter(frame(160,value=.9)) == []
    # A delayed packet straddles the pause boundary on the second track.
    prefix = gate.filter(frame(80,160,'loopback'))
    assert len(prefix) == 1 and prefix[0].pcm.size == 80
    assert gate.filter(frame(320,160,'loopback',.9)) == []
    gate.set_paused(False)
    gate.set_paused(False)
    for stream in ('mic','loopback'):
        resumed = gate.filter(frame(480,160,stream))
        assert len(resumed) == 1 and resumed[0].sample_offset == 160
        assert resumed[0].t_ms == 10
    gate.set_paused(True)
    assert gate.filter(frame(640,320)) == []
    gate.set_paused(False)
    assert gate.filter(frame(960))[0].sample_offset == 320


def test_pause_before_first_frame_and_late_packets_after_continue():
    gate = RecordingGate()
    gate.set_paused(True)
    assert gate.filter(frame(0,320)) == []
    gate.set_paused(False)
    assert gate.filter(frame(160,160,'loopback')) == []
    tail = gate.filter(frame(240,160,'loopback'))
    assert tail[0].pcm.size == 80 and tail[0].sample_offset == 0


@pytest.mark.asyncio
async def test_pause_continue_stop_retains_one_session_and_excludes_pause(recording_state):
    store,audio,events,publish = recording_state
    source = Source()
    runner = RecordingRunner(store,audio,lambda *a,**kw:source,publish,
                             transcriber=lambda *args:None)
    sid = runner.start(None,{'microphone_processing':{'gain_db':6.}},
                       'Pause fixture',consent_confirmed=True)['id']
    await wait_for(lambda:any(e.get('session',{}).get('status')=='recording' for e in events))
    runner.set_paused(sid,True)
    assert runner.paused
    # Let any audio already accepted before Pause drain, then compare file sizes.
    await asyncio.sleep(.1)
    before = {s:audio.path(sid,s).stat().st_size for s in ('mic','loopback')}
    raw = audio.original_path(sid,'mic').with_name('mic.raw.slpa')
    raw_size = raw.stat().st_size
    await asyncio.sleep(.15)
    assert before == {s:audio.path(sid,s).stat().st_size for s in before}
    assert raw.stat().st_size == raw_size
    with pytest.raises(ValueError): runner.set_paused('wrong-session',False)
    runner.set_paused(sid,False)
    assert not runner.paused
    await asyncio.sleep(.1)
    runner.set_paused(sid,True)
    runner.stop(sid)
    with pytest.raises(ValueError): runner.set_paused(sid,False)
    await asyncio.wait_for(runner.task,3)
    assert not runner.paused and store.get(sid)['status'] == 'completed'
    for stream in before:
        frames = list(audio.frames(sid,stream))
        assert len(frames) > 1
        assert all(b.t_ms == a.t_ms+20 for a,b in pairwise(frames))
    await runner.close()


def test_rapid_toggling_is_bounded_and_latest_packet_skips_history():
    gate=RecordingGate()
    for _ in range(20000):
        gate.set_paused(True);gate.set_paused(False)
    assert gate._intervals==[]  # No elapsed samples: no history allocation.
    for n in range(1000):
        gate.filter(frame(n*320,160))
        gate.set_paused(True)
        gate.filter(frame(n*320+160,160))
        gate.set_paused(False)
    class History(list):
        reads=0
        def __iter__(self):
            raise AssertionError('Must not copy/scan the entire pause history')
        def __getitem__(self,index):
            self.reads+=1
            return super().__getitem__(index)
    gate._intervals=History(gate._intervals)
    result=gate.filter(frame(320000))
    assert result[0].sample_offset==160000
    assert gate._intervals.reads<=1
    # A delayed second-track packet still uses the original pause history.
    result=gate.filter(frame(80,320,'loopback'))
    assert [(f.sample_offset,f.pcm.size) for f in result]==[(80,80),(160,80)]


def test_pause_limit_rejects_new_pause_without_stopping_audio_and_merges_adjacent():
    gate=RecordingGate();gate.MAX_INTERVALS=2
    for start in (0,320):
        gate.filter(frame(start));gate.set_paused(True)
        gate.filter(frame(start+160));gate.set_paused(False)
    # An adjacent pause can be reopened without another interval.
    gate.set_paused(True);gate.filter(frame(640));gate.set_paused(False)
    assert len(gate._intervals)==2
    assert gate.filter(frame(800))[0].sample_offset==320
    with pytest.raises(ValueError,match='Pause limit'):
        gate.set_paused(True)
    assert not gate.paused
    assert gate.filter(frame(960))[0].sample_offset==480


def test_indexed_history_matches_sample_mask_for_packets_spanning_many_pauses():
    gate=RecordingGate()
    for n in range(50):
        gate.filter(frame(n*320));gate.set_paused(True)
        gate.filter(frame(n*320+160));gate.set_paused(False)
    intervals=list(gate._intervals)
    for start,count in [(0,16000),(80,800),(15500,1000),(16100,160)]:
        source=frame(start,count,'loopback')
        source.pcm[:]=np.arange(start,start+count,dtype=np.float32)
        result=gate.filter(source)
        expected=[p for p in range(start,start+count) if not any(a<=p<b for a,b in intervals)]
        actual=np.concatenate([r.pcm for r in result]) if result else np.array([])
        assert actual.tolist()==expected
        for packet in result:
            original=int(packet.pcm[0])
            assert packet.sample_offset==original-sum(b-a for a,b in intervals if b<=original)
