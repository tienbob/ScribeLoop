"""Generated encrypted audio: expiry never deletes text; playback never uploads."""
import asyncio
import io
import threading
import wave

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.retained_audio import RetainedAudio
from scribeloop_core.storage.sessions import SessionStore
from test_recording import Keys


@pytest.fixture()
def retained(tmp_path):
    conn=migrate(connect(tmp_path/'metadata.db'))
    store=SessionStore(conn)
    audio=SessionAudioStore(tmp_path/'audio',Keys())
    yield RetainedAudio(store,audio)
    conn.close()


def seed(service,*,old=True,metadata=None):
    store,audio=service.store,service.audio
    sid=store.create_recording('Saved meeting',{'provider':'deepgram','model':'nova-3',**(metadata or {})})['id']
    audio.create_session(sid)
    store.set_audio_paths(sid,audio)
    for stream in ('mic','loopback'):
        writer=audio.writer(sid,stream)
        writer.write(AudioFrame(np.ones(1600,dtype='float32')*.25,stream,0,0))
        writer.write(AudioFrame(np.ones(1600,dtype='float32')*.5,stream,200,0))
        writer.close()
    store.append(sid,{'text':'Keep this transcript','start_ms':0,'end_ms':300,'is_final':True})
    store.transition(sid,'completed')
    store.create_transcript_notes(sid)
    if old:
        with store.conn:
            store.conn.execute('UPDATE sessions SET started_at=?,ended_at=? WHERE id=?',
                (store._now()-32*86400000,store._now()-31*86400000,sid))
    return sid


@pytest.mark.asyncio
async def test_expiry_only_removes_audio_and_key_preserving_transcript_and_notes(retained):
    sid=seed(retained)
    rows=retained.store.segments(sid)
    notes=retained.store.notes(sid)
    assert await retained.cleanup()==[sid]
    assert retained.audio.keys.get('audio:'+sid) is None
    assert not (retained.audio.root/sid).exists()
    assert retained.store.segments(sid)==rows
    assert retained.store.notes(sid)==notes
    assert retained.status(sid)['expired_at'] is not None
    assert not retained.status(sid)['available']
    assert await retained.cleanup()==[]
    with pytest.raises(ValueError):
        retained.store.archive_audio(sid,True)
    with pytest.raises(ValueError):
        await retained.playback(sid,'mic')
    retained.store.finish_delete(sid)
    assert retained.store.get(sid) is None


@pytest.mark.asyncio
async def test_archive_survives_restart_unarchive_expires_and_recent_audio_is_kept(retained):
    sid=seed(retained)
    recent=seed(retained,old=False)
    retained.store.archive_audio(sid,True)
    restarted=RetainedAudio(SessionStore(retained.store.conn),retained.audio)
    assert restarted.status(sid)['archived'] and restarted.status(sid)['expires_at'] is None
    assert await restarted.cleanup()==[]
    restarted.store.archive_audio(sid,False)
    assert await restarted.cleanup()==[sid]
    assert restarted.status(recent)['available']


@pytest.mark.asyncio
async def test_failed_key_first_cleanup_is_retryable(retained,monkeypatch):
    sid=seed(retained)
    original=retained.audio.delete_session
    def fail(sid):
        retained.audio.forget_session_key(sid)
        raise OSError('simulated unlink failure')
    monkeypatch.setattr(retained.audio,'delete_session',fail)
    with pytest.raises(OSError):
        await retained.cleanup()
    assert retained.store.audio_policy(sid)['expired_at'] is None
    assert not retained.status(sid)['available']
    assert retained.store.notes(sid)
    monkeypatch.setattr(retained.audio,'delete_session',original)
    assert await retained.cleanup()==[sid]


@pytest.mark.asyncio
async def test_playback_authenticates_complete_track_and_preserves_recorder_gaps(retained):
    sid=seed(retained,metadata={'scope':'microphone'})
    assert retained.status(sid)['tracks']==['mic']
    data=await retained.playback(sid,'mic')
    with wave.open(io.BytesIO(data),'rb') as wav:
        assert wav.getframerate()==16000 and wav.getnchannels()==1
        samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
    assert len(samples)==4800
    assert np.all(samples[1600:3200]==0), 'Playback uses recorder timestamps'
    assert samples[0]>0 and samples[-1]>samples[0]
    path=retained.audio.path(sid,'mic')
    damaged=bytearray(path.read_bytes())
    damaged[-1]^=1
    path.write_bytes(damaged)
    with pytest.raises(ValueError):
        await retained.playback(sid,'mic')
    assert not retained.busy


