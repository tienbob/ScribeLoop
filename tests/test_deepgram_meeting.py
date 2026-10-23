"""Streaming speaker integration and faults, with no outbound API calls."""
from __future__ import annotations

import json
import time

import numpy as np
import pytest
from scribeloop_core.pipeline.deepgram_meeting import (
    DeepgramConfig,
    DeepgramError,
    DeepgramRecordingRunner,
    speaker_rows,
)
from test_cloud_meeting import ControlledSource, wait_for
from test_cloud_meeting import state as cloud_state
from websockets.exceptions import ConnectionClosedOK


@pytest.fixture()
def state(tmp_path):
    yield from cloud_state.__wrapped__(tmp_path)


class Socket:
    def __init__(self, language='en', fail=False):
        self.sent_ms = 0
        self.reported = 0
        self.closed = False
        self.metadata = False
        self.language = language
        self.fail = fail
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def send(self,data):
        if isinstance(data,bytes):
            self.sent_ms += len(data)//32
        else:
            assert json.loads(data)['type']=='CloseStream'
            self.closed = True
    def recv(self,timeout):
        if self.fail:
            raise DeepgramError('Quota failure')
        if self.sent_ms > self.reported:
            start,end = self.reported/1000,self.sent_ms/1000
            self.reported = self.sent_ms
            return json.dumps({'type':'Results','is_final':True,'start':start,'duration':end-start,
                'channel':{'alternatives':[{'transcript':'hello there','words':[
                    {'start':start,'end':start+(end-start)/2,'word':'hello','speaker':0},
                    {'start':start+(end-start)/2,'end':end,'word':'there','speaker':1}]}]}})
        if self.closed:
            if not self.metadata:
                self.metadata = True
                return json.dumps({'type':'Metadata','duration':self.sent_ms/1000})
            raise ConnectionClosedOK(None,None)
        time.sleep(.01)
        raise TimeoutError


def event(words):
    return {'channel':{'alternatives':[{'transcript':'one two three','words':words}]}}


def test_word_speakers_split_and_missing_identity_stays_unknown():
    rows = speaker_rows(event([
        {'word':'one','start':0,'end':.1,'speaker':0},
        {'word':'two','start':.1,'end':.2,'speaker':1},
        {'word':'three','start':.2,'end':.3}]),stream='mic',epoch='a'*32,
        base_ms=1000,sent_ms=1300,language='en')
    assert len(rows)==3 and rows[-1]['speaker_key'] is None
    assert rows[0]['speaker_key']!=rows[1]['speaker_key']
    assert rows[0]['start_ms']==1000 and rows[-1]['end_ms']==1300
    with pytest.raises(DeepgramError):
        speaker_rows(event([{'word':'bad','start':0,'end':20,'speaker':1}]),
            stream='mic',epoch='a'*32,base_ms=0,sent_ms=300,language='en')


@pytest.mark.asyncio
async def test_live_speakers_are_saved_before_stop_restore_notes_and_no_second_pass(state):
    store,audio,events,publish = state
    sockets=[]
    source=ControlledSource()
    def connector(url,**kwargs):
        assert 'diarize_model=v1' in url and 'language=en' in url
        assert kwargs['additional_headers']['Authorization']=='Token hidden-test-key'
        sock=Socket()
        sockets.append(sock)
        return sock
    runner=DeepgramRecordingRunner(store,audio,lambda *a,**k:source,publish,
        config=DeepgramConfig(api_key='hidden-test-key',connector=connector))
    sid=runner.start(None,{'cloud_consent':True,'language':'en'},'People',consent_confirmed=True)['id']
    await wait_for(lambda:len(store.segments(sid))>=4)
    assert store.get(sid)['status']=='recording'
    labels={r['speaker_label'] for r in store.segments(sid)}
    assert len(labels)==4 and None not in labels, 'Each source has its own speaker namespace'
    runner.stop(sid)
    await runner.task
    assert store.get(sid)['status']=='completed' and source.closed
    assert len(sockets)==2 and runner.diarizer is False and runner.finalize_transcript is False
    assert 'Speaker ' in store.notes(sid)
    assert all(c['confirmed_ms']==12150 for c in store.checkpoints(sid).values())
    assert store.get(sid)['capture']['provider']=='deepgram'
    persisted=store.segments(sid)
    assert all(r['local_speaker_id'] and r['speaker_label'] for r in persisted)
    assert any(e['type']=='segment' and e['segment'].get('speaker_label') for e in events)
    runner.resume(sid,language='en',cloud_consent=True)
    await runner.task
    assert len(sockets)==2 and store.segments(sid)==persisted


