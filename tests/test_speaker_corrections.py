"""Manual speaker mappings are revision-scoped and never rewrite model IDs."""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


@pytest.fixture()
def saved(tmp_path):
    conn = migrate(connect(tmp_path/'saved.db'))
    store = SessionStore(conn)
    sid = store.create_recording('test',{'provider':'deepgram','model':'nova-3'})['id']
    ids = []
    for stream,gen,number in [('loopback','a',0),('loopback','a',1),('loopback','a',2),
                              ('mic','a',0),('loopback','b',0)]:
        row = store.append(sid,{'text':f'words {len(ids)}','start_ms':len(ids)*100,
            'end_ms':(len(ids)+1)*100,'is_final':True,
            'speaker_key':f'deepgram:{gen*32}:{stream}:{number}'},stream=stream)
        ids.append(row['local_speaker_id'])
    store.transition(sid,'completed')
    yield store,sid,ids
    conn.close()


def test_rename_merge_chain_and_revision_isolation(saved):
    store,sid,ids = saved
    raw = [r['local_speaker_id'] for r in store.segments(sid)]
    store.correct_speaker(sid,1,ids[0],label='Alex')
    store.correct_speaker(sid,1,ids[1],merge_target=ids[0])
    store.correct_speaker(sid,1,ids[0],merge_target=ids[2])
    store.correct_speaker(sid,1,ids[2],label='Minh')
    rows = store.segments(sid)
    assert [r['local_speaker_id'] for r in rows] == raw
    assert [r['display_speaker_id'] for r in rows[:3]] == [ids[2]]*3
    assert [r['speaker_label'] for r in rows[:3]] == ['Minh']*3
    assert 'Minh' in store.notes(sid) and 'Alex' not in store.notes(sid)
    store.revise_transcript(sid,[{'text':'new','start_ms':0,'end_ms':100,'is_final':True,
        'stream':'loopback','speaker_key':f'deepgram:{"c"*32}:loopback:0'}],provider='deepgram')
    assert store.segments(sid)[0]['speaker_label'] != 'Minh'
    assert store.segments(sid,revision=1)[0]['speaker_label'] == 'Minh'
    with pytest.raises(ValueError):
        store.correct_speaker(sid,1,ids[2],label='stale')
    store.finish_delete(sid)
    assert not store.conn.execute('SELECT * FROM revision_speaker_edits').fetchall()


@pytest.mark.parametrize('label',['','   ','x'*81,'Alex\nBob','\ud800'])
def test_invalid_names_leave_no_edits(saved,label):
    store,sid,ids = saved
    with pytest.raises((ValueError,UnicodeError)):
        store.correct_speaker(sid,1,ids[0],label=label)
    assert not store.conn.execute('SELECT * FROM revision_speaker_edits').fetchall()


@pytest.mark.parametrize('target',[0,3,4])
def test_self_track_and_generation_merges_are_rejected(saved,target):
    store,sid,ids = saved
    with pytest.raises(ValueError):
        store.correct_speaker(sid,1,ids[0],merge_target=ids[target])


def test_active_and_unknown_speakers_are_rejected(saved):
    store,sid,ids = saved
    with pytest.raises(ValueError):
        store.correct_speaker(sid,1,'unknown',label='Alex')
    store.transition(sid,'processing')
    with pytest.raises(ValueError):
        store.correct_speaker(sid,1,ids[0],label='Alex')


def test_note_failure_rolls_back_edit(saved):
    store,sid,ids = saved
    store.create_transcript_notes(sid)
    before = store.notes(sid)
    store.conn.execute("CREATE TRIGGER reject_notes BEFORE INSERT ON summaries "
                       "BEGIN SELECT RAISE(ABORT,'test fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.correct_speaker(sid,1,ids[0],label='Alex')
    assert store.segments(sid)[0]['speaker_label'] == 'Speaker 1'
    assert store.notes(sid) == before


def test_api_corrections_and_reconnect(saved,tmp_path):
    store,sid,ids = saved
    path = store.conn.execute('PRAGMA database_list').fetchone()[2]
    app,token = create_app(storage='public-demo',database=path,fixtures={},backend_factory=lambda:None)
    with TestClient(app) as client:
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            ws.send_text(token)
            assert ws.receive_json()['type'] == 'snapshot'
            ws.send_json({'action':'rename_speaker','session_id':sid,'revision':1,
                          'speaker_id':ids[0],'label':'<script>Alex</script>'})
            result = ws.receive_json()
            assert result['type'] == 'speakers_corrected'
            assert result['segments'][0]['speaker_label'] == '<script>Alex</script>'
            ws.send_json({'action':'merge_speaker','session_id':sid,'revision':1,
                          'speaker_id':ids[1],'merge_target':ids[0]})
            assert ws.receive_json()['segments'][1]['display_speaker_id'] == ids[0]
            ws.send_json({'action':'rename_speaker','session_id':sid,'revision':2,
                          'speaker_id':ids[0],'label':'stale'})
            assert ws.receive_json()['type'] == 'error'
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['segments'][sid][0]['speaker_label'] == '<script>Alex</script>'
            assert '<script>Alex</script>' in snapshot['notes'][sid]
