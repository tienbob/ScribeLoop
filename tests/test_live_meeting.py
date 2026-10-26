"""M6 timeline, agreement, durable catch-up and nonblocking recording tests."""
from __future__ import annotations

import asyncio
import secrets
import threading

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.diar import NearOnlineChunked, SpeakerMatcher, attribution
from scribeloop_core.pipeline.live import LocalAgreement, timeline_pcm, transcribe_live
from scribeloop_core.pipeline.recording import RecordingRunner
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


def row(text='hello',start=0,end=1000,stream='loopback'):
    return {'text':text,'start_ms':start,'end_ms':end,'is_final':True,'stream':stream}


class Keys:
    def __init__(self):
        self.rows = {}
    def create(self,key):
        self.rows[key] = secrets.token_bytes(32)
    def get(self,key):
        return self.rows.get(key)
    def delete(self,key):
        del self.rows[key]


@pytest.fixture()
def state(tmp_path):
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    audio = SessionAudioStore(tmp_path/'audio',Keys())
    session = store.create_recording('Live test',{'language':'en'})
    audio.create_session(session['id'])
    yield store,audio,session['id']
    conn.close()


def test_timeline_retains_nonzero_start_and_gaps():
    frames = [AudioFrame(np.ones(1600,dtype=np.float32),'mic',150,0),
              AudioFrame(np.ones(1600,dtype=np.float32)*2,'mic',450,0)]
    pcm = timeline_pcm(frames,100,600)
    assert pcm.size == 8000
    assert np.all(pcm[:800] == 0)
    assert np.all(pcm[800:2400] == 1)
    assert np.all(pcm[2400:5600] == 0)
    assert np.all(pcm[5600:7200] == 2)
    assert np.all(pcm[7200:] == 0)


def test_local_agreement_confirms_only_unchanged_prefix_and_final_tail():
    policy = LocalAgreement()
    assert policy.update([row('one'),row('two',1000,2000)]) == []
    assert policy.update([row('one'),row('changed',1000,2000)]) == [row('one')]
    policy.reset()
    assert policy.update([row('tail')],final=True) == [row('tail')]
    policy.reset()
    policy.update([row('same',0,1000)])
    assert policy.update([row('same',2000,3000)]) == []


class Worker:
    def __init__(self):
        self.calls = []
        self.closed = False
    def infer(self,tracks,language,cancelled):
        self.calls.append((tracks,language))
        stream = next(iter(tracks))
        return {'segments':[row(stream=stream)]}
    def close(self):
        self.closed = True


def all_speech(pcm):
    return [{'start':0,'end':len(pcm)}]


def test_completed_audio_catchup_uses_checkpoint_and_bounded_windows(state):
    _store,audio,sid = state
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            for i in range(450):
                writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,i*100,0))
    class ContextWorker(Worker):
        def infer(self,tracks,language,cancelled):
            self.calls.append((tracks,language))
            return {'segments':[row(start=2000,end=3000,stream=next(iter(tracks)))]}
    worker = ContextWorker()
    done = threading.Event()
    done.set()
    events = []
    transcribe_live(audio,sid,events.append,threading.Event(),done,language='en',
        worker_factory=lambda:worker,vad=all_speech,
        checkpoints={'mic':{'confirmed_ms':5000},'loopback':{'confirmed_ms':0}})
    confirmed = [e for e in events if e['type']=='confirmed_window']
    assert max(e['end_ms'] for e in confirmed) == 45000
    assert next(e for e in confirmed if e['stream']=='mic')['segments'][0]['start_ms'] == 5000
    assert all(len(next(iter(tracks.values()))) <= 22*16000 for tracks,_ in worker.calls)
    assert len([e for e in events if e['type']=='language_locked']) == 1
    assert worker.closed
    assert all(e['segments'][0]['end_ms'] <= e['end_ms'] for e in confirmed)


