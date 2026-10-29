"""Recorder lifecycle with generated PCM; no hardware or network access."""
import asyncio
import secrets
import threading
import time

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.recording import RecordingRunner
from scribeloop_core.storage.audio import MAGIC, SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.keys import KeyUnavailable
from scribeloop_core.storage.sessions import SessionStore

pytest.importorskip('cryptography')


class Keys:
    def __init__(self):
        self.rows = {}
    def create(self, key):
        self.rows[key] = secrets.token_bytes(32)
        return self.rows[key]
    def get(self, key):
        return self.rows.get(key)
    def delete(self, key):
        del self.rows[key]


class Source:
    def __init__(self):
        self.stopped = threading.Event()
        self.closed = False
    def frames(self):
        i = 0
        while not self.stopped.is_set():
            for stream in ('loopback', 'mic'):
                yield AudioFrame(np.full(320, .02, dtype=np.float32), stream, i * 20, 0)
            i += 1
            time.sleep(.005)
    def request_stop(self):
        self.stopped.set()
        return True
    def close(self):
        self.stopped.set()
        self.closed = True


@pytest.fixture()
def setup(tmp_path):
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    audio = SessionAudioStore(tmp_path / 'recordings', Keys())
    events = []
    async def publish(event):
        events.append(event)
    yield store, audio, events, publish
    conn.close()


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_encrypted_record_stop_then_stream_labelled_notes(setup):
    store, audio, events, publish = setup
    source = Source()
    def source_factory(*args, **kwargs):
        assert len(list(audio.root.rglob('*.slpa'))) == 2
        return source
    def transcribe(audio, sid, emit, cancelled):
        assert source.closed, 'ASR must start after capture closes'
        for stream in ('loopback', 'mic'):
            frames = list(audio.frames(sid, stream))
            assert frames
            emit({'stream':stream, 'text':'Local test speech', 'start_ms':0,
                      'end_ms':20, 'is_final':True})
    runner = RecordingRunner(store, audio, source_factory, publish, transcriber=transcribe)
    session = runner.start(None, {'scope':'application','app_id':'brave'}, 'Call', consent_confirmed=True)
    with pytest.raises(ValueError):
        runner.start(None, {}, 'Second', consent_confirmed=True)
    await wait_for(lambda: any(e.get('session', {}).get('status') == 'recording' for e in events))
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task, 3)
    assert runner.active_id is None
    assert store.get(session['id'])['status'] == 'completed'
    assert store.get(session['id'])['audio_duration_ms'] > 0
    assert {r['stream'] for r in store.segments(session['id'])} == {'loopback','mic'}
    assert 'Microphone' in store.notes(session['id'])
    assert 'Meeting audio' in store.notes(session['id'])
    for path in audio.root.rglob('*'):
        if path.is_file():
            assert path.suffix == '.slpa'
            assert path.read_bytes().startswith(MAGIC)
    stream_rows = list(store.conn.execute('SELECT * FROM session_streams'))
    assert len(stream_rows) == 2
    assert all(row['audio_path'] and row['ended_at'] for row in stream_rows)
    await runner.close()


