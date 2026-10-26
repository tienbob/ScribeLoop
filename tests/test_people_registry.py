"""Named people, explicit enrollment, calibration gates and complete derived purge."""
import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.speakers.embeddings import calibrate, extract
from scribeloop_core.speakers.registry import SpeakerRegistry
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.retained_audio import RetainedAudio
from scribeloop_core.storage.sessions import SessionStore
from scribeloop_core.storage.vectors import SqliteVecIndex
from test_recording import Keys


@pytest.fixture()
def state(tmp_path):
    conn=migrate(connect(tmp_path/'people.db'))
    store=SessionStore(conn)
    audio=SessionAudioStore(tmp_path/'audio',Keys())
    retained=RetainedAudio(store,audio)
    registry=SpeakerRegistry(store,audio=retained)
    sid=store.create_recording('Meeting',{'scope':'microphone','auto_title':True})['id']
    audio.create_session(sid)
    store.set_audio_paths(sid,audio)
    with audio.writer(sid,'mic') as writer:
        for second in range(32):
            writer.write(AudioFrame(np.ones(16000,dtype='float32')*.1,'mic',second*1000,0))
    row=store.append(sid,{'stream':'mic','text':'We discussed the project.','start_ms':0,'end_ms':32000,
        'is_final':True,'speaker_key':f'deepgram:{"a"*32}:mic:0'},stream='mic')
    store.transition(sid,'completed')
    yield store,registry,sid,row,tmp_path/'people.db'
    conn.close()


def test_linking_does_not_enable_learning_and_manual_rename_unlinks(state):
    store,registry,sid,row,_=state
    person=registry.create('Alice')
    registry.link(sid,1,row['local_speaker_id'],person)
    assert store.segments(sid)[0]['speaker_label']=='Alice'
    assert registry.list()[0]['learn_enabled']==0
    assert not store.conn.execute('SELECT 1 FROM speaker_embeddings').fetchone()
    store.correct_speaker(sid,1,row['local_speaker_id'],label='Someone else')
    assert store.segments(sid)[0]['speaker_label']=='Someone else'
    assert not store.conn.execute('SELECT 1 FROM person_links').fetchone()


def test_enrollment_requires_opt_in_review_source_and_model_provenance(state):
    store,registry,sid,row,_=state
    person=registry.create('Alice')
    speaker=row['local_speaker_id']
    registry.link(sid,1,speaker,person)
    values=np.ones(16,dtype='float32')
    for reviewed in (False,True):
        with pytest.raises(ValueError):
            registry.enroll(person,sid,1,speaker,0,32000,values,model='test-cloud',version='1',reviewed=reviewed)
    registry.learning(person,True)
    with pytest.raises(ValueError):
        registry.enroll(person,sid,1,speaker,0,10000,values,model='test-cloud',version='1',reviewed=True)
    identity=registry.enroll(person,sid,1,speaker,0,32000,values,model='test-cloud',version='1',reviewed=True)
    assert store.conn.execute('SELECT embedding_id FROM exemplar_provenance').fetchone()[0]==identity
    assert registry.suggest(values,{'suggestions_ready':False},model='test-cloud',version='1')==[]
    report={'suggestions_ready':True,'model':'test-cloud','version':'1','threshold':.99}
    assert registry.suggest(values,report,model='test-cloud',version='1')[0]['automatic'] is False
    registry.learning(person,False)
    assert registry.suggest(values,report,model='test-cloud',version='1')==[]
    with pytest.raises(ValueError):
        extract(np.ones(480000,dtype='float32'))


@pytest.mark.asyncio
async def test_forget_purges_exemplars_links_vectors_names_notes_and_cache(state):
    from scribeloop_core.llm.rolling import RollingNotes
    from test_rolling_notes import Provider

    store,registry,sid,row,path=state
    person=registry.create('Alice')
    registry.link(sid,1,row['local_speaker_id'],person)
    registry.learning(person,True)
    registry.enroll(person,sid,1,row['local_speaker_id'],0,32000,np.ones(16,dtype='float32'),model='test',version='1',reviewed=True)
    notes=await RollingNotes(store,Provider()).run(sid,store.segments(sid),revision=1)
    notes['overview']['text']='Alice discussed the project'
    _,fingerprint,_=store.summary_input(sid,1,build_messages=False)
    store.save_grounded_summary(sid,1,fingerprint,notes,provider='test',model='test')
    index=SqliteVecIndex(store.conn,dim=16)
    index.add(sid,0,32000,[row['id']],'Alice spoke',np.ones(16,dtype='float32').tobytes())
    assert registry.forget(person)==[sid]
    for table in ('speakers','speaker_embeddings','exemplar_provenance','person_links','person_link_history','grounded_summaries','summary_jobs','embeddings_chunks','embeddings_chunks_vec'):
        assert store.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
    assert store.segments(sid)[0]['text']=='We discussed the project.'
    assert 'Alice' not in store.notes(sid)
    assert b'Alice' not in path.read_bytes()


