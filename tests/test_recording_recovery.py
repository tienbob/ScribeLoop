"""M5 authentication, restart, checkpoint and retryable retirement gates."""
from __future__ import annotations

import secrets

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.recording import RecordingRunner
from scribeloop_core.storage.audio import MAGIC, RECORD, AudioIntegrityError, SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


class Keys:
    def __init__(self):
        self.rows = {}
    def create(self, key):
        self.rows[key] = secrets.token_bytes(32)
    def get(self, key):
        return self.rows.get(key)
    def delete(self, key):
        del self.rows[key]


@pytest.fixture()
def state(tmp_path):
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    audio = SessionAudioStore(tmp_path / 'audio',Keys())
    session = store.create_recording('Interrupted meeting',{})
    sid = session['id']
    audio.create_session(sid)
    store.set_audio_paths(sid,audio)
    for stream in ('mic','loopback'):
        writer = audio.writer(sid,stream)
        for t in (150,1150):
            writer.write(AudioFrame(np.full(1600,.05,dtype=np.float32),stream,t,0))
        writer.abort()
    store.recover()
    yield store,audio,sid
    conn.close()


def chunk(start=150,end=250,text='Saved words'):
    return {'start_ms':start,'end_ms':end,'text':text,'is_final':True,'language':'en'}


def test_recovery_fresh_file_identity_original_retained_and_strict_reader(state):
    _,audio,sid = state
    originals = {s:audio.original_path(sid,s).read_bytes() for s in ('mic','loopback')}
    with pytest.raises(AudioIntegrityError):
        list(audio.frames(sid,'mic'))
    report = audio.recover_session(sid)
    for stream,original in originals.items():
        assert audio.original_path(sid,stream).read_bytes() == original
        assert audio.path(sid,stream).name.endswith('.recovered.slpa')
        assert audio.path(sid,stream).read_bytes()[len(MAGIC):len(MAGIC)+16] != original[len(MAGIC):len(MAGIC)+16]
        assert [f.t_ms for f in audio.frames(sid,stream)] == [150,1150]
        assert report[stream]['complete'] is False
        assert report[stream]['end_ms'] == 1250
    assert audio.recover_session(sid)['mic']['complete'] is False  # Retry retains provenance.


@pytest.mark.parametrize('mutation',['tag','header','reorder','length'])
def test_recovery_never_treats_corruption_as_an_ordinary_tail(state,mutation):
    _,audio,sid = state
    path = audio.original_path(sid,'loopback')
    data = bytearray(path.read_bytes())
    head = len(MAGIC)+18+int.from_bytes(data[len(MAGIC)+16:len(MAGIC)+18],'little')
    size = RECORD.size+RECORD.unpack(data[head:head+RECORD.size])[1]
    if mutation == 'tag':
        data[head+RECORD.size+30] ^= 1
    elif mutation == 'header':
        data[len(MAGIC)] ^= 1
    elif mutation == 'length':
        data[head:head+RECORD.size] = RECORD.pack(0,100_000_000)
    else:
        data = data[:head]+data[head+size:]+data[head:head+size]
    path.write_bytes(data)
    with pytest.raises(AudioIntegrityError):
        audio.recover_session(sid)
    assert not list(audio.root.rglob('*.recovered.slpa'))


def test_partial_last_record_recovers_only_authenticated_complete_frames(state):
    _,audio,sid = state
    path = audio.path(sid,'mic')
    data = path.read_bytes()
    path.write_bytes(data[:-13])
    report = audio.recover_session(sid)
    assert report['mic']['tail_bytes'] > 0
    assert report['mic']['samples'] == 1600
    assert [f.t_ms for f in audio.frames(sid,'mic')] == [150]


