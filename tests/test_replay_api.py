"""Actual ASGI/WebSocket session lifecycle, with deterministic ASR and public WAVs."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from scribeloop_core.api.auth import host_allowed
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from starlette.websockets import WebSocketDisconnect


class FakeASR:
    def __init__(self):
        self.frames = 0
        self.closed = False
        self.flushed = False

    def feed(self, frame):
        self.frames += 1
        return [{'start_ms': frame.t_ms, 'end_ms': frame.t_ms + 100,
                 'text': '<script>unsafe</script>' if self.frames == 1 else 'confirmed words',
                 'is_final': self.frames != 1}]

    def flush(self):
        self.flushed = True
        return []

    def close(self):
        self.closed = True


@pytest.fixture()
def setup(tmp_path):
    wav = tmp_path / 'public.wav'
    sf.write(wav, np.zeros(4800, dtype=np.float32), 16000)
    database = tmp_path / 'sessions.sqlite3'
    backends = []

    def factory():
        backend = FakeASR()
        backends.append(backend)
        return backend

    app, token = create_app(database=database, fixtures={'public': wav}, backend_factory=factory)
    return app, token, database, backends, wav


def authenticated(client, token):
    socket = client.websocket_connect('ws://127.0.0.1/ws', headers={'origin': 'http://127.0.0.1'})
    return socket


def start(ws, consent=True, fixture='public'):
    ws.send_json({'action': 'start', 'fixture': fixture, 'title': 'Public replay',
                  'consent_confirmed': consent})


def until_terminal(ws):
    events = []
    for _ in range(20):
        event = ws.receive_json()
        events.append(event)
        if event['type'] == 'session' and event['session']['status'] in {'completed','failed','stopped'}:
            return events
    pytest.fail('No terminal event')


def test_confirmed_persistence_reconnect_and_no_partials_on_disk(setup):
    app, token, database, backends, _ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with authenticated(client, token) as ws:
            ws.send_text(token)
            assert ws.receive_json()['sessions'] == []
            start(ws)
            events = until_terminal(ws)
            assert events[-1]['session']['status'] == 'completed'
            assert any(e['type'] == 'partial' for e in events)
            session_id = events[-1]['session']['id']
        with authenticated(client, token) as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['sessions'][0]['id'] == session_id
            assert len(snapshot['segments'][session_id]) == 2
            assert all(s['text'] == 'confirmed words' for s in snapshot['segments'][session_id])
    assert backends[0].closed
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM segments WHERE revision=0').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM sessions WHERE consent_confirmed=1').fetchone()[0] == 1


def test_wrong_token_unicode_and_cross_origin_rejected(setup):
    app, token, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client:
        for wrong in ['wrong', '🦉']:
            with authenticated(client, token) as ws:
                ws.send_text(wrong)
                with pytest.raises(WebSocketDisconnect) as exc:
                    ws.receive_json()
                assert exc.value.code == 1008
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
                'ws://127.0.0.1/ws', headers={'origin': 'https://evil.example'}):
            pass
        assert client.get('/healthz', headers={'host': 'evil.example'}).status_code == 400


def test_consent_fixture_and_coerced_consent_rejected(setup):
    app, token, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with authenticated(client, token) as ws:
            ws.send_text(token)
            ws.receive_json()
            for consent, fixture in [(False, 'public'), (True, '/etc/passwd'), ('true', 'public')]:
                start(ws, consent, fixture)
                assert ws.receive_json()['type'] == 'error'
        with authenticated(client, token) as ws:
            ws.send_text(token)
            assert ws.receive_json()['sessions'] == []


def test_failure_finishes_session_and_allows_another_start(setup):
    _, _, database, _, wav = setup

    def factory():
        raise RuntimeError('private diagnostic should not be sent to clients')

    app, token = create_app(database=database, fixtures={'public':wav}, backend_factory=factory)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with authenticated(client, token) as ws:
            ws.send_text(token)
            ws.receive_json()
            start(ws)
            events = until_terminal(ws)
            assert events[-1]['session']['status'] == 'failed'
            assert 'private diagnostic' not in str(events)
        # Reconnect after task finalization, then retry.
        with authenticated(client, token) as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['active_id'] is None
            start(ws)
            assert until_terminal(ws)[-1]['session']['status'] == 'failed'


def test_restart_recovers_interrupted_session_and_static_security(setup):
    app, token, database, *_ = setup
    conn = migrate(connect(database))
    session = SessionStore(conn).create('public', 'Interrupted', '/public.wav')
    conn.close()
    with TestClient(app, base_url='http://127.0.0.1') as client:
        response = client.get('/')
        assert response.status_code == 200
        assert 'frame-ancestors' in response.headers['content-security-policy']
        assert response.headers['cache-control'] == 'no-store'
        assert 'script-src' in client.get('/app.js').headers['content-security-policy']
        with authenticated(client, token) as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['sessions'][0]['status'] == 'interrupted'
            assert snapshot['sessions'][0]['id'] == session['id']
            assert snapshot['active_id'] is None


def test_only_one_active_replay_and_stop(setup):
    app, token, _, _, wav = setup
    sf.write(wav, np.zeros(32000, dtype=np.float32), 16000)
    with TestClient(app, base_url='http://127.0.0.1') as client:  # noqa: SIM117 - retain lifecycle nesting
        with authenticated(client, token) as ws:
            ws.send_text(token)
            ws.receive_json()
            start(ws)
            session_id = ws.receive_json()['session']['id']
            start(ws)
            for _ in range(10):
                if ws.receive_json()['type'] == 'error':
                    break
            else:
                pytest.fail('Concurrent replay was not rejected')
            ws.send_json({'action':'stop', 'session_id':session_id})
            assert until_terminal(ws)[-1]['session']['status'] == 'stopped'


@pytest.mark.parametrize('host,expected', [('127.0.0.1:8757',True),('[::1]:8757',True),
    ('user@127.0.0.1',False),('127.0.0.1:bad',False),('127.0.0.1/evil',False)])
def test_host_parser(host, expected):
    assert host_allowed(host) is expected


def test_fixture_removed_after_startup_finishes_instead_of_hanging(setup):
    app, token, _, backends, wav = setup
    wav.unlink()
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        ws.send_text(token)
        ws.receive_json()
        start(ws)
        assert until_terminal(ws)[-1]['session']['status'] == 'failed'
        assert backends == []
        # A same-socket restart must be accepted immediately after terminal state.
        sf.write(wav, np.zeros(4800, dtype=np.float32), 16000)
        start(ws)
        assert until_terminal(ws)[-1]['session']['status'] == 'completed'


@pytest.mark.parametrize('bad_chunk', [
    {'text':'bad', 'start_ms':0, 'end_ms':100, 'is_final':False, 'confidence':float('nan')},
    {'text':'bad', 'start_ms':0, 'end_ms':301, 'is_final':True},
    {'text':'bad', 'start_ms':100, 'end_ms':50, 'is_final':True},
    {'text':' ', 'start_ms':0, 'end_ms':100, 'is_final':False},
    {'text':'bad', 'start_ms':0, 'end_ms':100, 'is_final':True, 'language':'bad\ud800'},
])
def test_invalid_model_output_fails_without_broadcast_or_storage(setup, bad_chunk):
    _, _, database, _, wav = setup

    class BadASR(FakeASR):
        def feed(self, frame):
            return [bad_chunk]

    app, token = create_app(database=database, fixtures={'public':wav}, backend_factory=BadASR)
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        ws.send_text(token)
        ws.receive_json()
        start(ws)
        events = until_terminal(ws)
        assert events[-1]['session']['status'] == 'failed'
        assert not any(e['type'] in {'partial','segment'} for e in events)
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_cleanup_failure_does_not_lock_replay_slot(setup):
    _, _, database, _, wav = setup

    class BadCleanup(FakeASR):
        def close(self):
            raise RuntimeError('cleanup failed')

    app, token = create_app(database=database, fixtures={'public':wav}, backend_factory=BadCleanup)
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        ws.send_text(token)
        ws.receive_json()
        for _ in range(2):
            start(ws)
            assert until_terminal(ws)[-1]['session']['status'] == 'failed'


@pytest.mark.parametrize('origin', ['http://[', 'null', 'http://127.0.0.1/path',
                                    'http://127.0.0.1?query', 'http://127.0.0.1#fragment'])
def test_malformed_origins_are_rejected(setup, origin):
    app, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client, pytest.raises(WebSocketDisconnect), \
            client.websocket_connect('ws://127.0.0.1/ws', headers={'origin':origin}):
        pass


def test_authentication_timeout_closes_socket(setup, monkeypatch):
    monkeypatch.setattr('scribeloop_core.api.server.AUTH_TIMEOUT_S', .03)
    app, token, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008


@pytest.mark.parametrize('authorized', [False, True])
def test_binary_protocol_frames_close_cleanly(setup, authorized):
    app, token, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        if authorized:
            ws.send_text(token)
            ws.receive_json()
        ws.send_bytes(b'binary')
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008


def test_surrogate_title_is_rejected_without_database_failure(setup):
    app, token, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        ws.send_text(token)
        ws.receive_json()
        ws.send_json({'action':'start', 'fixture':'public', 'title':'bad\ud800', 'consent_confirmed':True})
        assert ws.receive_json()['type'] == 'error'
        start(ws)
        assert until_terminal(ws)[-1]['session']['status'] == 'completed'


def test_storage_failure_stops_worker_and_requires_restart(setup, monkeypatch):
    app, token, _, backends, _ = setup

    def broken_append(self, *args):
        raise sqlite3.OperationalError('disk full private path')

    monkeypatch.setattr(SessionStore, 'append', broken_append)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with authenticated(client, token) as ws:
            ws.send_text(token)
            ws.receive_json()
            start(ws)
            for _ in range(10):
                event = ws.receive_json()
                if event['type'] == 'replay_unavailable':
                    break
            else:
                pytest.fail('Storage failure did not stop replay')
            assert 'private path' not in str(event)
            assert backends[0].closed
            assert not backends[0].flushed
            start(ws)
            assert ws.receive_json()['type'] == 'error'
        with authenticated(client, token) as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['replay_available'] is False
            assert snapshot['active_id'] is None


def test_blocked_socket_send_times_out_without_hanging_shutdown(setup, monkeypatch):
    import asyncio

    from fastapi import WebSocket

    original = WebSocket.send_json

    async def blocked(self, data, *args, **kwargs):
        if data['type'] != 'snapshot':
            await asyncio.sleep(10)
        return await original(self, data, *args, **kwargs)

    monkeypatch.setattr(WebSocket, 'send_json', blocked)
    monkeypatch.setattr('scribeloop_core.api.server.SEND_TIMEOUT_S', .03)
    app, token, *_ = setup
    with TestClient(app, base_url='http://127.0.0.1') as client, authenticated(client, token) as ws:
        ws.send_text(token)
        ws.receive_json()
        start(ws)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1013