@pytest.mark.asyncio
async def test_denied_consent_opens_no_keys_or_source(setup):
    store, audio, _, publish = setup
    def forbidden(*a, **kw):
        raise AssertionError('Source opened')
    runner = RecordingRunner(store, audio, forbidden, publish)
    for consent in (False, 1, 'true'):
        with pytest.raises(ValueError):
            runner.start(None, {}, 'No', consent_confirmed=consent)
    assert not store.list()
    assert not audio.keys.rows


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['key','writer','source','missing_stream','overflow'])
async def test_capture_failure_releases_slot_and_never_transcribes(setup, failure, monkeypatch):
    store, audio, events, publish = setup
    source = Source()
    def fail(*a, **kw):
        raise KeyUnavailable('denied')
    if failure == 'key':
        monkeypatch.setattr(audio, 'create_session', fail)
    elif failure == 'writer':
        monkeypatch.setattr(audio, 'writer', fail)
    def factory(*a, **kw):
        if failure == 'source':
            raise RuntimeError('Denied permission or missing app')
        if failure == 'missing_stream':
            source.frames = lambda: iter([AudioFrame(np.zeros(320,dtype=np.float32),'mic',0,0)])
        if failure == 'overflow':
            source.frames = lambda: (AudioFrame(np.zeros(320,dtype=np.float32),'mic',i*20,0)
                                    for i in range(10000))
        return source
    def no_transcribe(*a):
        raise AssertionError('Failed capture must not start ASR')
    async def checked_publish(event):
        if event.get('session',{}).get('status') == 'failed':
            assert runner.active_id is None, 'Failed-state snapshots must not retain a capture owner'
        await publish(event)
    runner = RecordingRunner(store, audio, factory, checked_publish, transcriber=no_transcribe, queue_size=2)
    session = runner.start(None, {}, 'Failure', consent_confirmed=True)
    await asyncio.wait_for(runner.task, 3)
    assert runner.active_id is None
    assert store.get(session['id'])['status'] == 'failed'
    assert any(event['type'] == 'recording_error' for event in events)
    if failure in {'writer','source'}:
        assert not audio.keys.rows
    if failure in {'missing_stream','overflow'}:
        assert source.closed


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_slow_inference(setup):
    store, audio, events, publish = setup
    source = Source()
    processing = threading.Event()
    release = threading.Event()
    def transcribe(*a):
        processing.set()
        release.wait(timeout=3)
    runner = RecordingRunner(store, audio, lambda *a, **kw: source, publish, transcriber=transcribe)
    session = runner.start(None, {}, 'Slow ASR', consent_confirmed=True)
    await wait_for(lambda: any(e.get('session', {}).get('status') == 'recording' for e in events))
    runner.stop(session['id'])
    await wait_for(processing.is_set)
    assert source.closed
    assert runner.active_id is None
    assert store.get(session['id'])['status'] == 'processing'
    release.set()
    await asyncio.wait_for(runner.task, 3)


def test_recovery_marks_recording_and_processing_interrupted(setup):
    store, _, _, _ = setup
    first = store.create_recording('Interrupted', {})
    second = store.create_recording('Processing', {})
    store.transition(second['id'], 'processing')
    store.recover()
    assert all(store.get(s['id'])['status'] == 'interrupted' for s in (first,second))


@pytest.mark.asyncio
async def test_reprocess_retains_old_revision_and_opens_no_capture(setup):
    store,audio,events,publish=setup
    session=store.create_recording('Vietnamese',{})
    sid=session['id']
    store.append(sid,{'text':'Old smoke text','start_ms':0,'end_ms':20,'is_final':True})
    store.transition(sid,'completed')
    def forbidden(*args,**kw):
        raise AssertionError('Reprocessing must not open capture')
    def transcribe(audio,sid,emit,cancelled):
        emit({'text':'có chạy thật không vậy','stream':'mic','start_ms':0,'end_ms':20,'is_final':True})
    runner=RecordingRunner(store,audio,forbidden,publish,transcriber=transcribe)
    original_end=store.get(sid)['ended_at']
    runner.reprocess(sid,language='vi')
    await asyncio.wait_for(runner.task,3)
    assert store.get(sid)['ended_at']==original_end
    assert store.get(sid)['status']=='completed'
    assert [r['text'] for r in store.segments(sid)]==['có chạy thật không vậy']
    assert store.conn.execute('SELECT count(*) FROM segments WHERE revision=1').fetchone()[0]==1
    assert any(e['type']=='transcript_replaced' for e in events)


@pytest.mark.asyncio
async def test_failed_reprocess_preserves_previous_transcript(setup):
    store,audio,events,publish=setup
    session=store.create_recording('Vietnamese',{})
    sid=session['id']
    store.append(sid,{'text':'Original','start_ms':0,'end_ms':20,'is_final':True})
    store.transition(sid,'completed')
    def fail(*args):
        raise RuntimeError('Model unavailable')
    runner=RecordingRunner(store,audio,None,publish,transcriber=fail)
    runner.reprocess(sid,language='vi')
    await asyncio.wait_for(runner.task,3)
    assert [r['text'] for r in store.segments(sid)]==['Original']
    assert any(e['type']=='recording_error' for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['write', 'footer', 'abort'])