def test_silence_advances_checkpoint_without_loading_asr(state):
    _,audio,sid = state
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.zeros(1600,dtype=np.float32),stream,150,0))
    worker = Worker()
    done = threading.Event()
    done.set()
    events = []
    transcribe_live(audio,sid,events.append,threading.Event(),done,language='en',
                    worker_factory=lambda:worker,vad=lambda pcm:[])
    assert not worker.calls
    assert {e['end_ms'] for e in events if e['type']=='confirmed_window'} == {250}
    assert all(not e['segments'] for e in events if e['type']=='confirmed_window')


def test_live_uses_one_shared_language_lock_and_rejects_bad_timestamps(state):
    _,audio,sid = state
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            for i in range(60):
                writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,i*100,0))
    done = threading.Event()
    done.set()
    worker = Worker()
    calls = []
    def detector(tracks):
        calls.append(tracks)
        return {'language':'vi','mode':'auto','confidence':.9,'sample_s':6}
    transcribe_live(audio,sid,lambda e:None,threading.Event(),done,
                    worker_factory=lambda:worker,vad=all_speech,detector=detector)
    assert len(calls) == 1
    assert all(lang == 'vi' for _,lang in worker.calls)
    worker.infer = lambda *a:{'segments':[row(end=7000)]}
    with pytest.raises(ValueError,match='timestamp'):
        transcribe_live(audio,sid,lambda e:None,threading.Event(),done,language='en',
                        worker_factory=lambda:worker,vad=all_speech)


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_confirmed_text_appears_before_stop_and_capture_does_not_wait_for_asr(state):
    store,audio,_ = state
    # Separate fresh session: runner prepares encryption itself.
    release = threading.Event()
    stop = threading.Event()
    class Source:
        closed = False
        def frames(self):
            for i in range(120):
                if i == 80:
                    release.wait(3)
                for stream in ('mic','loopback'):
                    yield AudioFrame(np.full(1600,.03,dtype=np.float32),stream,i*100,0)
            stop.wait(3)
        def request_stop(self):
            stop.set()
            release.set()
        def close(self):
            self.closed = True
            stop.set()
            release.set()
    source = Source()
    worker = Worker()
    events = []
    first_partial_rows = []
    async def publish(event):
        if event['type'] == 'partial' and event['segment']['text'] and not first_partial_rows:
            # Observe persistence at delivery, before the runner can consume the
            # next queued confirmed window. A polling waiter can resume later.
            first_partial_rows.append(store.segments(event['session_id']))
        events.append(event)
    def live(*args,**kwargs):
        return transcribe_live(*args,**kwargs,worker_factory=lambda:worker,vad=all_speech,poll_s=.01)
    runner = RecordingRunner(store,audio,lambda *a,**kw:source,publish,
                             live_transcriber=live,diarizer=False)
    session = runner.start(None,{'language':'en'},'Live recording',consent_confirmed=True)
    sid = session['id']
    await wait_for(lambda:bool(first_partial_rows))
    assert not first_partial_rows[0], 'The first partial must not be persisted'
    release.set()
    await wait_for(lambda:bool(store.segments(sid)))
    assert store.get(sid)['status'] == 'recording'
    assert runner.active_id == sid
    runner.stop(sid)
    await asyncio.wait_for(runner.task,5)
    assert source.closed and worker.closed
    assert store.get(sid)['status'] == 'completed'
    assert all(c['confirmed_ms'] == 12000 for c in store.checkpoints(sid).values())
    assert len({r['id'] for r in store.segments(sid)}) == len(store.segments(sid))


@pytest.mark.asyncio
async def test_live_model_failure_does_not_stop_recording(state):
    store,audio,_ = state
    stop = threading.Event()
    class Source:
        closed = False
        def frames(self):
            for stream in ('mic','loopback'):
                yield AudioFrame(np.ones(1600,dtype=np.float32),stream,0,0)
            stop.wait(3)
        def request_stop(self):
            stop.set()
        def close(self):
            self.closed = True
            stop.set()
    source = Source()
    def fail(*a,**kw):
        raise RuntimeError('Model unavailable')
    events = []
    async def publish(event):
        events.append(event)
    runner = RecordingRunner(store,audio,lambda *a,**kw:source,publish,
                             live_transcriber=fail,diarizer=False)
    session = runner.start(None,{},'Failed ASR',consent_confirmed=True)
    await wait_for(lambda:any(e['type']=='transcription_error' for e in events))
    assert not source.closed
    assert runner.active_id == session['id']
    runner.stop(session['id'])
    await runner.task
    assert len(list(audio.frames(session['id'],'mic'))) == 1
    assert source.closed