@pytest.mark.asyncio
async def test_fault_keeps_recorder_running_and_fresh_resume_does_not_merge_people(state):
    store,audio,events,publish = state
    source=ControlledSource()
    calls=[]
    def connector(*a,**kw):
        calls.append(1)
        return Socket(fail=True)
    runner=DeepgramRecordingRunner(store,audio,lambda *a,**k:source,publish,
        config=DeepgramConfig(api_key='hidden-test-key',connector=connector))
    sid=runner.start(None,{'cloud_consent':True,'language':'en'},'Fail',consent_confirmed=True)['id']
    await wait_for(lambda:any(e['type']=='transcription_error' for e in events))
    assert runner.active_id==sid and not source.closed
    runner.stop(sid)
    await runner.task
    assert list(audio.frames(sid,'mic'))
    assert len(calls)<=2
    runner.cloud.connector=lambda *a,**kw:Socket()
    runner.resume(sid,language='en',cloud_consent=True)
    await runner.task
    labels={r['speaker_label'] for r in store.segments(sid)}
    assert len(labels)==4
    runner.reprocess(sid,language='en',cloud_consent=True)
    await runner.task
    new_labels={r['speaker_label'] for r in store.segments(sid)}
    assert labels.isdisjoint(new_labels), 'New connection speaker IDs are not person identity evidence'


@pytest.mark.asyncio
async def test_explicit_provider_consent_language_and_budget_before_upload(state):
    store,audio,_,publish=state
    calls=[]
    runner=DeepgramRecordingRunner(store,audio,None,publish,
        config=DeepgramConfig(api_key='hidden-test-key',connector=lambda *a,**k:calls.append(1)))
    for metadata in ({'cloud_consent':False,'language':'en'},{'cloud_consent':True,'language':'auto'}):
        with pytest.raises(ValueError):
            runner.start(None,metadata,'No',consent_confirmed=True)
    assert not store.list() and not calls
    source=ControlledSource()
    runner.source_factory=lambda *a,**k:source
    runner.cloud.budget_usd=.000001
    sid=runner.start(None,{'cloud_consent':True,'language':'en'},'Cap',consent_confirmed=True)['id']
    await wait_for(lambda:runner.cancel_processing.is_set() or any(e for e in store.list() if e['status']=='recording'))
    runner.stop(sid)
    await runner.task
    assert not calls and store.cloud_usage(sid)['requests']==0


def test_speaker_assignment_is_atomic_and_reused_within_one_connection(state):
    store,_,_,_=state
    sid=store.create_recording('Atomic',{})['id']
    store.checkpoint(sid,'mic',recorded_ms=1000)
    def chunk(a,b,key):
        return {'start_ms':a,'end_ms':b,'text':'word','is_final':True,'speaker_key':key}
    key='deepgram:'+('a'*32)+':mic:0'
    store.commit_window(sid,'mic',[chunk(0,100,key),chunk(100,200,key)],200)
    assert len({r['speaker_label'] for r in store.segments(sid)})==1
    with pytest.raises(ValueError):
        store.commit_window(sid,'mic',[chunk(200,300,'deepgram:'+('b'*32)+':mic:0'),chunk(100,400,key)],400)
    assert store.conn.execute('SELECT COUNT(*) FROM session_speakers').fetchone()[0]==1
    assert store.checkpoints(sid)['mic']['confirmed_ms']==200


