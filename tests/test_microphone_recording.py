"""Mic-only scope through native routing, encrypted capture and model selection."""
from __future__ import annotations

import pytest
from scribeloop_core.meetings.contracts import CaptureSelection
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
from scribeloop_core.pipeline.model_selection import ModelRecordingRunner
from test_cloud_meeting import ControlledSource, wait_for
from test_cloud_meeting import state as cloud_state
from test_deepgram_meeting import Socket
from test_model_selection import LocalConfig, result


@pytest.fixture()
def state(tmp_path):
    yield from cloud_state.__wrapped__(tmp_path)


class MicSource(ControlledSource):
    def frames(self):
        for frame in super().frames():
            if frame.stream=='mic':
                yield frame


@pytest.mark.asyncio
@pytest.mark.parametrize('model',['deepgram','moss-q6-k'])
async def test_microphone_capture_readiness_live_processing_and_saved_resume(state,model):
    store,audio,events,publish=state
    source=MicSource()
    sockets=[]
    local_calls=[]
    def connector(*args,**kwargs):
        sock=Socket()
        sockets.append(sock)
        return sock
    def local(pcm,**kwargs):
        assert source.closed
        local_calls.append(True)
        return result(pcm,**kwargs)
    runner=ModelRecordingRunner(store,audio,lambda *a,**k:source,publish,
        cloud_config=DeepgramConfig(api_key='test-key',connector=connector),
        local_config=LocalConfig(request=local))
    runner.select_model(model)
    selection=CaptureSelection(scope='microphone',microphone_device_id='default')
    sid=runner.start(selection,{'scope':'microphone','language':'en','cloud_consent':model=='deepgram'},
        'Microphone',consent_confirmed=True)['id']
    await wait_for(lambda:store.get(sid)['status']=='recording')
    if model=='deepgram':
        await wait_for(lambda:len(store.segments(sid))>=2)
        assert runner.active_id==sid and not source.closed
        assert len(sockets)==1, 'No output connection or fake silence upload'
    else:
        assert not local_calls and not sockets
    runner.stop(sid)
    await runner.task
    assert store.get(sid)['status']=='completed'
    assert list(audio.frames(sid,'mic')) and list(audio.frames(sid,'loopback'))==[]
    assert all(r['stream']=='mic' and r['speaker_label'] for r in store.segments(sid))
    if model=='moss-q6-k':
        assert len(local_calls)==1 and store.cloud_usage(sid)['requests']==0
    before=store.segments(sid)
    runner.resume(sid,language='en',cloud_consent=model=='deepgram')
    await runner.task
    assert len(store.segments(sid))==len(before)
    assert len(sockets)==(1 if model=='deepgram' else 0)
    assert any(e['type']=='session' and e['session']['status']=='recording' for e in events)


def test_native_mic_scope_routes_only_to_microphone_helper(monkeypatch,tmp_path):
    from scribeloop_core.capture.macos_client import MacOSMeetingAudioSource

    monkeypatch.setattr('sys.platform','darwin')
    helper=tmp_path/'helper'
    helper.touch()
    source=MacOSMeetingAudioSource(CaptureSelection(scope='microphone',microphone_device_id='usb-mic'),
        consent_confirmed=True,helper_path=helper)
    native=source._source._source
    assert '--microphone-only' in native.command
    assert '--scope' not in native.command and '--bundle-id' not in native.command
    assert native.expected_streams==('mic',)
    source.close()


@pytest.mark.asyncio
async def test_microphone_selection_does_not_depend_on_installed_apps():
    from scribeloop_core.meetings.service import DiscoveryService

    async def publish(event):
        pass
    def unavailable():
        raise RuntimeError('App enumeration unavailable')
    service=DiscoveryService(publish,apps_provider=unavailable)
    source=await service.select(scope='microphone',app_id=None,microphone_id='default')
    assert source['name']=='Microphone only' and source['app_id'] is None
    for field in ({'app_id':'an-app'},{'candidate_id':'suggestion'}):
        with pytest.raises(ValueError):
            await service.select(**{'scope':'microphone','app_id':None,'microphone_id':'default',**field})
    with pytest.raises(ValueError):
        CaptureSelection(scope='microphone',bundle_ids=('an-app',),microphone_device_id='default')


