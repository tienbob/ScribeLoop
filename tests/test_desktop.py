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


def test_language_hint_selection_survives_reopen_and_validates():
    from scribeloop_core.pipeline.languages import HINT_LANGUAGE_NAMES, word_language
    settings=DesktopSettings(Vault())
    assert settings.value.public()['language_hints'] == ['en','vi']
    selected=['en','vi','fr','ja','zh-HK']
    settings.save({'language':'auto','language_hints':selected})
    assert DesktopSettings(settings.backend).value.public()['language_hints'] == selected
    assert settings.value.public()['supported_language_hints'] == HINT_LANGUAGE_NAMES
    for invalid in ([],['invented'],['en',1],'fr'):
        with pytest.raises(ValueError): settings.save({'language_hints':invalid})
    settings.save({'language':'vi'})
    assert settings.value.public()['language_hints'] == ['vi']
    assert word_language('fr-FR') == 'fr'
    assert word_language('zh-HK') == 'zh-HK'


@pytest.mark.parametrize('model,extra',[
    ('StartRecording',{'request_id':1,'scope':'microphone','microphone_id':'default','consent_confirmed':True,'action':'record'}),
    ('AcceptCall',{'candidate_id':'test','app_id':'test.app','consent_confirmed':True,'action':'accept_call'}),
    ('ReprocessRecording',{'session_id':'a'*32,'action':'reprocess'}),
    ('SavedMeeting',{'session_id':'a'*32,'action':'resume'}),
])
def test_recording_commands_validate_multilanguage_hints(model,extra):
    from scribeloop_core.api import server
    schema=getattr(server,model)
    assert schema.model_validate({**extra,'language_hints':['en','fr','ja']}).language_hints == ['en','fr','ja']
    with pytest.raises(ValueError): schema.model_validate({**extra,'language_hints':[]})
    with pytest.raises(ValueError): schema.model_validate({**extra,'language_hints':['unknown']})


def test_microphone_preferences_persist_and_reject_invalid_values():
    settings = DesktopSettings(Vault())
    assert settings.value.microphone_gain_db == 0
    assert settings.value.microphone_noise_filter == 'standard'
    settings.save({'microphone_gain_db':6., 'microphone_noise_filter':'strong', 'microphone_noise_threshold_db':-44.})
    reopened = DesktopSettings(settings.backend)
    assert reopened.value.microphone_gain_db == 6.
    assert reopened.value.public()['microphone_noise_filter'] == 'strong'
    assert reopened.value.microphone_noise_threshold_db == -44.
    for patch in ({'microphone_gain_db':25.}, {'microphone_gain_db':float('inf')},
                  {'microphone_noise_filter':'invalid'}):
        with pytest.raises(ValueError): settings.save(patch)
    assert settings.value.microphone_gain_db == 6.


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


def test_desktop_language_scope_is_english_vietnamese_with_legacy_migration():
    import json

    from scribeloop_core.pipeline.languages import deepgram_language_options, word_language
    vault = Vault()
    vault.value = json.dumps({'language':'fr'})
    settings = DesktopSettings(vault)
    assert settings.value.language == 'auto'
    assert set(settings.cloud().public()['languages']) == {'auto','en','vi'}
    for language in ('auto','en','vi'):
        settings.save({'language':language})
        assert DesktopSettings(vault).value.language == language
    with pytest.raises(ValueError): settings.save({'language':'fr'})
    assert deepgram_language_options('auto') == {'language':'multi','language_hint':['en','vi']}
    assert deepgram_language_options('vi') == {'language':'vi'}
    assert word_language('vi-VN') == 'vi'


@pytest.mark.parametrize('key,preview', [('', ''),('abc','********'),('abcd','********'),
    ('abcde','abcd********'),('sk-example-secret','sk-e********')])
