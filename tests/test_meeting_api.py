"""Actual WebSocket discovery/selection controls, with no native capture."""
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.contracts import ActivitySnapshot, AppIdentity, ProcessActivity
from scribeloop_core.meetings.service import DiscoveryService
from starlette.websockets import WebSocketDisconnect

APP = AppIdentity(app_id='example.slack', name='Slack', kind='meeting',
                  bundle_ids=('example.slack',))


class SeededService(DiscoveryService):
    now = 100.0

    async def start(self):
        await self.refresh()
        self.now = 101.5
        await self.refresh()
        self.now = 103.0
        await self.refresh()

    def snapshot_activity(self):
        return ActivitySnapshot(observed_at=self.now, available=True, complete=True,
            processes=(ProcessActivity(pid=22, bundle_id=APP.app_id,
                                       input_running=True, output_running=True),))


@pytest.fixture()
def workspace(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Selection must not open a PCM source')

    monkeypatch.setattr('scribeloop_core.capture.macos_client.MacOSScreenAudioSource', forbidden)

    def factory(publish):
        service = SeededService(publish, apps_provider=lambda: (APP,), clock=lambda: service.now)
        service._observer = type('Observer', (), {'snapshot': lambda _: service.snapshot_activity()})()
        return service

    return create_app(discovery_factory=factory)


def connect(client):
    return client.websocket_connect('ws://127.0.0.1/ws', headers={'origin': 'http://127.0.0.1'})


def select(ws, candidate=None, request_id=1, **overrides):
    command = {'action': 'select_source', 'request_id': request_id, 'scope': 'application',
               'app_id': APP.app_id, 'microphone_id': 'default', 'candidate_id': candidate}
    command.update(overrides)
    ws.send_json(command)


def test_authentication_required_for_discovery_and_selection(workspace):
    app, _token = workspace
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/healthz').json() == {'status': 'ok'}
        with connect(client) as ws:
            select(ws)
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_json()
            assert error.value.code == 1008


def test_candidate_selection_ack_is_private_and_creates_no_session(workspace):
    app, token = workspace
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with connect(client) as one, connect(client) as two:
            one.send_text(token)
            snapshot = one.receive_json()
            assert snapshot['discovery']['recording_enabled'] is False
            candidate, = snapshot['discovery']['candidates']
            two.send_text(token)
            assert two.receive_json()['discovery']['candidates'] == [candidate]
            select(one, candidate['id'])
            assert one.receive_json()['type'] == 'source_selected'
            select(two, request_id=2, scope='system', app_id=None)
            reply = two.receive_json()
            assert reply['type'] == 'source_selected' and reply['request_id'] == 2
            one.send_json({'action': 'dismiss_candidate', 'candidate_id': candidate['id']})
            for ws in (one, two):
                assert ws.receive_json()['discovery']['candidates'] == []
        with connect(client) as ws:
            ws.send_text(token)
            restored = ws.receive_json()
            assert restored['sessions'] == []
            assert restored['discovery']['candidates'] == []
            assert 'selected_source' not in restored


def test_stale_invented_and_malformed_sources_rejected(workspace):
    app, token = workspace
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with connect(client) as ws:
            ws.send_text(token)
            ws.receive_json()
            for fields in (
                {'candidate_id': 'previous-server:example.slack:1'},
                {'app_id': 'arbitrary.bundle'},
                {'app_id': None},
                {'scope': 'system'},
                {'microphone_id': '/arbitrary/device'},
                {'bundle_ids': ['arbitrary.bundle']},
                {'request_id': True},
            ):
                select(ws, **fields)
                reply = ws.receive_json()
                assert reply['type'] == 'error' and reply['context'] == 'meeting'
            ws.send_json({'action': 'record', 'consent_confirmed': True})
            assert ws.receive_json()['type'] == 'error'
            for malformed in ([], {}, None):
                ws.send_json({'action': malformed})
                assert ws.receive_json()['type'] == 'error'
        with connect(client) as ws:
            ws.send_text(token)
            assert ws.receive_json()['sessions'] == []