def test_forget_transaction_rolls_back_if_derived_notes_cannot_be_rebuilt(state,monkeypatch):
    store,registry,sid,row,_=state
    person=registry.create('Alice')
    registry.link(sid,1,row['local_speaker_id'],person)
    def fail(*args):
        raise OSError('Simulated storage failure')
    monkeypatch.setattr(store,'_write_transcript_notes',fail)
    with pytest.raises(OSError):
        registry.forget(person)
    assert registry.list() and store.segments(sid)[0]['speaker_label']=='Alice'


def test_calibration_requires_independent_people_and_sufficient_heldout_evidence():
    def pair(score,same,prefix,condition='clean'):
        return {'score':score,'same_person':same,'people':[prefix+'a',prefix+'a' if same else prefix+'b'],'condition':condition}
    training=[pair(.95,True,'train'),pair(.2,False,'train')]
    heldout=[pair(.9,True,'heldout'),pair(.1,False,'heldout')]
    report=calibrate(training,heldout,model='test-cloud',version='1')
    assert report['automatic_naming'] is False and not report['suggestions_ready']
    assert report['heldout']['false_matches']==0
    with pytest.raises(ValueError,match='independent'):
        calibrate(training,training,model='test-cloud',version='1')
    heldout=[pair(.99,True,'heldout'+str(i)) for i in range(40)]+[pair(.1,False,'heldout'+str(i)) for i in range(400)]
    assert calibrate(training,heldout,model='test-cloud',version='1')['suggestions_ready']


def test_forget_also_purges_names_from_previously_unlinked_meetings(state):
    store,registry,sid,row,path=state
    person=registry.create('Alice')
    registry.link(sid,1,row['local_speaker_id'],person)
    registry.link(sid,1,row['local_speaker_id'],None)
    assert 'Alice' not in store.notes(sid)
    assert store.conn.execute("SELECT COUNT(*) FROM summaries WHERE content_md LIKE '%Alice%'").fetchone()[0]
    assert registry.forget(person)==[sid]
    assert b'Alice' not in path.read_bytes()


def test_api_people_actions_update_all_clients_and_require_forget_confirmation(tmp_path):
    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from test_recording_api import connection, receive

    app,token=create_app()
    with TestClient(app,base_url='http://127.0.0.1') as client, connection(client) as one, connection(client) as two:
        def saved():
            store=app.state.store
            sid=store.create_recording('People',{})['id']
            row=store.append(sid,{'text':'Words','start_ms':0,'end_ms':1000,'is_final':True,
                'speaker_key':f'deepgram:{"a"*32}:loopback:0'})
            store.transition(sid,'completed')
            return sid,row['local_speaker_id']
        sid,speaker=client.portal.call(saved)
        for ws in (one,two):
            ws.send_text(token)
            assert ws.receive_json()['people']==[]
        one.send_json({'action':'create_person','name':'Alex'})
        person=receive(one,'people_changed')['people'][0]['id']
        assert receive(two,'people_changed')['people'][0]['learn_enabled']==0
        one.send_json({'action':'link_person','person_id':person,'session_id':sid,'revision':1,'speaker_id':speaker})
        for ws in (one,two):
            assert receive(ws,'people_changed')['segments'][sid][0]['speaker_label']=='Alex'
        one.send_json({'action':'forget_person','person_id':person,'confirmed':False})
        receive(one,'error')
        one.send_json({'action':'forget_person','person_id':person,'confirmed':True})
        for ws in (one,two):
            event=receive(ws,'people_changed')
            assert event['people']==[]
            assert event['segments'][sid][0]['speaker_label']!='Alex'


def test_whole_meeting_deletion_also_purges_derived_vector_rows(state):
    store,registry,sid,row,_=state
    person=registry.create('Alex')
    registry.link(sid,1,row['local_speaker_id'],person)
    index=SqliteVecIndex(store.conn,dim=16)
    index.add(sid,0,32000,[row['id']],'Words',np.ones(16,dtype='float32').tobytes())
    store.finish_delete(sid)
    assert store.conn.execute('SELECT COUNT(*) FROM embeddings_chunks_vec').fetchone()[0]==0
    assert store.conn.execute('SELECT COUNT(*) FROM person_link_history').fetchone()[0]==0
    assert registry.list(), 'Meeting deletion does not delete the global person'
