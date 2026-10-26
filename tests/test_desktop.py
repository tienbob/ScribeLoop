"""Desktop protocol and secure settings, using synthetic data and fake secrets only."""
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop import bootstrap
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings, Settings
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.service import DiscoveryService
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.keys import KeyUnavailable
from scribeloop_core.storage.sessions import SessionStore
from starlette.websockets import WebSocketDisconnect


class Vault:
    def __init__(self):
        self.value = None
    def get_password(self, service, account):
        return self.value
    def set_password(self, service, account, value):
        self.value = value


def envelope(command, request=None):
    return {'protocol':2,'request_id':request or uuid4().hex,'command':command}


def receive(ws, kind):
    for _ in range(100):
        value=ws.receive_json()
        assert value['protocol']==2 and len(value['boot_id'])==32
        if value['type']==kind:
            return value
    raise AssertionError('Expected desktop event missing')


def test_settings_do_not_read_env_and_never_echo_secrets(monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY','ignored-environment-key')
    monkeypatch.setenv('OPENAI_API_KEY','ignored-environment-key')
    settings=DesktopSettings(Vault())
    assert not settings.cloud().api_key and not settings.value.summary().configured
    public=settings.save({'summary_key':'s'*32,'language':'vi'})
    assert public['summary_configured'] and 'deepgram_key' not in public
    assert 'd'*32 not in str(public) and 's'*32 not in str(public)
    settings.save({'summary_model':'example-model'})
    assert settings.value.summary_key=='s'*32
    assert DesktopSettings(settings.backend).value.language=='vi'
    for bad in ({'duration_s':True},{'duration_s':42},{'language':'xx'},
                {'summary_endpoint':'http://untrusted.example/v1'}, {'deepgram_key':'bad key'}):
        with pytest.raises(ValueError):
            settings.save(bad)
    assert settings.value.summary_model=='example-model'
    assert 's'*32 not in repr(settings.value)


def test_locked_settings_fail_closed():
    vault=Vault()
    vault.value='invalid'
    with pytest.raises(KeyUnavailable):
        DesktopSettings(vault)


def test_private_production_bootstrap():
    import json
    good={'protocol':2,'token':'a'*64,'launch_id':'b'*32,'database':'/tmp/desktop.sqlcipher'}
    assert bootstrap(json.dumps(good).encode())==good
    for change in ({'protocol':True},{'protocol':1},{'fixture':True},{'database':'relative.sqlcipher'},
                   {'token':'short'},{'database':'/tmp/plain.sqlite3'}):
        with pytest.raises(ValueError):
            bootstrap(json.dumps(good | change).encode())


def test_pages_are_bounded_stable_and_revision_pinned():
    conn=migrate(connect(':memory:'))
    store=SessionStore(conn)
    settings=DesktopSettings(Vault())
    desktop=DesktopContract(settings,Path('/fake/helper'))
    ids=[store.create_recording(f'Synthetic {i}',{})['id'] for i in range(75)]
    for i in range(230):
        store.append(ids[0],{'text':f'Segment {i}','start_ms':i*100,'end_ms':i*100+99,'is_final':True},stream='mic')
    history=desktop.history(store)
    assert len(history['sessions'])==30 and history['next_cursor']
    page2=desktop.history(store,history['next_cursor'])
    assert not {s['id'] for s in history['sessions']} & {s['id'] for s in page2['sessions']}
    from types import SimpleNamespace
    app=SimpleNamespace(state=SimpleNamespace(store=store,audio=None))
    result=desktop.meeting(app,{'action':'meeting_page','session_id':ids[0],'offset':-1},settings.value.summary())
    assert result['total']==230 and len(result['segments'])==30 and result['offset']==200
    first=store.segments(ids[0],limit=1)[0]
    anchor=desktop.meeting(app,{'action':'meeting_page','session_id':ids[0],'anchor':first['id']},settings.value.summary())
    assert anchor['offset']==0 and len(anchor['segments'])==100
    stale=desktop.meeting(app,{'action':'meeting_page','session_id':ids[0],'revision':999},settings.value.summary())
    assert stale['type']=='invalidate'
    assert len(store.segments(ids[0]))==230, 'Legacy export retains all rows'
    conn.close()


def test_desktop_auth_receipts_reconnect_settings_and_no_replay():
    settings=DesktopSettings(Vault())
    desktop=DesktopContract(settings,Path('/fake/helper'))
    app,token=create_app(desktop=desktop,summary_provider=settings.value.summary())
    with TestClient(app) as client:
        assert client.get('http://127.0.0.1/').status_code==404
        with pytest.raises(WebSocketDisconnect), client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text('wrong'); ws.receive_json()
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            snapshot=receive(ws,'snapshot')
            assert 'segments' not in snapshot and 'settings' in snapshot
            rejected=envelope({'action':'summarize'})
            ws.send_json(rejected)
            assert receive(ws,'error')
            assert receive(ws,'receipt')['status']=='rejected'
            command=envelope({'action':'accept_agreement','version':snapshot['agreement']['version']})
            ws.send_json(command)
            accepted=receive(ws,'agreement')['agreement']['accepted_at']
            assert receive(ws,'receipt')['status']=='accepted'
            ws.send_json(command)
            assert receive(ws,'receipt')['status']=='accepted'
            assert client.portal.call(app.state.store.agreement)['accepted_at']==accepted
            ws.send_json(envelope({'action':'save_settings','settings':{'summary_key':'s'*32}}))
            event=receive(ws,'settings')
            assert 's'*32 not in str(event)
            assert receive(ws,'receipt')['status']=='accepted'
            ws.send_json(envelope({'action':'set_detection','enabled':False}))
            assert receive(ws,'discovery')['discovery']['status']=='paused'
            assert receive(ws,'receipt')['status']=='accepted'
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            assert receive(ws,'snapshot')['agreement']['accepted']
            ws.send_json(envelope({'action':'request_status','request_id':command['request_id']}))
            assert receive(ws,'receipt')['status']=='accepted'


@pytest.mark.asyncio
async def test_pause_stops_observation_and_invalidates_candidates():
    events=[]
    async def publish(value):
        events.append(value)
    service=DiscoveryService(publish,apps_provider=lambda:())
    await service.start()
    assert service._task is not None
    await service.set_enabled(False)
    assert service._task is None and not service.snapshot()['candidates']
    await service.set_enabled(True)
    assert service._task is not None
    await service.close()


def test_receipts_are_bounded_and_protocol_rejects_legacy():
    contract=DesktopContract(DesktopSettings(Vault()),Path('/fake/helper'))
    with pytest.raises(ValueError):
        contract.unwrap({'action':'record'})
    for _ in range(300):
        contract.receipt(uuid4().hex,'accepted')
    assert len(contract.receipts)==256
    assert Settings().summary().model=='gpt-5.4-mini'


def test_desktop_streams_before_stop_no_replay_and_finishes_notes(tmp_path,require_sqlcipher):
    from scribeloop_core.api.desktop_fixture import FixtureKeys
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
    from scribeloop_core.pipeline.model_selection import CloudRecordingRouter
    from test_cloud_meeting import ControlledSource
    from test_deepgram_meeting import Socket

    settings=DesktopSettings(Vault(),transcription_key='synthetic-key-123456789')
    settings.save({'language':'en','duration_s':300})
    contract=DesktopContract(settings,Path('/fake/helper'))
    class MicrophoneSource(ControlledSource):
        def frames(self):
            yield from (frame for frame in super().frames() if frame.stream == 'mic')
    sources=[]
    sockets=[]
    def connector(*_args,**_kwargs):
        sock=Socket()
        sockets.append(sock)
        return sock
    def recording(store,audio,_factory,publish):
        def source(*_args,**_kwargs):
            item=MicrophoneSource()
            sources.append(item)
            return item
        return CloudRecordingRouter(store,audio,source,publish,
            cloud_config=DeepgramConfig(api_key='synthetic-key-123456789',connector=connector,
                                       budget_usd=.25,max_requests=160))
    app,token=create_app(database=tmp_path/'desktop.sqlcipher',storage='encrypted',
        key_store=FixtureKeys(),desktop=contract,summary_provider=settings.value.summary(),
        recording_factory=recording,discovery_factory=lambda publish:DiscoveryService(publish))
    with TestClient(app) as client:
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            snapshot=receive(ws,'snapshot')
            ws.send_json(envelope({'action':'accept_agreement','version':snapshot['agreement']['version']}))
            receive(ws,'agreement'); receive(ws,'receipt')
            record=envelope({'action':'record','request_id':1,'scope':'microphone','microphone_id':'default',
                'title':'Meeting conversation','duration_s':300,'language':'en','consent_confirmed':True})
            ws.send_json(record)
            sid=receive(ws,'session')['session']['id']
            live=receive(ws,'segment')['segment']
            assert live['speaker_label'] and client.portal.call(app.state.store.get,sid)['status']=='recording'
            ws.send_json(record)
            receipt=receive(ws,'receipt')
            # A duplicate id must never construct a second capture source.
            while receipt['request_id'] != record['request_id']:
                receipt=receive(ws,'receipt')
            assert len(sources)==1
            change=envelope({'action':'save_settings','settings':{'language':'vi'}})
            ws.send_json(change)
            receipt=receive(ws,'receipt')
            while receipt['request_id'] != change['request_id']:
                receipt=receive(ws,'receipt')
            assert receipt['status']=='rejected'
            receive(ws,'error')
            assert contract.settings.value.language=='en'
        # Reconnecting does not stop or restart an owned active recording.
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            assert receive(ws,'snapshot')['active_id']==sid and len(sources)==1
            ws.send_json(envelope({'action':'stop','session_id':sid}))
            completed=None
            for _ in range(100):
                event=ws.receive_json()
                if event['type']=='session' and event['session']['status']=='completed':
                    completed=event['session']; break
            assert completed and sources[0].closed
            assert len(sockets)==1, 'Stop finishes the existing streams; no full-audio second pass'
            ws.send_json(envelope({'action':'meeting_page','session_id':sid}))
            detail=receive(ws,'meeting_page')
            assert detail['segments'] and detail['notes'] and detail['audio']['available']


def test_provider_presets_select_endpoints_and_do_not_reuse_keys():
    from scribeloop_core.api.desktop_settings import SUMMARY_PRESETS
    settings=DesktopSettings(Vault())
    settings.save({'summary_key':'openai-secret'})
    assert settings.value.summary().configured
    settings.save({'summary_provider':'Gemini','summary_model':'example-gemini-model'})
    assert settings.value.summary_endpoint==SUMMARY_PRESETS['Gemini']
    assert not settings.value.summary_key and not settings.value.summary().configured
    settings.save({'summary_key':'gemini-secret'})
    for provider in ('Ollama','LM Studio'):
        settings.save({'summary_provider':provider,'summary_model':'installed-model',
                       'summary_endpoint':'https://must-be-overridden.example/v1'})
        assert settings.value.summary_endpoint==SUMMARY_PRESETS[provider]
        assert settings.value.summary().configured and not settings.value.summary().cloud
        assert not settings.value.summary_key
    settings.save({'summary_provider':'Custom','summary_endpoint':'https://custom.example/v1',
                   'summary_key':'custom-secret','summary_model':'custom-model'})
    assert settings.value.summary_endpoint=='https://custom.example/v1'
    assert settings.value.summary_key=='custom-secret'
    settings.save({'summary_provider':'OpenAI','summary_key':'new-openai-secret'})
    assert settings.value.summary_endpoint==SUMMARY_PRESETS['OpenAI']
    assert settings.value.summary_key=='new-openai-secret'
    assert settings.value.public()['summary_presets']==SUMMARY_PRESETS


def test_transcription_is_operator_managed_and_legacy_user_key_is_ignored(monkeypatch):
    import json
    monkeypatch.setenv('DEEPGRAM_API_KEY','environment-must-not-win')
    vault=Vault()
    vault.value=json.dumps({'deepgram_key':'legacy-must-not-win','summary_key':'summary-secret'})
    settings=DesktopSettings(vault,transcription_key='operator-build-key')
    assert settings.cloud().api_key=='operator-build-key'
    assert settings.value.summary_key=='summary-secret'
    with pytest.raises(ValueError):
        settings.save({'deepgram_key':'user-cannot-change-this'})
    settings.save({'language':'vi'})
    assert 'deepgram_key' not in vault.value
    assert 'operator-build-key' not in vault.value
    assert 'operator-build-key' not in str(settings.value.public())
    assert settings.cloud().api_key=='operator-build-key'


def test_build_credential_is_literal_validated_and_explicit(tmp_path):
    import runpy
    namespace=runpy.run_path(str(Path(__file__).parents[1]/'packaging/macos/build_desktop.py'))
    read=namespace['transcription_key']
    config=tmp_path/'build.env'
    config.write_text('DEEPGRAM_API_KEY="literal-build-key-12345"\nOPENAI_API_KEY=ignored\n')
    assert read({},config)=='literal-build-key-12345'
    assert read({'SCRIBELOOP_BUILD_DEEPGRAM_API_KEY':'explicit-build-key-12345'},config)=='explicit-build-key-12345'
    with pytest.raises(SystemExit):
        read({'SCRIBELOOP_BUILD_DEEPGRAM_API_KEY':'invalid key'},config)
