"""First-use authorization survives restart, never starts capture, and revokes globally."""
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


def test_agreement_version_persistence_and_revocation(tmp_path):
    path = tmp_path/'settings.sqlite3'
    conn = migrate(connect(path))
    store = SessionStore(conn)
    store.create_recording('Legacy meeting',{'cloud_consent':True})
    assert not store.agreement()['accepted'], 'Old per-call grants never imply acceptance'
    with pytest.raises(ValueError):
        store.accept_agreement('future-version')
    accepted = store.accept_agreement(store.AGREEMENT_VERSION)
    assert accepted['accepted'] and accepted['accepted_at'] is not None
    conn.close()
    conn = migrate(connect(path))
    store = SessionStore(conn)
    assert store.agreement() == accepted
    assert not store.revoke_agreement()['accepted']
    assert store.agreement()['revoked_at'] is not None
    assert store.list(), 'Revocation preserves saved meetings'
    conn.close()


def test_websocket_agreement_is_shared_and_blocks_processing_without_acceptance():
    app,token = create_app()
    headers={'origin':'http://127.0.0.1'}
    with TestClient(app) as client:
        with client.websocket_connect('ws://127.0.0.1/ws',headers=headers) as one, \
                client.websocket_connect('ws://127.0.0.1/ws',headers=headers) as two:
            one.send_text(token)
            snapshot=one.receive_json()
            two.send_text(token)
            assert not two.receive_json()['agreement']['accepted']
            # Neither legacy checkbox grants nor malformed saved actions bypass onboarding.
            for action in ('record','accept_call','resume','reprocess','summarize'):
                one.send_json({'action':action,'cloud_consent':True,'consent_confirmed':True})
                assert 'first-use agreement' in one.receive_json()['message']
            one.send_json({'action':'accept_agreement','version':'obsolete'})
            assert one.receive_json()['type']=='error'
            one.send_json({'action':'accept_agreement','version':snapshot['agreement']['version']})
            assert one.receive_json()['agreement']['accepted']
            assert two.receive_json()['agreement']['accepted']
            assert not client.portal.call(app.state.store.list), 'Agree never starts a session'
            two.send_json({'action':'revoke_agreement'})
            assert not one.receive_json()['agreement']['accepted']
            assert not two.receive_json()['agreement']['accepted']
            one.send_json({'action':'summarize','text_consent':True})
            assert 'first-use agreement' in one.receive_json()['message']
        with client.websocket_connect('ws://127.0.0.1/ws',headers=headers) as ws:
            ws.send_text(token)
            assert ws.receive_json()['agreement']['revoked_at'] is not None


def test_revocation_stops_active_capture_before_saved_transcription(tmp_path,require_sqlcipher):
    from scribeloop_core.meetings.contracts import AppIdentity
    from scribeloop_core.meetings.service import DiscoveryService
    from scribeloop_core.pipeline.recording import RecordingRunner
    from test_recording import Keys, Source
    from test_recording_api import command, connection, receive

    sources=[]
    processed=[]
    def recording(store,audio,factory,publish):
        def source(*args,**kwargs):
            item=Source()
            sources.append(item)
            return item
        def transcribe(*args):
            processed.append(True)
        return RecordingRunner(store,audio,source,publish,transcriber=transcribe)
    identity=AppIdentity(app_id='com.brave.Browser',name='Brave',kind='browser',bundle_ids=('com.brave.Browser',))
    app,token=create_app(storage='encrypted',database=tmp_path/'active.sqlcipher',key_store=Keys(),
        recording_factory=recording,discovery_factory=lambda p:DiscoveryService(p,apps_provider=lambda:(identity,)))
    with TestClient(app,base_url='http://127.0.0.1') as client, connection(client) as ws:
        ws.send_text(token)
        snapshot=ws.receive_json()
        ws.send_json({'action':'accept_agreement','version':snapshot['agreement']['version']})
        receive(ws,'agreement')
        ws.send_json(command(consent_confirmed=False))
        sid=receive(ws,'session','recording')['session']['id']
        ws.send_json({'action':'revoke_agreement'})
        assert not receive(ws,'agreement')['agreement']['accepted']
        receive(ws,'session','interrupted')
        assert sources[0].closed and not processed
        assert client.portal.call(app.state.store.get,sid), 'Revocation keeps captured audio and metadata'
        ws.send_json(command())
        assert 'first-use agreement' in receive(ws,'error')['message']
