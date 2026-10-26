"""Demo connects without a key; production token and browser boundary remain."""
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from starlette.websockets import WebSocketDisconnect


def test_keyless_demo_connects_without_first_frame_and_never_starts_capture():
    app,token=create_app(require_launch_key=False)
    assert token==''
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as client:
        page=client.get('/')
        assert 'data-require-launch-key="false"' in page.text
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            snapshot=ws.receive_json()
            assert snapshot['type']=='snapshot' and not snapshot['sessions']
            assert snapshot['active_id'] is None


@pytest.mark.parametrize('origin',[None,'null','http://evil.example','http://localhost'])
def test_demo_rejects_missing_or_cross_origin(origin):
    app,_=create_app(require_launch_key=False)
    with (TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as client,
          pytest.raises(WebSocketDisconnect),
          client.websocket_connect('ws://127.0.0.1/ws',headers={} if origin is None else {'origin':origin})):
        pass


def test_demo_rejects_non_loopback_client_even_with_matching_headers():
    app,_=create_app(require_launch_key=False)
    with (TestClient(app,base_url='http://127.0.0.1',client=('192.0.2.1',50000)) as client,
          pytest.raises(WebSocketDisconnect),
          client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'})):
        pass


def test_default_api_still_requires_token():
    app,token=create_app()
    assert token
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert 'data-require-launch-key="true"' in client.get('/').text
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            ws.send_text('wrong')
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
