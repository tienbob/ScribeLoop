"""Actual streamed commits must support FK-backed person links and cited notes."""
import pytest
from scribeloop_core.api.desktop_fixture import FixtureKeys
from scribeloop_core.llm.grounded import transcript_hash
from scribeloop_core.speakers.registry import SpeakerRegistry
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import encrypted_database
from scribeloop_core.storage.sessions import SessionStore


def stream(store):
    sid=store.create_recording('Stream provenance',{'provider':'deepgram','model':'nova-3'})['id']
    store.checkpoint(sid,'mic',recorded_ms=1000)
    rows=store.commit_window(sid,'mic',[{'text':'The public test succeeded.','is_final':True,
        'start_ms':0,'end_ms':1000,'speaker_key':'deepgram:'+('a'*32)+':mic:0'}],1000)
    store.transition(sid,'completed')
    return sid,rows


def assert_naming_and_summary(store,sid):
    roster=store.speaker_roster(sid,1)
    SpeakerRegistry(store).name_speaker(sid,1,roster[0]['id'],name='Test speaker')
    rows=store.segments(sid)
    assert rows[0]['speaker_label']=='Test speaker'
    value={'overview':{'text':'The public test succeeded.','segment_ids':[rows[0]['id']]},
           'key_points':[],'decisions':[],'action_items':[],'open_questions':[],'uncertainties':[]}
    store.save_grounded_summary(sid,1,transcript_hash(rows),value,provider='test',model='test')
    assert not store.grounded_summary(sid)['stale']


def test_streamed_sqlcipher_commit_supports_naming_and_summary(tmp_path,require_sqlcipher):
    with encrypted_database(tmp_path/'stream.sqlcipher',FixtureKeys()) as conn:
        store=SessionStore(conn)
        sid,_rows=stream(store)
        revisions=store.revisions(sid)
        assert len(revisions)==1 and revisions[0]['provider']=='deepgram' and revisions[0]['model']=='nova-3'
        assert_naming_and_summary(store,sid)
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_v10_missing_stream_provenance_is_repaired_idempotently(tmp_path,require_sqlcipher):
    with encrypted_database(tmp_path/'repair.sqlcipher',FixtureKeys()) as conn:
        store=SessionStore(conn)
        sid,_rows=stream(store)
        with conn:
            conn.execute('DELETE FROM transcript_revisions WHERE session_id=?',(sid,))
            conn.execute('PRAGMA user_version=10')
        assert not store.revisions(sid)
        before=store.segments(sid)
        migrate(conn)
        assert store.segments(sid)==before
        revision=store.revisions(sid)[0]
        assert revision['origin']=='confirmed' and revision['provider']=='deepgram'
        assert conn.execute('PRAGMA user_version').fetchone()[0]==11
        migrate(conn)
        assert len(store.revisions(sid))==1
        assert_naming_and_summary(store,sid)


def test_failed_stream_commit_rolls_back_provenance_and_checkpoint(tmp_path,require_sqlcipher):
    with encrypted_database(tmp_path/'rollback.sqlcipher',FixtureKeys()) as conn:
        store=SessionStore(conn)
        sid=store.create_recording('Rejected stream',{})['id']
        store.checkpoint(sid,'mic',recorded_ms=1000)
        with pytest.raises(ValueError):
            store.commit_window(sid,'mic',[{'text':'Invalid','is_final':True,'start_ms':0,'end_ms':2000}],1000)
        assert not store.revisions(sid) and not store.segments(sid)
        assert store.checkpoints(sid)['mic']['confirmed_ms']==0
