"""Local/cloud selection, isolation and saved transcript replacement; offline."""
from __future__ import annotations

import pytest
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
from scribeloop_core.pipeline.model_selection import ModelRecordingRunner
from scribeloop_core.pipeline.moss_meeting import MossConfig
from test_cloud_meeting import ControlledSource, wait_for
from test_cloud_meeting import state as cloud_state
from test_deepgram_meeting import Socket


@pytest.fixture()
def state(tmp_path):
    yield from cloud_state.__wrapped__(tmp_path)


class LocalConfig(MossConfig):
    def public(self):
        return {**super().public(),'configured':True}


def result(pcm,**kwargs):
    return {'language':'en','text':'hello there','segments':[
        {'start_ms':150,'end_ms':500,'speaker':1,'text':'hello'},
        {'start_ms':500,'end_ms':1000,'speaker':2,'text':'there'}]}


@pytest.mark.asyncio
async def test_local_after_stop_has_speakers_and_never_connects_to_cloud(state):
    store,audio,_events,publish = state
    source = ControlledSource()
    calls = []
    def local(pcm,**kwargs):
        assert source.closed, 'No inference until capture has closed'
        calls.append(len(pcm))
        return result(pcm,**kwargs)
    def forbidden(*args,**kwargs):
        pytest.fail('Local mode must not use cloud')
    runner = ModelRecordingRunner(store,audio,lambda *a,**k:source,publish,
        local_config=LocalConfig(request=local),cloud_config=DeepgramConfig(api_key='',connector=forbidden))
    runner.select_model('moss-q6-k')
    sid = runner.start(None,{'language':'en','cloud_consent':False},'Local',consent_confirmed=True)['id']
    await wait_for(lambda:store.get(sid)['status']=='recording')
    assert not calls
    with pytest.raises(ValueError,match='finish'):
        runner.select_model('deepgram')
    runner.stop(sid)
    await runner.task
    assert len(calls)==2 and store.get(sid)['status']=='completed'
    assert store.get(sid)['capture']['engine']=='moss-q6-k'
    assert store.get(sid)['transcript_provider']=='local-moss'
    assert store.cloud_usage(sid)['requests']==0
    assert len({r['speaker_label'] for r in store.segments(sid)})==4
    assert all(r['local_speaker_id'] for r in store.segments(sid))
    assert 'Speaker ' in store.notes(sid)
    await runner.close()


@pytest.mark.asyncio
async def test_replacement_switches_provider_atomically_and_resume_never_mixes(state):
    store,audio,_,publish = state
    source = ControlledSource()
    sockets = []
    def connector(*a,**k):
        sock=Socket()
        sockets.append(sock)
        return sock
    config=LocalConfig(request=result)
    runner=ModelRecordingRunner(store,audio,lambda *a,**k:source,publish,
        local_config=config,cloud_config=DeepgramConfig(api_key='test-key',connector=connector))
    sid=runner.start(None,{'language':'en','cloud_consent':True},'Cloud',consent_confirmed=True)['id']
    await wait_for(lambda:len(store.segments(sid))>=2)
    runner.stop(sid)
    await runner.task
    prior=store.segments(sid)
    runner.select_model('moss-q6-k')
    with pytest.raises(ValueError,match='Transcribe again'):
        runner.resume(sid,language='en')
    def broken(*a,**k):
        raise RuntimeError('Offline failure')
    config.request=broken
    runner.reprocess(sid,language='en')
    await runner.task
    assert store.segments(sid)==prior
    assert store.transcript_provider(sid)=='deepgram'
    config.request=result
    runner.reprocess(sid,language='en')
    await runner.task
    assert store.transcript_provider(sid)=='local-moss'
    assert store.get(sid)['transcript_provider']=='local-moss'
    assert len(store.segments(sid))==4
    assert len(sockets)==2
    runner.resume(sid,language='en')
    await runner.task
    assert len(store.segments(sid))==4, 'Local Resume replaces instead of duplicating rows'
    runner.select_model('deepgram')
    with pytest.raises(ValueError,match='Transcribe again'):
        runner.resume(sid,language='en',cloud_consent=True)


@pytest.mark.asyncio
async def test_local_language_and_installation_checks_precede_capture(state,tmp_path):
    store,audio,_,publish=state
    runner=ModelRecordingRunner(store,audio,None,publish,
        local_config=MossConfig(runtime=tmp_path,model=tmp_path/'absent.gguf'))
    runner.select_model('moss-q6-k')
    for language in ('vi','en'):
        with pytest.raises(ValueError):
            runner.start(None,{'language':language},'No',consent_confirmed=True)
    assert not store.list()
    with pytest.raises(ValueError):
        runner.select_model('unknown')
    await runner.close()
    with pytest.raises(ValueError):
        runner.select_model('deepgram')


@pytest.mark.asyncio
async def test_local_tampered_track_prevents_inference_on_both_tracks(state):
    import numpy as np
    from scribeloop_core.capture.source import AudioFrame

    store,audio,_,publish=state
    sid=store.create_recording('Tampered',{'provider':'local-moss'})['id']
    audio.create_session(sid)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.ones(1600,dtype='float32'),stream,0,0))
    path=audio.path(sid,'mic')
    path.write_bytes(path.read_bytes()[:-1])
    store.transition(sid,'interrupted')
    calls=[]
    runner=ModelRecordingRunner(store,audio,None,publish,
        local_config=LocalConfig(request=lambda *a,**k:calls.append(True)))
    runner.select_model('moss-q6-k')
    runner.resume(sid,language='en')
    await runner.task
    assert not calls and not store.segments(sid)