def test_speaker_matcher_preserves_identity_across_label_swaps_and_unknowns():
    matcher = SpeakerMatcher()
    first = matcher.match({'A':[1,0,0],'B':[0,1,0]})
    swapped = matcher.match({'newB':[0,1,0],'newA':[1,0,0]})
    assert swapped['newA'][0] == first['A'][0]
    assert swapped['newB'][0] == first['B'][0]
    assert matcher.match({'ambiguous':[.7,.7,0]})['ambiguous'][0] is None
    assert matcher.match({'invalid':[0,0,0]})['invalid'][0] is None


def test_delayed_diarization_preserves_overlapping_turns_and_bounds():
    class DiarWorker(Worker):
        def infer(self,*args):
            return {'embeddings':{'A':[1,0],'B':[0,1]},'turns':[
                {'start_ms':0,'end_ms':2000,'local_speaker':'A'},
                {'start_ms':1000,'end_ms':3000,'local_speaker':'B'}]}
    worker = DiarWorker()
    backend = NearOnlineChunked(worker=worker)
    turns = backend.window(np.ones(4*16000,dtype=np.float32),10000,11000,13000,threading.Event())
    assert [(t['start_ms'],t['end_ms']) for t in turns] == [(11000,12000),(11000,13000)]
    assert len({t['label'] for t in turns}) == 2
    assert attribution(row(start=11000,end=12000),turns) == 'Overlapping / uncertain speakers'
    assert attribution(row(start=12000,end=13000),turns) == 'Speaker 2'
    assert attribution(row(stream='mic'),turns) == 'Microphone'
    assert attribution(row(),[]) == 'Unknown speaker'
    backend.close()
    assert worker.closed


def test_sixty_second_lag_defers_model_work_until_capture_stops(state):
    _,audio,sid = state
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            for i in range(610):
                writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,i*100,0))
    worker = Worker()
    done, cancel = threading.Event(), threading.Event()
    events = []
    def emit(event):
        events.append(event)
        if event.get('mode') == 'deferred' and event['stream'] == 'mic':
            assert not worker.calls
            done.set()  # Stop makes durable backlog eligible for catch-up.
    transcribe_live(audio,sid,emit,cancel,done,language='en',worker_factory=lambda:worker,
                    vad=all_speech,poll_s=.001)
    assert {e['stream'] for e in events if e.get('mode')=='deferred'} == {'mic','loopback'}
    assert worker.calls and worker.closed
    assert max(e['end_ms'] for e in events if e['type']=='confirmed_window') == 61000


def test_cancel_closes_persistent_model_without_committing_partial_tail(state):
    _,audio,sid = state
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            for i in range(50):
                writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,i*100,0))
    cancel = threading.Event()
    worker = Worker()
    events = []
    def emit(event):
        events.append(event)
        if event['type']=='partial':
            cancel.set()
    transcribe_live(audio,sid,emit,cancel,threading.Event(),language='en',
                    worker_factory=lambda:worker,vad=all_speech,poll_s=.001)
    assert worker.closed
    assert not [e for e in events if e['type']=='confirmed_window']