async def test_disk_failure_cleans_both_writers_and_source(setup, monkeypatch, failure):
    store, audio, events, publish = setup
    source = Source()
    original = audio.writer
    writers = []
    aborted = []

    def writer(sid, stream):
        item = original(sid, stream)
        writers.append(item)
        original_abort = item.abort
        def abort():
            aborted.append(stream)
            original_abort()
            if failure == 'abort' and stream == 'mic':
                raise OSError('cleanup failure')
        item.abort = abort
        if stream == 'mic':
            def fail(*args):
                raise OSError('disk full')
            if failure == 'footer':
                item.close = fail
            else:
                item.write = fail
        return item

    monkeypatch.setattr(audio, 'writer', writer)
    def forbidden(*args):
        raise AssertionError('Failed disk output must not be transcribed')
    runner = RecordingRunner(store, audio, lambda *a, **kw: source, publish,
                             transcriber=forbidden, max_s=.04)
    session = runner.start(None, {}, 'Disk fault', consent_confirmed=True)
    await asyncio.wait_for(runner.task, 3)
    assert store.get(session['id'])['status'] == 'failed'
    assert runner.active_id is None and runner.source is None
    assert source.closed
    assert set(aborted) == {'mic', 'loopback'}
    assert all(item.closed for item in writers)
    assert any(e['type'] == 'recording_error' for e in events)


@pytest.mark.asyncio
async def test_blocked_stop_times_out_and_closes_native_reader(setup):
    store, audio, events, publish = setup
    source = Source()
    def frames():
        for stream in ('mic', 'loopback'):
            yield AudioFrame(np.zeros(320, dtype=np.float32), stream, 0, 0)
        source.stopped.wait(2)
    source.frames = frames
    source.request_stop = lambda: False  # Unresponsive graceful shutdown.
    runner = RecordingRunner(store, audio, lambda *a, **kw: source, publish,
                             stop_timeout_s=.05)
    session = runner.start(None, {}, 'Blocked Stop', consent_confirmed=True)
    await wait_for(lambda: any(e.get('session', {}).get('status') == 'recording' for e in events))
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task, 1)
    assert source.closed and runner.source is None and runner.active_id is None
    assert store.get(session['id'])['status'] == 'failed'


@pytest.mark.asyncio
async def test_five_minute_encrypted_tracks_preserve_offset_and_samples(setup):
    store, audio, _events, publish = setup
    source = Source()
    def frames():
        # Accelerated recorder check, not a wall-clock or physical latency test.
        for i in range(3000):
            for stream, offset, level in (('loopback', 0, .02), ('mic', 150, .04)):
                yield AudioFrame(np.full(1600, level, dtype=np.float32), stream,
                                 i * 100 + offset, 0)
            time.sleep(.0001)  # Keep normal queue bounds; don't test overflow here.
    source.frames = frames
    def verify(audio, sid, emit, cancelled):
        for stream, offset, level in (('loopback', 0, .02), ('mic', 150, .04)):
            count = 0
            for index, frame in enumerate(audio.frames(sid, stream)):
                assert frame.t_ms == index * 100 + offset
                assert np.all(frame.pcm == np.float32(level))
                count += frame.pcm.size
            assert count == 300 * 16000
    runner = RecordingRunner(store, audio, lambda *a, **kw: source, publish,
                             transcriber=verify)
    session = runner.start(None, {}, 'Generated five minutes', consent_confirmed=True)
    await asyncio.wait_for(runner.task, 15)
    assert store.get(session['id'])['status'] == 'completed'
    assert store.get(session['id'])['audio_duration_ms'] == 300000
    assert source.closed and runner.active_id is None
    assert len(list(audio.root.rglob('*.slpa'))) == 2


@pytest.mark.asyncio
async def test_source_close_failure_clears_reference_and_releases_slot(setup):
    store, audio, _events, publish = setup
    source = Source()
    original_close = source.close
    def close():
        original_close()
        raise OSError('Native cleanup error')
    source.close = close
    runner = RecordingRunner(store, audio, lambda *a, **kw: source, publish, max_s=.02)
    session = runner.start(None, {}, 'Cleanup failure', consent_confirmed=True)
    await asyncio.wait_for(runner.task, 3)
    assert runner.source is None and runner.active_id is None
    assert source.closed
    assert store.get(session['id'])['status'] == 'failed'
