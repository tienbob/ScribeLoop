"""Default cloud app lifecycle; all outbound requests are mocked."""
from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.cloud_meeting import (
    Budget,
    CloudRecordingRunner,
    GroqConfig,
    run_cloud,
)
from scribeloop_core.pipeline.groq_asr import GroqError
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_recording import Keys


@pytest.fixture()
def state(tmp_path):
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    audio = SessionAudioStore(tmp_path/'audio',Keys())
    events = []
    async def publish(event):
        events.append(event)
    yield store,audio,events,publish
    conn.close()


async def wait_for(predicate):
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(.01)


class ControlledSource:
    def __init__(self):
        self.stopped = threading.Event()
        self.closed = False
    def frames(self):
        for i in range(120):
            for stream in ('loopback','mic'):
                yield AudioFrame(np.full(1600,.02,dtype=np.float32),stream,i*100+150,0)
        self.stopped.wait(4)
    def request_stop(self):
        self.stopped.set()
    def close(self):
        self.closed = True
        self.stopped.set()


def result(pcm,**kwargs):
    return {'language':'Vietnamese','segments':[{'text':'có chạy thật không vậy',
            'start_ms':0,'end_ms':min(1000,round(len(pcm)/16))}], 'text':'có chạy thật không vậy'}


@pytest.mark.asyncio
async def test_cloud_consent_and_key_required_before_keys_or_capture(state):
    store,audio,_,publish = state
    def forbidden(*a,**kw):
        raise AssertionError('Unexpected source')
    for key,consent in (('test-key',False),('',True)):
        runner = CloudRecordingRunner(store,audio,forbidden,publish,config=GroqConfig(api_key=key))
        with pytest.raises(ValueError):
            runner.start(None,{'cloud_consent':consent},'No',consent_confirmed=True)
    assert not store.list() and not audio.keys.rows


@pytest.mark.asyncio
async def test_cloud_updates_before_stop_with_shared_language_and_no_local_workers(state,monkeypatch):
    store,audio,events,publish = state
    source = ControlledSource()
    calls = []
    def request(pcm,**kwargs):
        calls.append(kwargs['language'])
        return result(pcm,**kwargs)
    config = GroqConfig(api_key='test-key',request=request)
    runner = CloudRecordingRunner(store,audio,lambda *a,**kw:source,publish,config=config)
    def forbidden(*a,**kw):
        raise AssertionError('Local speech model/diarizer invoked')
    monkeypatch.setattr(runner,'_diarize',forbidden)
    sid = runner.start(None,{'cloud_consent':True,'language':'auto'},'Cloud call',consent_confirmed=True)['id']
    await wait_for(lambda:len(store.segments(sid))>=2)
    assert store.get(sid)['status']=='recording'
    assert calls[0]=='auto' and all(lang=='vi' for lang in calls[1:])
    assert store.language_lock(sid)['language']=='vi'
    assert runner.diarizer is False and runner.finalize_transcript is False
    runner.stop(sid)
    await runner.task
    assert source.closed and store.get(sid)['status']=='completed'
    assert store.get(sid)['capture']['cloud_consent'] is True
    assert store.get(sid)['capture']['provider']=='groq'
    assert store.cloud_usage(sid)['requests']==len(calls)
    assert all(c['confirmed_ms']==12150 for c in store.checkpoints(sid).values())
    assert any(e['type']=='cloud_usage' for e in events)
    before=len(calls)
    with pytest.raises(ValueError):
        runner.reprocess(sid,language='vi',cloud_consent=False)
    with pytest.raises(ValueError):
        runner.resume(sid,language='vi',cloud_consent=False)
    assert len(calls)==before


@pytest.mark.asyncio
async def test_cloud_quota_failure_keeps_capture_and_reserves_budget_across_resume(state):
    store,audio,events,publish = state
    source = ControlledSource()
    calls = []
    def fail(*a,**kw):
        calls.append(True)
        raise GroqError('quota exhausted')
    runner = CloudRecordingRunner(store,audio,lambda *a,**kw:source,publish,
        config=GroqConfig(api_key='test-key',request=fail,max_requests=1))
    sid = runner.start(None,{'cloud_consent':True,'language':'vi'},'Quota',consent_confirmed=True)['id']
    await wait_for(lambda:any(e['type']=='transcription_error' for e in events))
    assert not source.closed and runner.active_id==sid
    runner.stop(sid)
    await runner.task
    assert list(audio.frames(sid,'mic'))
    assert store.cloud_usage(sid)['requests']==1
    runner.resume(sid,language='vi',cloud_consent=True)
    await runner.task
    assert len(calls)==1, 'A resume must not reset the request cap'
    assert store.get(sid)['status']=='interrupted'


def test_budget_is_durable_before_dispatch_and_cap_prevents_more_requests(state):
    store,audio,_,_ = state
    sid=store.create_recording('Budget',{})['id']
    audio.create_session(sid)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,150,0))
    calls=[]
    def request(pcm,**kwargs):
        assert store.cloud_usage(sid)['requests']==len(calls)+1
        calls.append(True)
        return result(pcm,**kwargs)
    config=GroqConfig(api_key='test-key',request=request,max_requests=1)
    cancel,done=threading.Event(),threading.Event()
    done.set()
    def emit(event):
        if event['type']=='cloud_request':
            store.reserve_cloud(sid,event['reservation'])
            event['ack'].set()
    with pytest.raises(GroqError,match='cap'):
        run_cloud(audio,sid,emit,cancel,done,config=config,budget=Budget(config,{}),language='vi')
    assert len(calls)==1