@pytest.mark.asyncio
async def test_resume_authenticates_both_saved_tracks_before_either_upload(state):
    from scribeloop_core.capture.source import AudioFrame
    store,audio,_,publish=state
    sid=store.create_recording('Tampered',{'provider':'deepgram'})['id']
    audio.create_session(sid)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.ones(1600,dtype='float32'),stream,0,0))
    path=audio.path(sid,'mic')
    path.write_bytes(path.read_bytes()[:-1])
    store.transition(sid,'interrupted')
    calls=[]
    runner=DeepgramRecordingRunner(store,audio,None,publish,
        config=DeepgramConfig(api_key='test-key',connector=lambda *a,**k:calls.append(True)))
    runner.resume(sid,language='en',cloud_consent=True)
    await runner.task
    assert not calls and store.cloud_usage(sid)['requests']==0


def test_reservation_must_be_durable_before_connection_and_cap_spans_requests(state):
    import threading

    from scribeloop_core.pipeline.deepgram_meeting import StreamingBudget
    store,_,_,_=state
    sid=store.create_recording('Budget',{})['id']
    config=DeepgramConfig(api_key='test-key',max_requests=1)
    cancelled=threading.Event()
    def emit(event):
        store.reserve_cloud(sid,event['reservation'])
        event['ack'].set()
    budget=StreamingBudget(config,{})
    budget.reserve('mic',emit,cancelled)
    assert store.cloud_usage(sid)['requests']==1
    resumed=StreamingBudget(config,store.cloud_usage(sid))
    with pytest.raises(DeepgramError):
        resumed.reserve('mic',emit,cancelled)


def test_api_restores_speaker_labels_and_hides_credentials(tmp_path):
    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.contracts import AppIdentity
    from scribeloop_core.meetings.service import DiscoveryService
    from test_recording import Keys
    from test_recording_api import command, connection, receive
    secret='never-expose-deepgram-test-key'
    sockets=[]
    def connector(*a,**kw):
        sock=Socket()
        sockets.append(sock)
        return sock
    def recording(store,audio,source,publish):
        return DeepgramRecordingRunner(store,audio,lambda *a,**k:ControlledSource(),publish,
            config=DeepgramConfig(api_key=secret,connector=connector))
    def discovery(publish):
        return DiscoveryService(publish,apps_provider=lambda:(AppIdentity(
            app_id='com.brave.Browser',name='Brave',kind='browser',bundle_ids=('com.brave.Browser',)),))
    app,token=create_app(database=tmp_path/'speaker.sqlcipher',storage='encrypted',key_store=Keys(),
        recording_factory=recording,discovery_factory=discovery)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        with connection(client) as ws:
            ws.send_text(token)
            snapshot=ws.receive_json()
            assert snapshot['transcription']['provider']=='deepgram'
            assert snapshot['transcription']['speaker_labels'] is True
            assert secret not in json.dumps(snapshot)
            ws.send_json(command(cloud_consent=False,language='en'))
            receive(ws,'error')
            assert not sockets
            ws.send_json(command(cloud_consent=True,language='en'))
            sid=receive(ws,'session','recording')['session']['id']
            assert receive(ws,'segment')['segment']['speaker_label'].startswith('Speaker ')
            ws.send_json({'action':'stop','session_id':sid})
            for _ in range(1000):
                event=ws.receive_json()
                if event['type']=='session' and event['session']['status']=='completed':
                    break
            else:
                raise AssertionError('No completion event')
        with connection(client) as ws:
            ws.send_text(token)
            snapshot=ws.receive_json()
            assert all(r['speaker_label'] for r in snapshot['segments'][sid])
            assert 'Speaker ' in snapshot['notes'][sid]
            assert secret not in json.dumps(snapshot)


def test_wrong_provider_configuration_is_rejected_before_key_dispatch(state):
    from scribeloop_core.pipeline.cloud_meeting import GroqConfig
    store,audio,_,publish=state
    with pytest.raises(TypeError,match='Deepgram configuration'):
        DeepgramRecordingRunner(store,audio,None,publish,config=GroqConfig(api_key='other-provider-key'))