def test_saved_key_preview_is_masked_and_never_replaces_actual_key(key,preview):
    settings=DesktopSettings(Vault())
    public=settings.save({'summary_key':key})
    assert public['summary_key_preview']==preview
    assert 'summary_key' not in public
    if len(key)>4:
        assert key not in str(public)
    settings.save({'summary_model':'updated-model'})
    assert DesktopSettings(settings.backend).value.summary_key==key


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
    settings._cloud.connector = connector
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
            customer_key = 'customer-synthetic-key-123456789'
            ws.send_json(envelope({'action':'save_settings','settings':{'deepgram_key':customer_key}}))
            event = receive(ws,'settings')
            assert event['settings']['transcription_configured']
            assert event['transcription']['configured']
            assert customer_key not in str(event)
            assert receive(ws,'receipt')['status']=='accepted'
            assert app.state.recording.base_config.api_key == customer_key
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
            ws.send_json(envelope({'action':'set_recording_paused','session_id':sid,'paused':True}))
            assert receive(ws,'recording_state')['paused']
        # Reconnecting does not stop or restart an owned active recording.
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            snapshot = receive(ws,'snapshot')
            assert snapshot['active_id']==sid and len(sources)==1 and snapshot['recording_paused']
            ws.send_json(envelope({'action':'set_recording_paused','session_id':sid,'paused':False}))
            assert not receive(ws,'recording_state')['paused']
            ws.send_json(envelope({'action':'set_recording_paused','session_id':sid,'paused':True}))
            assert receive(ws,'recording_state')['paused']
            ws.send_json(envelope({'action':'stop_recording'}))
            completed=None
            for _ in range(100):
                event=ws.receive_json()
                if event['type']=='session' and event['session']['status']=='completed':
                    completed=event['session']; break
            assert completed and sources[0].closed
            assert completed['capture']['microphone_processing'] == {
                'gain_db':0., 'noise_filter':'standard', 'noise_threshold_db':-40.}
            assert (tmp_path/'recordings'/sid/'mic.raw.slpa').is_file()
            assert len(sockets)==1, 'Stop finishes the existing streams; no full-audio second pass'
            # A delayed Stop or a stale UI must not fall back to replay rejection.
            for command in ({'action':'stop','session_id':sid}, {'action':'stop_recording'}):
                request = envelope(command)
                ws.send_json(request)
                assert receive(ws,'recording_state')['active_id'] is None
                receipt = receive(ws,'receipt')
                while receipt['request_id'] != request['request_id']:
                    receipt = receive(ws,'receipt')
                assert receipt['status'] == 'accepted'
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


def test_provider_switch_restores_each_provider_saved_key():
    settings=DesktopSettings(Vault())
    settings.save({'summary_key':'openai-secret'})
    # Gemini has no saved key yet: switching must not reuse the OpenAI key.
    settings.save({'summary_provider':'Gemini','summary_model':'example-gemini-model'})
    assert not settings.value.summary_key
    settings.save({'summary_key':'gemini-secret'})
    # Switching back and forth restores each provider's own key: no re-entry.
    settings.save({'summary_provider':'OpenAI','summary_model':'gpt-5.4-mini'})
    assert settings.value.summary_key=='openai-secret'
    assert settings.value.summary().configured
    settings.save({'summary_provider':'Gemini','summary_model':'example-gemini-model'})
    assert settings.value.summary_key=='gemini-secret'
    assert settings.value.summary_keys=={'OpenAI':'openai-secret','Gemini':'gemini-secret'}


def test_saved_provider_keys_are_private_and_survive_reopen():
    settings=DesktopSettings(Vault())
    settings.save({'summary_key':'openai-secret'})
    settings.save({'summary_provider':'Gemini','summary_key':'gemini-secret',
                   'summary_model':'example-gemini-model'})
    public=settings.value.public()
    assert 'summary_keys' not in public and 'summary_key' not in public
    assert 'openai-secret' not in str(public) and 'gemini-secret' not in str(public)
    assert public['summary_key_previews']=={'OpenAI':'open********','Gemini':'gemi********'}
    assert public['summary_key_preview']=='gemi********'
    reopened=DesktopSettings(settings.backend).value
    assert reopened.summary_keys=={'OpenAI':'openai-secret','Gemini':'gemini-secret'}
    assert reopened.summary_key=='gemini-secret'