@pytest.mark.asyncio
async def test_failed_usage_commit_cancels_before_any_audio_upload(state,monkeypatch):
    store,audio,_,publish = state
    source=ControlledSource()
    calls=[]
    def request(*a,**kw):
        calls.append(True)
        raise AssertionError('Audio sent without durable reservation')
    def fail(*a,**kw):
        raise OSError('Usage ledger unavailable')
    monkeypatch.setattr(store,'reserve_cloud',fail)
    runner=CloudRecordingRunner(store,audio,lambda *a,**kw:source,publish,
        config=GroqConfig(api_key='test-key',request=request))
    sid=runner.start(None,{'cloud_consent':True,'language':'vi'},'Ledger fault',consent_confirmed=True)['id']
    await wait_for(runner.cancel_processing.is_set)
    runner.stop(sid)
    await asyncio.wait_for(runner.task,3)
    assert not calls and source.closed


@pytest.mark.asyncio
async def test_explicit_cloud_reprocess_revision_and_checkpoint_do_not_duplicate_on_resume(state):
    store,audio,_,publish=state
    sid=store.create_recording('Saved local recording',{})['id']
    audio.create_session(sid)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,0,0))
    store.append(sid,{'text':'Old text','start_ms':0,'end_ms':100,'is_final':True})
    store.transition(sid,'completed')
    calls=[]
    def request(pcm,**kw):
        calls.append(True)
        return result(pcm,**kw)
    runner=CloudRecordingRunner(store,audio,None,publish,config=GroqConfig(api_key='test-key',request=request))
    runner.reprocess(sid,language='vi',cloud_consent=True)
    await runner.task
    assert all(r['text']=='có chạy thật không vậy' for r in store.segments(sid))
    assert len(store.segments(sid))==2
    assert all(c['confirmed_ms']==100 for c in store.checkpoints(sid).values())
    runner.resume(sid,language='auto',cloud_consent=True)
    await runner.task
    assert len(calls)==2 and len(store.segments(sid))==2


def test_cloud_api_requires_new_and_saved_consent_and_keeps_key_out_of_snapshot(tmp_path,require_sqlcipher):
    import json

    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.contracts import AppIdentity
    from scribeloop_core.meetings.service import DiscoveryService
    from test_recording_api import command, connection, receive
    opened=[]
    secret='hidden-test-provider-key'
    config=GroqConfig(api_key=secret,request=result)
    def recording(store,audio,factory,publish):
        def source(*a,**kw):
            instance=ControlledSource()
            opened.append(instance)
            return instance
        return CloudRecordingRunner(store,audio,source,publish,config=config)
    def discovery(publish):
        return DiscoveryService(publish,apps_provider=lambda:(AppIdentity(
            app_id='com.brave.Browser',name='Brave',kind='browser',bundle_ids=('com.brave.Browser',)),))
    app,token=create_app(database=tmp_path/'cloud.sqlcipher',storage='encrypted',key_store=Keys(),
        recording_factory=recording,discovery_factory=discovery)
    with TestClient(app,base_url='http://127.0.0.1') as client,connection(client) as ws:
        ws.send_text(token)
        snapshot=ws.receive_json()
        assert snapshot['transcription']['configured'] is True
        assert snapshot['transcription']['model']=='whisper-large-v3-turbo'
        assert snapshot['replay_enabled'] is False
        assert secret not in json.dumps(snapshot)
        ws.send_json(command(cloud_consent=False))
        receive(ws,'error')
        assert not opened
        ws.send_json(command(cloud_consent=True,language='vi'))
        sid=receive(ws,'session','recording')['session']['id']
        receive(ws,'segment')
        ws.send_json({'action':'stop','session_id':sid})
        receive(ws,'session','completed')
        ws.send_json({'action':'reprocess','session_id':sid,'language':'vi'})
        receive(ws,'error')
        ws.send_json({'action':'resume','session_id':sid,'cloud_consent':False})
        receive(ws,'error')
        ws.send_json({'action':'reprocess','session_id':sid,'language':'vi','cloud_consent':True})
        receive(ws,'session','completed')
        assert len(opened)==1
        assert client.portal.call(app.state.store.cloud_usage,sid)['requests']>0


@pytest.mark.asyncio
async def test_saved_reprocess_checks_both_complete_tracks_before_any_cloud_request(state):
    store,audio,_,publish=state
    sid=store.create_recording('Broken second track',{})['id']
    audio.create_session(sid)
    for stream in ('loopback','mic'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.ones(1600,dtype=np.float32),stream,0,0))
    path=audio.path(sid,'mic')
    path.write_bytes(path.read_bytes()[:-1])
    store.transition(sid,'interrupted')
    calls=[]
    def forbidden(*a,**kw):
        calls.append(True)
        raise AssertionError('A corrupt saved recording was uploaded')
    runner=CloudRecordingRunner(store,audio,None,publish,
        config=GroqConfig(api_key='test-key',request=forbidden))
    runner.reprocess(sid,language='vi',cloud_consent=True)
    await runner.task
    assert not calls
    assert store.get(sid)['status']=='failed'