@pytest.mark.asyncio
async def test_zero_filled_track_skips_inference_and_clears_backlog(state):
    import numpy as np
    from scribeloop_core.capture.source import AudioFrame

    store,audio,events,publish=state
    sid=store.create_recording('Silent mic',{'provider':'local-moss'})['id']
    audio.create_session(sid)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream) as writer:
            pcm=np.zeros(16000,dtype='float32') if stream=='mic' else np.ones(16000,dtype='float32')
            writer.write(AudioFrame(pcm,stream,0,0))
    store.transition(sid,'interrupted')
    calls=[]
    def request(pcm,**kwargs):
        calls.append(len(pcm))
        return result(pcm,**kwargs)
    runner=ModelRecordingRunner(store,audio,None,publish,local_config=LocalConfig(request=request))
    runner.select_model('moss-q6-k')
    runner.resume(sid,language='en')
    await runner.task
    assert store.get(sid)['status']=='completed' and len(calls)==1
    assert all(r['stream']=='loopback' for r in store.segments(sid))
    progress=[e for e in events if e['type']=='transcription_progress' and e['stream']=='mic']
    assert progress[-1]['confirmed_ms']==1000 and progress[-1]['lag_ms']==0


@pytest.mark.parametrize('cancel', [False, True])
def test_local_process_transfers_large_pcm_and_cancels_without_plaintext_files(monkeypatch,cancel):
    import subprocess
    import sys
    import threading
    import time

    import numpy as np

    popen = subprocess.Popen
    processes = []
    def worker(command,**kwargs):
        assert command[2]=='scribeloop_core.pipeline.moss_gguf_worker'
        assert 'DEEPGRAM_API_KEY' not in kwargs['env']
        code = ('import sys,time,json; time.sleep(.3); '
                'data=sys.stdin.buffer.read(); '
                + ('time.sleep(10); ' if cancel else '') +
                'print(json.dumps({"bytes":len(data)}))')
        proc=popen([sys.executable,'-c',code],**kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(subprocess,'Popen',worker)
    monkeypatch.setenv('DEEPGRAM_API_KEY','must-not-reach-local-worker')
    cancelled=threading.Event()
    timer=threading.Timer(.6,cancelled.set) if cancel else None
    if timer:
        timer.start()
    started=time.monotonic()
    pcm=np.ones(320000,dtype='float32')
    try:
        if cancel:
            with pytest.raises(RuntimeError,match='cancelled'):
                MossConfig().infer(pcm,language='en',cancelled=cancelled)
        else:
            assert MossConfig().infer(pcm,language='en',cancelled=cancelled)=={'bytes':pcm.nbytes}
        assert time.monotonic()-started < 3
        assert processes[0].poll() is not None
    finally:
        if timer:
            timer.cancel()


def test_api_model_selection_restores_profile_and_local_speaker_rows(tmp_path,require_sqlcipher):
    import json

    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.contracts import AppIdentity
    from scribeloop_core.meetings.service import DiscoveryService
    from test_recording import Keys
    from test_recording_api import command, connection, receive

    def recording(store,audio,source,publish):
        return ModelRecordingRunner(store,audio,lambda *a,**k:ControlledSource(),publish,
            local_config=LocalConfig(request=result),cloud_config=DeepgramConfig(api_key=''))
    def discovery(publish):
        return DiscoveryService(publish,apps_provider=lambda:(AppIdentity(
            app_id='com.brave.Browser',name='Brave',kind='browser',bundle_ids=('com.brave.Browser',)),))
    app,token=create_app(database=tmp_path/'models.sqlcipher',storage='encrypted',key_store=Keys(),
        recording_factory=recording,discovery_factory=discovery)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        with connection(client) as ws:
            ws.send_text(token)
            snapshot=ws.receive_json()
            assert snapshot['transcription_models']['moss-q6-k']['cloud'] is False
            assert snapshot['transcription_models']['deepgram']['cloud'] is True
            assert 'auto' in snapshot['transcription_models']['deepgram']['languages']
            assert 'zh' not in snapshot['transcription_models']['deepgram']['languages']
            assert 'api_key' not in json.dumps(snapshot)
            ws.send_json(command(model='moss-q6-k',language='vi'))
            receive(ws,'error')
            assert not client.portal.call(app.state.store.list)
            ws.send_json(command(model='moss-q6-k',language='en',cloud_consent=False))
            session=receive(ws,'session','preparing')['session']
            assert session['capture']['provider']=='local-moss'
            receive(ws,'session','recording')
            ws.send_json({'action':'stop','session_id':session['id']})
            receive(ws,'session','completed')
        with connection(client) as ws:
            ws.send_text(token)
            snapshot=ws.receive_json()
            assert snapshot['sessions'][0]['transcript_provider']=='local-moss'
            rows=snapshot['segments'][session['id']]
            assert len(rows)==4 and all(r['speaker_label'] for r in rows)
