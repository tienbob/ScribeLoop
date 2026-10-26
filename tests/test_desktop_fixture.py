"""The desktop spike must remain isolated, authenticated and read-only."""
import json

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_fixture import (
    FixtureKeys,
    create_fixture_app,
    deny_external_network,
    validate_bootstrap,
)
from starlette.websockets import WebSocketDisconnect

TOKEN = 'a' * 64
LAUNCH = 'b' * 32


def test_bootstrap_strict_and_bounded():
    value = {'protocol': 1, 'token': TOKEN, 'launch_id': LAUNCH, 'fixture': True}
    assert validate_bootstrap(json.dumps(value).encode()) == value
    for change in ({'protocol': True}, {'fixture': False}, {'token': 'secret'}, {'extra': 1}):
        with pytest.raises(ValueError):
            validate_bootstrap(json.dumps(value | change).encode())
    with pytest.raises(ValueError):
        validate_bootstrap(b' ' * 2049)


def test_fixture_network_policy():
    deny_external_network('socket.connect', (None, ('127.0.0.1', 1234)))
    for address in [('example.com', 443), ('192.168.1.1', 80), '/tmp/socket']:
        with pytest.raises(PermissionError):
            deny_external_network('socket.connect', (None, address))


def test_encrypted_fixture_auth_and_read_only(tmp_path, monkeypatch):
    # These credentials must never become configured providers in the fixture.
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-be-used')
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'must-not-be-used')
    keys = FixtureKeys()
    app = create_fixture_app(TOKEN, LAUNCH, tmp_path, keys)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/').status_code == 404
        for origin, token in [('http://evil.example', TOKEN), (None, 'wrong')]:
            with (pytest.raises(WebSocketDisconnect),
                  client.websocket_connect('ws://127.0.0.1/ws',
                      headers={'origin': origin} if origin else {}) as ws):
                ws.send_text(token)
                ws.receive_json()
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(TOKEN)
            snapshot = ws.receive_json()
            assert snapshot['fixture'] is True
            assert snapshot['checks']['provider_calls'] is False
            assert snapshot['checks']['keychain'] == 'not_requested'
            assert len(snapshot['sessions']) == 1
            ws.send_json({'action': 'record'})
            assert ws.receive_json()['type'] == 'error'
            ws.send_json({'action': 'snapshot'})
            assert ws.receive_json() == snapshot
        assert (tmp_path / 'fixture.sqlcipher').read_bytes()[:16] != b'SQLite format 3\x00'
    assert not keys.values