def test_recovery_rename_failure_is_retryable_without_old_nonce_append(state,monkeypatch):
    _,audio,sid = state
    import os
    replace = os.replace
    calls = []
    def fail_second(source,target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError('power loss before second rename')
        replace(source,target)
    monkeypatch.setattr(os,'replace',fail_second)
    with pytest.raises(OSError):
        audio.recover_session(sid)
    assert len(list(audio.root.rglob('*.recovered.slpa'))) == 1
    monkeypatch.setattr(os,'replace',replace)
    report = audio.recover_session(sid)
    assert all(not r['complete'] for r in report.values())
    assert all(len(list(audio.frames(sid,s))) == 2 for s in ('mic','loopback'))
    assert not list(audio.root.rglob('.*.slpa'))


def test_checkpoint_and_text_are_atomic_and_duplicate_resume_is_rejected(state):
    store,_,sid = state
    store.checkpoint(sid,'mic',recorded_ms=1250)
    rows = store.commit_window(sid,'mic',[chunk()],250)
    assert len(rows) == 1
    assert store.checkpoints(sid)['mic']['confirmed_ms'] == 250
    with pytest.raises(ValueError):
        store.commit_window(sid,'mic',[chunk()],1250)
    assert len(store.segments(sid)) == 1
    assert store.checkpoints(sid)['mic']['confirmed_ms'] == 250
    with pytest.raises(ValueError):
        store.commit_window(sid,'mic',[chunk(250,2000)],2000)
    assert len(store.segments(sid)) == 1


@pytest.mark.asyncio
async def test_recover_resume_opens_no_capture_and_preserves_confirmed_rows(state):
    store,audio,sid = state
    store.checkpoint(sid,'mic',recorded_ms=1250)
    store.commit_window(sid,'mic',[chunk()],250)
    prior = store.segments(sid)[0]['id']
    def forbidden(*a,**kw):
        raise AssertionError('Capture must not reopen')
    def resume(audio,sid,emit,cancelled,done,**kw):
        assert done.is_set()
        assert kw['checkpoints']['mic']['confirmed_ms'] == 250
        for stream in ('mic','loopback'):
            emit({'type':'recorded_checkpoint','stream':stream,'end_ms':1250})
            emit({'type':'confirmed_window','stream':stream,'end_ms':1250,
                  'segments':[chunk(1150,1250,'Remaining words')]})
    events = []
    async def publish(event):
        events.append(event)
    runner = RecordingRunner(store,audio,forbidden,publish,live_transcriber=resume,diarizer=False)
    runner.recover_saved(sid)
    await runner.task
    assert store.get(sid)['status'] == 'interrupted'
    assert store.get(sid)['recovery']['mic']['complete'] is False
    runner.resume(sid,language='en')
    await runner.task
    assert store.get(sid)['status'] == 'completed'
    assert store.segments(sid)[0]['id'] == prior
    assert len(store.segments(sid)) == 3
    # A second resume has no remaining audio and must not duplicate saved text.
    def empty(audio,sid,emit,cancelled,done,**kw):
        assert all(c['confirmed_ms'] == 1250 for c in kw['checkpoints'].values())
    runner.live_transcriber = empty
    runner.resume(sid,language='en')
    await runner.task
    assert len(store.segments(sid)) == 3


@pytest.mark.asyncio
async def test_delete_fault_keeps_retryable_tombstone_and_removes_dependents(state,monkeypatch):
    store,audio,sid = state
    store.checkpoint(sid,'mic',recorded_ms=1250)
    store.commit_window(sid,'mic',[chunk()],250)
    store.create_transcript_notes(sid)
    events = []
    async def publish(event):
        events.append(event)
    runner = RecordingRunner(store,audio,None,publish,diarizer=False)
    original = audio.delete_session
    def fail(sid):
        audio.forget_session_key(sid)
        raise OSError('cleanup failed after key retirement')
    monkeypatch.setattr(audio,'delete_session',fail)
    runner.delete_saved(sid)
    await runner.task
    assert store.get(sid)['status'] == 'deleting'
    assert not audio.keys.rows
    monkeypatch.setattr(audio,'delete_session',original)
    runner.delete_saved(sid)
    await runner.task
    assert store.get(sid) is None
    for table in ('segments','summaries','session_streams','recording_checkpoints','events'):
        assert store.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert not list(audio.root.iterdir())


def test_retention_only_includes_old_idle_authorized_meetings(state):
    store,_,sid = state
    with store.conn:
        store.conn.execute('UPDATE sessions SET started_at=1 WHERE id=?',(sid,))
    fresh = store.create_recording('Active',{})
    assert store.retention_candidates(30) == [sid]
    assert fresh['id'] not in store.retention_candidates(1)
    with pytest.raises(ValueError):
        store.retention_candidates(0)


def test_v1_migration_preserves_existing_session(tmp_path):
    from scribeloop_core.storage.db import DDL
    conn = connect(tmp_path/'old.db')
    conn.executescript(DDL)
    conn.execute('PRAGMA user_version=1')
    conn.execute("INSERT INTO sessions(id,title,status) VALUES ('legacy','Keep me','completed')")
    conn.commit()
    migrate(conn)
    migrate(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 12
    assert conn.execute("SELECT title FROM sessions WHERE id='legacy'").fetchone()[0] == 'Keep me'
    conn.close()


def test_checkpoint_write_fault_rolls_back_new_segments(state):
    import sqlite3
    store,_,sid = state
    store.checkpoint(sid,'mic',recorded_ms=1250)
    store.conn.execute("CREATE TRIGGER checkpoint_fault BEFORE UPDATE OF confirmed_ms ON recording_checkpoints "
                       "BEGIN SELECT RAISE(ABORT,'injected checkpoint failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_window(sid,'mic',[chunk()],250)
    assert not store.segments(sid)
    assert store.checkpoints(sid)['mic']['confirmed_ms'] == 0
