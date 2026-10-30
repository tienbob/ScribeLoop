"""Linux selection, authentication boundary and encrypted notebook regressions."""
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.linux import local_navigation
from scribeloop_core.api.server import create_app
from scribeloop_core.capture.linux_pipewire import (
    LinuxMeetingAudioSource,
    linux_capture_devices,
    resolve_devices,
)
from scribeloop_core.meetings.contracts import CaptureSelection


@pytest.mark.parametrize('uri,expected', [
    ('http://127.0.0.1:1234/#token=secret', True),
    ('http://127.0.0.1:1234/app.js', True),
    ('http://127.0.0.1:1235/', False),
    ('https://127.0.0.1:1234/', False),
    ('http://localhost:1234/', False),
    ('http://user:pass@127.0.0.1:1234/', False),
    ('file:///etc/passwd', False),
    ('javascript:alert(1)', False),
    ('http://127.0.0.1:bad/', False),
])
def test_navigation_is_scoped_to_owned_origin(uri, expected):
    assert local_navigation(uri, 1234) is expected


def test_selection_requires_consent_and_rejects_app_scope():
    selection = CaptureSelection(scope='microphone', microphone_device_id='default')
    with pytest.raises(ValueError, match='consent'):
        LinuxMeetingAudioSource(selection, consent_confirmed=False)
    with pytest.raises(ValueError, match='system audio only'):
        LinuxMeetingAudioSource(CaptureSelection(scope='application', bundle_ids=('app',),
            microphone_device_id='default'), consent_confirmed=True)
    # Construction and closing must work without importing GI or opening audio.
    source = LinuxMeetingAudioSource(selection, consent_confirmed=True)
    source.close()
    source.close()


def pulse_fixture(monkeypatch):
    def query(*args):
        return {('info',): {'default_source_name':'mic', 'default_sink_name':'output'},
                ('list', 'sources'): [
                    {'name':'mic', 'description':'Microphone', 'monitor_of_sink':None},
                    {'name':'output.monitor', 'monitor_of_sink':0}],
                ('list', 'sinks'): [{'name':'output', 'monitor_source':'output.monitor'}]}[args]
    monkeypatch.setattr('scribeloop_core.capture.linux_pipewire.pulse_query', query)


def test_device_discovery_excludes_monitors_and_never_captures(monkeypatch):
    pulse_fixture(monkeypatch)
    devices = linux_capture_devices()
    assert devices.available
    assert [row.id for row in devices.microphones] == ['mic']
    assert devices.microphones[0].is_default


def test_explicit_monitor_resolution_and_missing_mic(monkeypatch):
    pulse_fixture(monkeypatch)
    assert resolve_devices(CaptureSelection(scope='system', microphone_device_id='default')) == [
        ('mic', 'mic'), ('loopback', 'output.monitor')]
    with pytest.raises(ValueError, match='unavailable'):
        resolve_devices(CaptureSelection(scope='microphone', microphone_device_id='gone'))


class SecretBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


def test_provider_configuration_is_authenticated_and_keys_are_redacted():
    backend = SecretBackend()
    settings = DesktopSettings(backend=backend)
    app, token = create_app(settings=settings)
    with TestClient(app) as client:
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text('wrong')
            assert ws.receive()['type'] == 'websocket.close'
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['type'] == 'snapshot'
            assert 'deepgram_key' not in snapshot['settings']
            ws.send_json({'action':'save_settings', 'settings':{
                'deepgram_key':'unit-test-key-123456789', 'summary_key':'private-secret'}})
            response = ws.receive_json()
            assert response['type'] == 'settings'
            assert response['transcription'] == {}
            assert response['settings']['transcription_configured']
            assert 'private-secret' not in str(response)
            assert 'unit-test-key' not in str(response)
            assert settings.value.deepgram_key == 'unit-test-key-123456789'
