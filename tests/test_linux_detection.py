from scribeloop_core.meetings.detector import MeetingDetector
from scribeloop_core.meetings.linux import LinuxActivityObserver, known_apps


def test_linux_observer_detects_known_microphone_stream(monkeypatch):
    properties = {'application.name': 'Firefox', 'application.process.id': '123'}
    rows = {
        ('list', 'sources'): [{'index': 1, 'name': 'mic', 'monitor_of_sink': None},
                              {'index': 2, 'name': 'output.monitor', 'monitor_of_sink': 0}],
        ('list', 'source-outputs'): [{'source': 2, 'properties': properties}],
        ('list', 'sink-inputs'): [],
    }
    monkeypatch.setattr('scribeloop_core.meetings.linux.pulse_query', lambda *args: rows[args])
    observer = LinuxActivityObserver()
    detector = MeetingDetector(known_apps())
    detector.update(observer.snapshot())
    assert not detector.candidates
    rows[('list', 'source-outputs')].append({'source': 1, 'properties': properties})
    rows[('list', 'sink-inputs')].append({'properties': properties})
    detector.update(observer.snapshot())
    detector.update(observer.snapshot())
    detector.update(observer.snapshot())
    assert [candidate.app_id for candidate in detector.candidates] == ['firefox']


def test_linux_observer_failure_is_unavailable(monkeypatch):
    def fail(*args):
        raise OSError('audio server unavailable')
    monkeypatch.setattr('scribeloop_core.meetings.linux.pulse_query', fail)
    snapshot = LinuxActivityObserver().snapshot()
    assert not snapshot.available
    assert not snapshot.complete


def test_linux_observer_ignores_corked_and_unidentified_streams(monkeypatch):
    rows = {
        ('list', 'sources'): [{'index': 1, 'name': 'mic'}],
        ('list', 'source-outputs'): [
            {'source': 1, 'corked': True, 'properties': {
                'application.name': 'Zoom', 'application.process.id': '123'}},
            {'source': 1, 'properties': {'application.name': 'Firefox',
                                         'application.process.id': 'invalid'}},
            {'source': 1, 'properties': {'application.name': 'Unrelated'}}],
        ('list', 'sink-inputs'): [],
    }
    monkeypatch.setattr('scribeloop_core.meetings.linux.pulse_query', lambda *args: rows[args])
    snapshot = LinuxActivityObserver().snapshot()
    assert snapshot.available and snapshot.complete
    assert not snapshot.processes


def test_linux_settings_control_discovery_across_restarts():
    from fastapi.testclient import TestClient
    from scribeloop_core.api.desktop_settings import DesktopSettings
    from scribeloop_core.api.server import create_app
    from scribeloop_core.meetings.service import DiscoveryService

    class Backend:
        def __init__(self):
            self.values = {}
        def get_password(self, service, account):
            return self.values.get((service, account))
        def set_password(self, service, account, value):
            self.values[service, account] = value

    settings = DesktopSettings(Backend())
    app, token = create_app(settings=settings,
        discovery_factory=lambda publish: DiscoveryService(publish))
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as socket:
        socket.send_text(token)
        assert socket.receive_json()['type'] == 'snapshot'
        socket.send_json({'action': 'save_settings', 'settings': {'detection_enabled': False}})
        for _ in range(3):
            event = socket.receive_json()
            if event['type'] == 'settings':
                break
        assert event['type'] == 'settings'
        assert not event['settings']['detection_enabled']
        assert not app.state.discovery.enabled
    app, _ = create_app(settings=settings,
        discovery_factory=lambda publish: DiscoveryService(publish))
    with TestClient(app):
        assert not app.state.discovery.enabled