def test_cleanup_provider_key_and_model_are_remembered_per_provider():
    settings=DesktopSettings(Vault())
    settings.save({'local_cleanup_provider':'OpenAI','local_cleanup_model':'gpt-5.4-mini',
                   'local_cleanup_key':'cleanup-secret'})
    assert settings.value.local_cleanup_keys=={'OpenAI':'cleanup-secret'}
    assert settings.value.local_cleanup_models=={'OpenAI':'gpt-5.4-mini'}
    assert settings.value.cleanup().cloud is True
    # A provider with no saved values starts blank instead of reusing another
    # provider's key or model; switching back restores the saved ones.
    settings.save({'local_cleanup_provider':'Ollama','local_cleanup_model':'text-model'})
    assert settings.value.local_cleanup_key==''
    assert settings.value.local_cleanup_models=={'OpenAI':'gpt-5.4-mini','Ollama':'text-model'}
    settings.save({'local_cleanup_provider':'OpenAI'})
    assert settings.value.local_cleanup_key=='cleanup-secret'
    assert settings.value.local_cleanup_model=='gpt-5.4-mini'
    public=settings.value.public()
    assert 'local_cleanup_key' not in public and 'local_cleanup_keys' not in public
    assert 'cleanup-secret' not in str(public)
    assert public['local_cleanup_key_previews']=={'OpenAI':'clea********'}
    assert public['local_cleanup_models']=={'OpenAI':'gpt-5.4-mini','Ollama':'text-model'}


def test_notes_model_is_remembered_per_provider():
    settings=DesktopSettings(Vault())
    settings.save({'summary_key':'openai-secret','summary_model':'gpt-5.4-mini'})
    assert settings.value.summary_models=={'OpenAI':'gpt-5.4-mini'}
    # Switching to Gemini with a new model remembers it under Gemini; the
    # OpenAI entry survives for the switch back.
    settings.save({'summary_provider':'Gemini','summary_model':'example-gemini-model'})
    assert settings.value.summary_models=={'OpenAI':'gpt-5.4-mini','Gemini':'example-gemini-model'}
    # A provider switch without an explicit model restores the saved one.
    settings.save({'summary_provider':'OpenAI','summary_key':'new-openai-secret'})
    assert settings.value.summary_model=='gpt-5.4-mini'
    settings.save({'summary_provider':'Gemini'})
    assert settings.value.summary_model=='example-gemini-model'
    # A provider with no saved model carries the previous model over on a
    # backend switch; the UI always sends an explicit model, so this only
    # affects API callers.
    settings.save({'summary_provider':'LM Studio'})
    assert settings.value.summary_model=='example-gemini-model'
    assert settings.value.summary_models=={'OpenAI':'gpt-5.4-mini',
        'Gemini':'example-gemini-model','LM Studio':'example-gemini-model'}
    assert settings.value.public()['summary_models']=={'OpenAI':'gpt-5.4-mini',
        'Gemini':'example-gemini-model','LM Studio':'example-gemini-model'}


def test_legacy_single_key_settings_migrate_to_provider_keys():
    import json

    from scribeloop_core.api.desktop_settings import ACCOUNT, SERVICE
    backend=Vault()
    backend.set_password(SERVICE,ACCOUNT,json.dumps({'summary_provider':'OpenAI',
        'summary_key':'legacy-secret','summary_model':'gpt-5.4-mini'}))
    settings=DesktopSettings(backend)
    assert settings.value.summary_keys=={'OpenAI':'legacy-secret'}
    assert settings.value.summary_models=={'OpenAI':'gpt-5.4-mini'}
    assert settings.value.summary_key=='legacy-secret'
    assert settings.value.summary().configured


def test_customer_transcription_key_persists_without_secret_echo(monkeypatch):
    import json
    monkeypatch.setenv('DEEPGRAM_API_KEY','environment-must-not-win')
    vault = Vault()
    settings = DesktopSettings(vault)
    assert not settings.cloud().api_key
    key = 'customer-owned-synthetic-key-12345'
    public = settings.save({'deepgram_key':key})
    assert settings.cloud().api_key == key
    assert public['transcription_configured']
    assert 'deepgram_key' not in public and key not in str(public)
    assert key not in repr(settings.value)
    reopened = DesktopSettings(vault)
    assert reopened.cloud().api_key == key
    reopened.save({'language':'vi'})
    assert json.loads(vault.value)['deepgram_key'] == key
    for bad in ('short', 'invalid key with spaces', 'x'*513):
        with pytest.raises(ValueError): reopened.save({'deepgram_key':bad})
    reopened.save({'deepgram_key':''})
    assert not reopened.cloud().api_key
    assert not DesktopSettings(vault).value.public()['transcription_configured']


