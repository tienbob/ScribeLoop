"""Authenticated recorder controls; native source is replaced with generated PCM."""

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.contracts import AppIdentity
from scribeloop_core.meetings.service import DiscoveryService
from scribeloop_core.pipeline.recording import RecordingRunner
from test_recording import Keys, Source


def connection(client):
    return client.websocket_connect('ws://127.0.0.1/ws', headers={'origin':'http://127.0.0.1'})


def command(**extra):
    return {'action':'record', 'request_id':1, 'scope':'application', 'app_id':'com.brave.Browser',
            'microphone_id':'default', 'title':'Browser test', 'consent_confirmed':True, **extra}


def receive(ws, kind, status=None):
    for _ in range(30):
        event = ws.receive_json()
        if event['type'] == kind and (status is None or event['session']['status'] == status):
            return event
    raise AssertionError('Expected event absent')


def test_plaintext_server_rejects_recording():
    app, token = create_app()
    with TestClient(app, base_url='http://127.0.0.1') as client, connection(client) as ws:
        ws.send_text(token)
        assert ws.receive_json()['recording_available'] is False
        ws.send_json(command())
        assert ws.receive_json()['type'] == 'error'
        assert client.portal.call(app.state.store.list) == []


def test_encrypted_recording_consent_exclusivity_stop_and_notes(tmp_path):
    pytest.importorskip('sqlcipher3')
    app_identity = AppIdentity(app_id='com.brave.Browser', name='Brave', kind='browser',
                               bundle_ids=('com.brave.Browser',))
    opened = []
    def discovery(publish):
        return DiscoveryService(publish, apps_provider=lambda: (app_identity,))
    def recording(store, audio, factory, publish):
        def source(*args, **kw):
            instance = Source()
            opened.append(instance)
            return instance
        def transcribe(audio, sid, emit, cancelled):
            for stream in ('mic', 'loopback'):
                assert list(audio.frames(sid, stream))
                emit({'text':'Test call words', 'stream':stream, 'start_ms':0,
                      'end_ms':20, 'is_final':True})
        return RecordingRunner(store, audio, source, publish, transcriber=transcribe)
    app, token = create_app(database=tmp_path/'test.sqlcipher', storage='encrypted', key_store=Keys(),
                            discovery_factory=discovery, recording_factory=recording)
    with TestClient(app, base_url='http://127.0.0.1') as client, connection(client) as ws:
        ws.send_text(token)
        assert ws.receive_json()['recording_available'] is True
        ws.send_json(command(consent_confirmed=False))
        receive(ws, 'error')
        assert opened == []
        ws.send_json(command())
        session = receive(ws, 'session', 'preparing')['session']
        receive(ws, 'session', 'recording')
        ws.send_json(command(request_id=2))
        receive(ws, 'error')
        assert len(opened) == 1
        ws.send_json({'action':'stop','session_id':session['id']})
        receive(ws, 'notes')
        receive(ws, 'session', 'completed')
        assert opened[0].closed
        assert len(client.portal.call(app.state.store.list)) == 1
        assert {s['stream'] for s in client.portal.call(app.state.store.segments,session['id'])} == {'loopback','mic'}
    with TestClient(app, base_url='http://127.0.0.1') as client, connection(client) as ws:
        ws.send_text(token)
        snapshot = ws.receive_json()
        assert 'Test call words' in snapshot['notes'][session['id']]
        assert snapshot['active_id'] is None
