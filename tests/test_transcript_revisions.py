"""Revision history survives upgrades, failed replacement and reconnects."""
import sqlite3

import pytest
from scribeloop_core.storage.db import DDL, connect, migrate
from scribeloop_core.storage.sessions import SessionStore


def chunk(text='original', **extra):
    return {'text':text,'start_ms':0,'end_ms':100,'is_final':True,**extra}


@pytest.fixture()
def store():
    conn = migrate(connect(':memory:'))
    yield SessionStore(conn)
    conn.close()


def test_history_and_provenance_are_atomic_and_old_text_is_readable(store):
    sid = store.create_recording('test',{'provider':'deepgram','model':'nova-3'})['id']
    assert store.append(sid,chunk(is_final=False)) is None
    assert store.revisions(sid) == []
    original = store.append(sid,chunk())
    assert store.get(sid)['transcript_revision'] == 1
    revision = store.revise_transcript(sid,[chunk('replacement',stream='mic')],
                                      provider='local-moss',model='MOSS Q6_K')
    assert revision == 2
    history = store.revisions(sid)
    assert [(r['revision'],r['provider'],r['model'],r['selected']) for r in history] == [
        (2,'local-moss','MOSS Q6_K',1),(1,'deepgram','nova-3',0)]
    assert store.segments(sid,revision=1) == [original]
    assert store.segments(sid)[0]['text'] == 'replacement'
    # Fail after the revision row and first segment were inserted.
    store.conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON segments "
                       "WHEN NEW.text='reject' BEGIN SELECT RAISE(ABORT,'test fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.revise_transcript(sid,[chunk('good',stream='mic'),chunk('reject',stream='mic')],
                                provider='deepgram',model='nova-3')
    assert store.revisions(sid) == history
    assert store.get(sid)['transcript_provider'] == 'local-moss'
    assert store.segments(sid)[0]['text'] == 'replacement'
    store.finish_delete(sid)
    assert not store.revisions(sid)
    assert not store.conn.execute('SELECT * FROM segments').fetchall()


def test_legacy_migration_preserves_ids_and_does_not_guess_provenance(tmp_path):
    path = tmp_path/'legacy.db'
    conn = connect(path)
    conn.executescript(DDL)
    conn.execute("INSERT INTO sessions(id,source,status) VALUES ('old','meeting','completed')")
    conn.execute("INSERT INTO segments(id,session_id,text,revision) VALUES ('a','old','first',1)")
    conn.execute("INSERT INTO segments(id,session_id,text,revision) VALUES ('b','old','second',2)")
    conn.execute('PRAGMA user_version=1')
    conn.commit()
    conn.close()
    conn = migrate(connect(path))
    migrate(conn)  # Idempotent reopen.
    store = SessionStore(conn)
    history = store.revisions('old')
    assert [(r['origin'],r['provider'],r['model'],r['created_at']) for r in history] == [
        ('legacy',None,None,None),('legacy',None,None,None)]
    assert [r['id'] for r in store.segments('old',revision=1)] == ['a']
    assert [r['id'] for r in store.segments('old')] == ['b']
    conn.close()


@pytest.mark.parametrize('revision',[0,-1,True,'1',1.5])
def test_invalid_revision_is_rejected(store,revision):
    sid = store.create_recording('test',{})['id']
    with pytest.raises(ValueError):
        store.segments(sid,revision=revision)
    with pytest.raises(ValueError):
        store.append(sid,chunk(),revision=revision)
    assert not store.revisions(sid)


def test_resume_extends_selected_revision_without_mutating_history(store):
    sid = store.create_recording('test',{'provider':'deepgram','model':'nova-3'})['id']
    original = store.append(sid,chunk())
    store.revise_transcript(sid,[chunk('replacement',stream='mic')],
                            provider='deepgram',model='nova-3')
    resumed = store.append(sid,chunk('resumed',start_ms=100,end_ms=200),stream='mic')
    assert resumed['revision'] == 2
    assert resumed['source'] == 'stream'
    assert [r['text'] for r in store.segments(sid)] == ['replacement','resumed']
    assert store.segments(sid,revision=1) == [original]
    assert store.revisions(sid)[0]['segment_count'] == 2


def test_partial_replacement_keeps_previous_selection(store):
    sid = store.create_recording('test',{})['id']
    original = store.append(sid,chunk())
    with pytest.raises(ValueError,match='confirmed'):
        store.revise_transcript(sid,[chunk('partial',stream='mic',is_final=False)])
    assert store.segments(sid) == [original]
    assert len(store.revisions(sid)) == 1


def test_upgrade_can_resume_before_schema_version_commit(store):
    sid = store.create_recording('test',{'provider':'deepgram','model':'nova-3'})['id']
    store.append(sid,chunk())
    before = store.revisions(sid)
    store.conn.execute('PRAGMA user_version=2')
    store.conn.commit()
    migrate(store.conn)
    assert store.revisions(sid) == before