@pytest.mark.asyncio
@pytest.mark.parametrize('final_fails',[False,True])
async def test_final_refinement_preserves_live_revision_and_never_reopens_capture(state,final_fails):
    store,audio,_ = state
    class Source:
        closed = False
        def frames(self):
            for stream in ('mic','loopback'):
                yield AudioFrame(np.ones(1600,dtype=np.float32),stream,0,0)
        def request_stop(self):
            pass
        def close(self):
            self.closed = True
    source = Source()
    opens = []
    def factory(*a,**kw):
        opens.append(True)
        return source
    def live(audio,sid,emit,cancelled,done,**kw):
        emit({'type':'language_locked','language':'en','mode':'auto','confidence':.95,'sample_s':2})
        for stream in ('mic','loopback'):
            emit({'type':'recorded_checkpoint','stream':stream,'end_ms':100})
            emit({'type':'confirmed_window','stream':stream,'end_ms':100,
                  'segments':[row('Live draft',0,100,stream)]})
    def final(audio,sid,emit,cancelled):
        assert source.closed
        if final_fails:
            raise RuntimeError('Final model failed')
        for stream in ('mic','loopback'):
            assert list(audio.frames(sid,stream))
            emit(row('Refined words',0,100,stream))
    events = []
    async def publish(event):
        events.append(event)
    runner = RecordingRunner(store,audio,factory,publish,transcriber=final,
        live_transcriber=live,diarizer=False,finalize_transcript=True)
    session = runner.start(None,{},'Refine',consent_confirmed=True)
    await runner.task
    sid = session['id']
    assert len(opens) == 1
    assert store.conn.execute('SELECT count(*) FROM segments WHERE session_id=? AND revision=1',(sid,)).fetchone()[0] == 2
    assert {r['text'] for r in store.segments(sid)} == ({'Live draft'} if final_fails else {'Refined words'})
    assert store.get(sid)['status'] == ('failed' if final_fails else 'completed')
    assert any(e['type']=='transcription_stage' for e in events)


@pytest.mark.asyncio
async def test_slow_live_worker_does_not_delay_encrypted_stop_or_recorder_checkpoint(state):
    store,audio,_ = state
    blocked, release, stop = threading.Event(),threading.Event(),threading.Event()
    class Source:
        closed = False
        def frames(self):
            for i in range(60):
                for stream in ('mic','loopback'):
                    yield AudioFrame(np.ones(1600,dtype=np.float32),stream,i*100,0)
            stop.wait(3)
        def request_stop(self):
            stop.set()
        def close(self):
            self.closed = True
            stop.set()
    source = Source()
    def live(*a,**kw):
        blocked.set()
        release.wait(3)
    async def publish(event):
        pass
    runner = RecordingRunner(store,audio,lambda *a,**kw:source,publish,
                             live_transcriber=live,diarizer=False)
    sid = runner.start(None,{'language':'en'},'Slow live worker',consent_confirmed=True)['id']
    await wait_for(blocked.is_set)
    await wait_for(lambda:store.checkpoints(sid).get('mic',{}).get('recorded_ms') == 6000)
    runner.stop(sid)
    await wait_for(lambda:source.closed and runner.active_id is None)
    assert store.get(sid)['status'] == 'processing'
    assert sum(f.pcm.size for f in audio.frames(sid,'mic')) == 6000*16
    assert not release.is_set()
    release.set()
    await runner.task


@pytest.mark.asyncio
async def test_diarization_persistence_failure_cancels_its_worker_without_blocking_asr(state,monkeypatch):
    store,audio,sid = state
    ended = threading.Event()
    def diar(audio,sid,emit,cancelled,done,**kw):
        try:
            while not cancelled.is_set():
                emit({'turns':[{'start_ms':0,'end_ms':100,'label':'Speaker 1'}],'end_ms':100})
        finally:
            ended.set()
    def fail(*args):
        raise OSError('Injected speaker-store failure')
    monkeypatch.setattr(store,'save_turns',fail)
    events = []
    async def publish(event):
        events.append(event)
    runner = RecordingRunner(store,audio,None,publish,diarizer=diar)
    runner.storage_ready.set()
    await asyncio.wait_for(runner._diarize(sid),2)
    assert ended.is_set()
    assert not runner.cancel_processing.is_set()
    assert any(e['type']=='speaker_warning' for e in events)