def test_summary_instructions_roundtrip_clear_and_unicode_bounds():
    settings=DesktopSettings(Vault())
    guide='Ưu tiên quyết định, người phụ trách và ngày đến hạn.'
    public=settings.save({'summary_instructions':guide})
    assert public['summary_instructions']==guide
    assert DesktopSettings(settings.backend).value.summary().instructions==guide
    settings.save({'summary_instructions':'😀'*2000})
    assert DesktopSettings(settings.backend).value.summary_instructions=='😀'*2000
    with pytest.raises(ValueError): settings.save({'summary_instructions':'x'*2001})
    settings.save({'summary_instructions':''})
    assert settings.value.summary().instructions==''


def test_cleanup_keeps_inherited_provider_key_and_model_when_notes_switch():
    settings=DesktopSettings(Vault())
    settings.save({'summary_provider':'Gemini','summary_model':'gemini-test',
                   'summary_key':'gemini-secret','local_cleanup_provider':'Gemini'})
    assert settings.value.cleanup()._api_key=='gemini-secret'
    assert settings.value.cleanup().model=='gemini-test'
    settings.save({'summary_provider':'Ollama','summary_model':'local-notes'})
    assert settings.value.cleanup()._api_key=='gemini-secret'
    assert settings.value.cleanup().model=='gemini-test'
    reopened=DesktopSettings(settings.backend)
    assert reopened.value.cleanup()._api_key=='gemini-secret'
    assert reopened.value.cleanup().model=='gemini-test'
    settings.save({'local_cleanup_key':'dedicated-cleanup-secret'})
    assert settings.value.cleanup()._api_key=='dedicated-cleanup-secret'
    assert settings.value.summary_keys['Gemini']=='gemini-secret'


def test_cleanup_never_inherits_notes_key_for_another_endpoint():
    settings=DesktopSettings(Vault())
    settings.save({'summary_provider':'Gemini','summary_model':'gemini-test',
                   'summary_key':'gemini-secret','local_cleanup_provider':'Gemini',
                   'local_cleanup_model':'cleanup-model',
                   'local_cleanup_endpoint':'https://other.example/v1'})
    assert settings.value.cleanup() is None
    settings.save({'summary_provider':'Ollama','summary_model':'local-notes'})
    assert settings.value.cleanup() is None


def test_transcript_only_preference_survives_provider_changes_and_restart():
    vault = Vault()
    settings = DesktopSettings(vault)
    assert settings.value.automatic_notes is True  # Existing settings preserve behavior.
    settings.save({'automatic_notes':False})
    settings.save({'summary_provider':'Gemini','summary_model':'test-model'})
    reopened = DesktopSettings(vault)
    assert reopened.value.public()['automatic_notes'] is False
    reopened.save({'automatic_notes':True})
    assert DesktopSettings(vault).value.automatic_notes is True
    with pytest.raises(ValueError):
        reopened.save({'automatic_notes':'false'})


def test_detection_pause_survives_core_restart():
    vault = Vault()
    settings = DesktopSettings(vault)
    def open_app(settings):
        return create_app(desktop=DesktopContract(settings,Path('/fake/helper')),
                          summary_provider=settings.value.summary())
    app,token = open_app(settings)
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        receive(ws,'snapshot')
        ws.send_json(envelope({'action':'set_detection','enabled':False}))
        assert receive(ws,'discovery')['discovery']['enabled'] is False
        assert receive(ws,'receipt')['status']=='accepted'
    reopened = DesktopSettings(vault)
    assert reopened.value.detection_enabled is False
    app,token = open_app(reopened)
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        assert receive(ws,'snapshot')['discovery']['enabled'] is False