@pytest.mark.asyncio
async def test_playback_lease_blocks_cleanup_even_after_client_cancellation(retained,monkeypatch):
    sid=seed(retained)
    entered,release=threading.Event(),threading.Event()
    original=retained._wav
    def held(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(retained,'_wav',held)
    task=asyncio.create_task(retained.playback(sid,'mic'))
    assert await asyncio.to_thread(entered.wait,3)
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert retained.busy
        assert await retained.cleanup()==[]
        assert retained.audio.keys.get('audio:'+sid)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not retained.busy
    assert await retained.cleanup()==[sid]


def test_api_startup_expiry_archive_and_playback_do_not_call_cloud(tmp_path,require_sqlcipher):
    import base64

    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
    from scribeloop_core.pipeline.model_selection import CloudRecordingRouter
    from test_recording_api import connection, receive

    calls=[]
    keys=Keys()
    def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('No provider request expected')
    def recording(store,audio,source,publish):
        return CloudRecordingRouter(store,audio,forbidden,publish,
            cloud_config=DeepgramConfig(api_key='test-key',connector=forbidden))
    app,token=create_app(storage='encrypted',database=tmp_path/'app.sqlcipher',key_store=keys,
                         recording_factory=recording)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        expired=client.portal.call(seed,app.state.audio)
        archived=client.portal.call(seed,app.state.audio)
        recent=client.portal.call(lambda:seed(app.state.audio,old=False))
        client.portal.call(app.state.store.archive_audio,archived,True)
        client.portal.call(app.state.store.accept_agreement,app.state.store.AGREEMENT_VERSION)
    with TestClient(app,base_url='http://127.0.0.1') as client, connection(client) as ws:
        async def await_expiry():
            async with asyncio.timeout(3):
                while app.state.audio.status(expired)['available']:
                    await asyncio.sleep(.01)
        client.portal.call(await_expiry)
        ws.send_text(token)
        snapshot=ws.receive_json()
        assert snapshot['audio_states'][expired]['expired_at'] is not None
        assert snapshot['segments'][expired] and snapshot['notes'][expired]
        assert snapshot['audio_states'][archived]['archived']
        assert snapshot['audio_states'][archived]['available']
        ws.send_json({'action':'play_audio','session_id':recent,'stream':'mic'})
        event=receive(ws,'audio_playback')
        with wave.open(io.BytesIO(base64.b64decode(event['wav_base64'])),'rb') as wav:
            assert wav.getnframes()==4800
        ws.send_json({'action':'play_audio','session_id':recent,'stream':'mic'})
        assert receive(ws,'audio_playback')['session_id']==recent, 'Delivery releases the per-client playback slot'
        ws.send_json({'action':'archive_audio','session_id':recent,'archived':True})
        assert receive(ws,'audio_state')['audio']['archived']
        ws.send_json({'action':'archive_audio','session_id':expired,'archived':True})
        receive(ws,'error')
        for action in ('resume','reprocess'):
            ws.send_json({'action':action,'session_id':expired,'language':'en'})
            receive(ws,'error')
        assert not calls
        ws.send_json({'action':'delete','session_id':expired,'confirmed':True})
        receive(ws,'session_deleted')
        assert client.portal.call(app.state.store.get,expired) is None


@pytest.mark.asyncio
async def test_playback_rejects_audio_beyond_preview_bound(retained):
    sid=seed(retained)
    path=retained.audio.original_path(sid,'mic')
    path.unlink()
    writer=retained.audio.writer(sid,'mic')
    writer.write(AudioFrame(np.ones(1600,dtype='float32'),'mic',10_806_000,0))
    writer.close()
    with pytest.raises(ValueError,match='timeline'):
        await retained.playback(sid,'mic')


@pytest.mark.parametrize('fail',[False,True])
def test_explicit_rerun_replaces_transcript_then_generates_notes_once(tmp_path,require_sqlcipher,fail):
    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.llm.schemas import GroundedSummary
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
    from scribeloop_core.pipeline.model_selection import CloudRecordingRouter
    from test_deepgram_meeting import Socket
    from test_recording_api import connection, receive

    sockets=[]
    summaries=[]
    def connector(*args,**kwargs):
        sock=Socket(fail=fail)
        sockets.append(sock)
        return sock
    def recording(store,audio,source,publish):
        def never_capture(*args,**kwargs):
            raise AssertionError('A saved rerun must not open capture')
        return CloudRecordingRouter(store,audio,never_capture,publish,
            cloud_config=DeepgramConfig(api_key='test-key',connector=connector))
    def draft(text):
        row=app.state.store.segments(sid)[0]
        return GroundedSummary.model_validate({'title':{'text':text,'segment_ids':[row['id']]},
            'overview':{'text':text,'segment_ids':[row['id']]},'key_points':[],
            'decisions':[],'action_items':[],'open_questions':[],'uncertainties':[]})
    class Provider:
        provider='Test summary'
        model='Test model'
        def public(self):
            return {'configured':True,'cloud':True,'provider':self.provider,'model':self.model,
                    'endpoint':'https://test.invalid/v1'}
        async def generate(self,messages):
            summaries.append(messages)
            return draft('Fresh topic')
    app,token=create_app(storage='encrypted',database=tmp_path/'rerun.sqlcipher',key_store=Keys(),
        recording_factory=recording,summary_provider=Provider())
    with TestClient(app,base_url='http://127.0.0.1') as client, connection(client) as ws:
        sid=client.portal.call(lambda:seed(app.state.audio,old=False,metadata={
            'auto_title':True,'auto_workflow':True,'auto_summary':Provider().public()}))
        before=client.portal.call(app.state.store.segments,sid)
        def old_summary():
            _,fingerprint,_=app.state.store.summary_input(sid,1)
            return app.state.store.save_grounded_summary(sid,1,fingerprint,draft('Previous topic'),
                provider='Test summary',model='Test model')
        previous=client.portal.call(old_summary)
        client.portal.call(app.state.store.accept_agreement,app.state.store.AGREEMENT_VERSION)
        ws.send_text(token)
        ws.receive_json()
        assert not sockets and not summaries, 'Opening history never runs providers'
        ws.send_json({'action':'reprocess','session_id':sid,'language':'en'})
        if fail:
            receive(ws,'session','failed')
            assert client.portal.call(app.state.store.segments,sid)==before
            assert client.portal.call(app.state.store.grounded_summary,sid)==previous
            assert not summaries
        else:
            receive(ws,'summary_ready')
            assert len(summaries)==1
            assert len(sockets)==2, 'Explicit rerun streams each saved track once'
            current=client.portal.call(app.state.store.get,sid)
            assert current['transcript_revision']==2
            assert current['title']=='Fresh topic'
            assert client.portal.call(app.state.store.grounded_summary,sid)['stale'] is False
            def attempts():
                return app.state.store.conn.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id=? AND type='manual_rerun_summary_attempt'",
                    (sid,)).fetchone()[0]
            assert client.portal.call(attempts)==1
        assert all(sock.closed for sock in sockets) if not fail else len(sockets)<=2


@pytest.mark.asyncio
async def test_cleanup_rechecks_authorization_before_each_recording(retained):
    first=seed(retained)
    second=seed(retained)
    accepted=[True]
    async def revoke(sid):
        accepted[0]=False
    removed=await retained.cleanup(authorized=lambda:accepted[0],on_expired=revoke)
    assert len(removed)==1
    remaining=second if removed[0]==first else first
    assert retained.status(remaining)['available']


@pytest.mark.asyncio
async def test_cleanup_rejects_redirected_recording_directory(retained,tmp_path):
    sid=seed(retained)
    directory=retained.audio.root/sid
    outside=tmp_path/'redirected'
    directory.rename(outside)
    directory.symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):
        await retained.cleanup()
    assert retained.audio.keys.get('audio:'+sid) is not None
    assert (outside/'mic.slpa').is_file()
    assert not retained.status(sid)['available']