@pytest.mark.asyncio
async def test_microphone_scope_rejects_unexpected_output_frames(state):
    store,audio,_,publish=state
    runner=ModelRecordingRunner(store,audio,lambda *a,**k:ControlledSource(),publish,
        local_config=LocalConfig(request=result))
    runner.select_model('moss-q6-k')
    sid=runner.start(CaptureSelection(scope='microphone',microphone_device_id='default'),
        {'scope':'microphone','language':'en'},'Wrong scope',consent_confirmed=True)['id']
    await runner.task
    assert store.get(sid)['status']=='failed' and not store.segments(sid)
    frames,report=audio.prefix(sid,'loopback')
    assert not frames and report['end_ms']==0


def test_api_mic_only_streams_before_stop_with_one_connection(tmp_path):
    from fastapi.testclient import TestClient
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.service import DiscoveryService
    from test_recording import Keys
    from test_recording_api import command, connection, receive

    sockets=[]
    def connector(*args,**kwargs):
        sock=Socket()
        sockets.append(sock)
        return sock
    def recording(store,audio,source,publish):
        return ModelRecordingRunner(store,audio,lambda *a,**k:MicSource(),publish,
            cloud_config=DeepgramConfig(api_key='test-key',connector=connector))
    app,token=create_app(database=tmp_path/'mic.sqlcipher',storage='encrypted',key_store=Keys(),
        recording_factory=recording,discovery_factory=lambda p:DiscoveryService(p))
    with TestClient(app,base_url='http://127.0.0.1') as client, connection(client) as ws:
        ws.send_text(token)
        ws.receive_json()
        ws.send_json(command(scope='microphone',app_id=None,model='deepgram',language='en',cloud_consent=True))
        sid=receive(ws,'session','preparing')['session']['id']
        row=receive(ws,'segment')['segment']
        assert row['stream']=='mic' and row['speaker_label']
        assert client.portal.call(app.state.store.get,sid)['status']=='recording'
        assert len(sockets)==1
        ws.send_json({'action':'stop','session_id':sid})
        for _ in range(200):
            event=ws.receive_json()
            if event['type']=='session' and event['session']['status']=='completed':
                break
        else:
            pytest.fail('Mic recording did not complete')


@pytest.mark.asyncio
async def test_mic_only_recovery_accepts_intentionally_empty_output(state):
    import numpy as np
    from scribeloop_core.capture.source import AudioFrame
    from scribeloop_core.storage.audio import AudioIntegrityError

    store,audio,_,publish=state
    sid=store.create_recording('Interrupted mic',{'scope':'microphone','provider':'local-moss'})['id']
    audio.create_session(sid)
    for stream in ('mic','loopback'):
        writer=audio.writer(sid,stream)
        if stream=='mic':
            writer.write(AudioFrame(np.zeros(1600,dtype='float32'),'mic',0,0))
        writer.abort()
    store.transition(sid,'failed')
    with pytest.raises(AudioIntegrityError):
        audio.recover_session(sid)  # Dual-track recovery must still require both tracks.
    runner=ModelRecordingRunner(store,audio,None,publish)
    runner.recover_saved(sid)
    await runner.task
    assert store.get(sid)['status']=='interrupted'
    assert len(list(audio.frames(sid,'mic')))==1
    assert list(audio.frames(sid,'loopback'))==[]


@pytest.mark.asyncio
async def test_sanitized_native_failure_is_saved_without_secret_exception_text(state):
    import numpy as np
    from scribeloop_core.capture.source import AudioFrame
    from scribeloop_core.pipeline.recording import RecordingRunner

    store,audio,events,publish=state
    class BrokenMic(MicSource):
        def frames(self):
            yield AudioFrame(np.zeros(170,dtype='float32'),'mic',84,0)
            raise RuntimeError('Native capture ended: Audio clock discontinuity or drift exceeded 2 ms; secret-api-key')
    runner=RecordingRunner(store,audio,lambda *a,**k:BrokenMic(),publish,transcriber=lambda *a:None)
    sid=runner.start(CaptureSelection(scope='microphone',microphone_device_id='default'),
        {'scope':'microphone'},'Failure',consent_confirmed=True)['id']
    await runner.task
    failure=store.get(sid)['failure']
    assert failure['code']=='capture_clock_discontinuity' and failure['stage']=='capture'
    assert 'secret-api-key' not in str(failure)
    assert any(e['type']=='recording_error' and e['diagnostic']==failure for e in events)


def test_unknown_failure_diagnostic_never_exposes_arbitrary_text():
    from scribeloop_core.pipeline.failures import failure_diagnostic

    diagnostic=failure_diagnostic(RuntimeError('secret recording transcript token=abc'),'capture')
    assert diagnostic['code']=='capture_failed'
    assert 'secret' not in str(diagnostic) and 'token=abc' not in str(diagnostic)
