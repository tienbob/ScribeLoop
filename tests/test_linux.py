"""Linux selection, authentication boundary and encrypted notebook regressions."""
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.linux import backend_command, local_navigation
from scribeloop_core.api.server import create_app
from scribeloop_core.capture.devices import CaptureDevices, MicrophoneDevice
from scribeloop_core.capture.linux_pipewire import (
    LinuxMeetingAudioSource,
    linux_capture_devices,
    resolve_devices,
)
from scribeloop_core.meetings.contracts import AppIdentity, CaptureSelection
from scribeloop_core.meetings.linux import (
    LinuxActivityObserver,
    LinuxApplicationCatalog,
    installed_apps,
)
from scribeloop_core.meetings.service import DiscoveryService


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


def test_backend_command_uses_working_tree_for_module_launch(monkeypatch, tmp_path):
    installed = tmp_path/'usr/lib/scribeloop/launch.py'
    installed.parent.mkdir(parents=True)
    installed.write_text('')
    monkeypatch.setattr(sys, 'argv', ['/workspace/core/scribeloop_core/api/linux.py'])

    assert backend_command(installed) == [sys.executable, '-m', 'scribeloop_core.api.linux']


def test_backend_command_uses_installed_launcher_for_package_launch(monkeypatch, tmp_path):
    installed = tmp_path/'usr/lib/scribeloop/launch.py'
    installed.parent.mkdir(parents=True)
    installed.write_text('')
    monkeypatch.setattr(sys, 'argv', [str(installed)])

    assert backend_command(installed) == [sys.executable, '-I', str(installed.resolve())]


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


def test_linux_capture_supports_requested_eos_stop(monkeypatch):
    class FakeGst:
        class Event:
            @staticmethod
            def new_eos():
                return 'eos'
        class State:
            NULL = 'null'
    monkeypatch.setitem(sys.modules,'gi',SimpleNamespace(require_version=lambda *args: None))
    monkeypatch.setitem(sys.modules,'gi.repository',SimpleNamespace(Gst=FakeGst))
    selection=CaptureSelection(scope='microphone',microphone_device_id='default')
    source=LinuxMeetingAudioSource(selection,consent_confirmed=True)
    events=[]
    class Pipeline:
        def send_event(self,event):
            events.append(event)
            return True
        def set_state(self,state):
            return state
    source._pipeline=Pipeline()
    assert source.request_stop() is True
    assert source._stop_requested.is_set() and events==['eos']
    assert not source._closed.is_set() and source._pipeline is not None
    assert not source.request_stop()
    source.close()
    assert source._closed.is_set() and source._pipeline is None


def test_linux_eos_drain_does_not_require_buffer_flags(monkeypatch):
    import numpy as np

    class Element:
        def set_property(self, *_): pass
        def link(self, *_): return True

    class Buffer:
        pts = 0
        def get_flags(self):
            raise AssertionError('GStreamer BufferFlags is not a portable EOS API')
        def map(self, *_):
            return True, SimpleNamespace(data=np.zeros(1600, dtype='<f4').tobytes())
        def unmap(self, *_): pass

    class Sample:
        def __init__(self, buffer=None):
            self.buffer = buffer
        def get_buffer(self):
            return self.buffer

    class Sink(Element):
        def __init__(self):
            self.samples = [Sample(Buffer()), Sample()]
            self.eos = False
        def emit(self, *_):
            if not self.samples:
                self.eos = True
                return None
            return self.samples.pop(0)
        def get_property(self, name):
            assert name == 'eos'
            return self.eos

    class Bus:
        def pop_filtered(self, *_): return None

    class Pipeline:
        def __init__(self):
            self.events = []
            self.sink = None
        def add(self, _): pass
        def set_state(self, _): return 'success'
        def send_event(self, event):
            self.events.append(event)
            self.sink.eos = True
            return True
        def get_bus(self): return Bus()

    current = SimpleNamespace(pipeline=None)
    def new_pipeline(_name):
        pipeline = Pipeline()
        current.pipeline = pipeline
        return pipeline

    def make_element(name):
        if name == 'appsink':
            sink = Sink()
            current.pipeline.sink = sink
            return sink
        return Element()

    FakeGst = SimpleNamespace(MSECOND=1, SECOND=10_000, CLOCK_TIME_NONE=object(),
        StateChangeReturn=SimpleNamespace(FAILURE='failure'),
        State=SimpleNamespace(NULL='null', PLAYING='playing'),
        MapFlags=SimpleNamespace(READ='read'),
        MessageType=SimpleNamespace(ERROR=1, EOS=2),
        Event=SimpleNamespace(new_eos=staticmethod(lambda: 'eos')),
        Pipeline=SimpleNamespace(new=new_pipeline),
        ElementFactory=SimpleNamespace(make=make_element),
        Caps=SimpleNamespace(from_string=lambda _value: None))
    FakeGst.init = staticmethod(lambda _args: None)
    monkeypatch.setitem(sys.modules,'gi',SimpleNamespace(require_version=lambda *_:None))
    monkeypatch.setitem(sys.modules,'gi.repository',SimpleNamespace(Gst=FakeGst))
    monkeypatch.setattr('scribeloop_core.capture.linux_pipewire.resolve_devices',
                        lambda _selection: [('mic','test-mic')])

    source=LinuxMeetingAudioSource(CaptureSelection(scope='microphone',
        microphone_device_id='default'),consent_confirmed=True)
    frames=iter(source.frames())
    assert next(frames).stream == 'mic'
    assert source.request_stop()
    with pytest.raises(StopIteration):
        next(frames)
    assert current.pipeline.events == ['eos']
    source.close()


def test_linux_capture_stop_before_lazy_pipeline_start(monkeypatch):
    class FakeGst:
        class Event:
            @staticmethod
            def new_eos():
                return 'eos'
    monkeypatch.setitem(sys.modules,'gi',SimpleNamespace(require_version=lambda *args: None))
    monkeypatch.setitem(sys.modules,'gi.repository',SimpleNamespace(Gst=FakeGst))
    selection=CaptureSelection(scope='microphone',microphone_device_id='default')
    source=LinuxMeetingAudioSource(selection,consent_confirmed=True)
    assert source.request_stop() is True
    assert source._stop_requested.is_set() and source._pipeline is None
    source.close()


def test_linux_installed_apps_reads_known_desktop_entries_only(monkeypatch, tmp_path):
    applications = tmp_path/'applications'
    applications.mkdir()
    (applications/'com.slack.Slack.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Slack\nExec=/usr/bin/slack %U\n')
    (applications/'google-chrome.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Google Chrome\nExec=google-chrome %U\n')
    (applications/'text-editor.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Text Editor\nExec=editor %U\n')
    monkeypatch.setattr('scribeloop_core.meetings.linux._data_dirs', lambda: (applications,))
    monkeypatch.setattr('scribeloop_core.meetings.linux._CATALOG', LinuxApplicationCatalog())

    apps = installed_apps()

    assert [(app.app_id, app.kind) for app in apps] == [
        ('com.slack.Slack', 'meeting'), ('google-chrome', 'browser')]


def test_linux_application_catalog_caches_and_refreshes():
    now = [0.0]
    scans = []
    app = AppIdentity(app_id='teams.desktop', name='Microsoft Teams', kind='meeting',
                      bundle_ids=('teams.desktop',))
    catalog = LinuxApplicationCatalog(clock=lambda: now[0])
    def scan():
        scans.append(now[0])
        return ((app, frozenset({'teams.desktop'})),)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr('scribeloop_core.meetings.linux._desktop_entries', scan)
        assert catalog.apps() == (app,)
        now[0] = 10
        assert catalog.apps() == (app,)
        now[0] = 31
        assert catalog.apps() == (app,)
    assert scans == [0, 31]


def test_linux_activity_observer_maps_only_known_audio_stream_metadata(monkeypatch):
    teams = AppIdentity(app_id='teams.desktop', name='Microsoft Teams', kind='meeting',
                        bundle_ids=('teams.desktop',))
    entries = ((teams, frozenset({'teams.desktop', 'microsoft teams', 'teams'})),)
    monkeypatch.setattr('scribeloop_core.meetings.linux._desktop_entries', lambda: entries)
    queried = []

    def rows(kind, timeout_s):
        queried.append((kind, timeout_s))
        if kind == 'source-outputs':
            return [{'properties': {'application.name':'Microsoft Teams',
                                    'application.process.id':'145'}}]
        return [{'properties': {'application.id':'teams.desktop',
                                'application.process.id':'145'}},
                {'properties': {'application.name':'Unrelated app',
                                'application.process.id':'200'}}]

    monkeypatch.setattr('scribeloop_core.meetings.linux._stream_rows', rows)

    snapshot = LinuxActivityObserver(timeout_s=1.5).snapshot()

    assert snapshot.available and snapshot.complete
    assert snapshot.processes[0].model_dump() == {
        'pid':145, 'bundle_id':'teams.desktop', 'input_running':True, 'output_running':True}
    assert queried == [('sink-inputs', 1.5), ('source-outputs', 1.5)]


def test_linux_activity_observer_marks_missing_known_pid_incomplete(monkeypatch):
    teams = AppIdentity(app_id='teams.desktop', name='Microsoft Teams', kind='meeting',
                        bundle_ids=('teams.desktop',))
    monkeypatch.setattr('scribeloop_core.meetings.linux._desktop_entries',
                        lambda: ((teams, frozenset({'teams.desktop', 'microsoft teams'})),))
    monkeypatch.setattr('scribeloop_core.meetings.linux._stream_rows',
        lambda kind, timeout_s: ([{'properties': {'application.name':'Microsoft Teams'}}]
                                 if kind == 'sink-inputs' else []))

    snapshot = LinuxActivityObserver().snapshot()

    assert snapshot.available and not snapshot.complete and not snapshot.processes


def test_linux_activity_observer_reports_unavailable_sound_server(monkeypatch):
    monkeypatch.setattr('scribeloop_core.meetings.linux._desktop_entries', lambda: ())
    def unavailable(*_args, **_kwargs):
        raise OSError('PulseAudio server unavailable')
    monkeypatch.setattr('scribeloop_core.meetings.linux._stream_rows', unavailable)

    snapshot = LinuxActivityObserver().snapshot()

    assert not snapshot.available and not snapshot.complete and not snapshot.processes


@pytest.mark.asyncio
async def test_linux_discovery_rejects_app_scope_without_capture_backend():
    app = AppIdentity(app_id='teams.desktop', name='Microsoft Teams', kind='meeting',
                      bundle_ids=('teams.desktop',))
    devices = CaptureDevices(available=True, permission='authorized',
        microphones=(MicrophoneDevice(id='mic', name='Microphone', is_default=True),))
    async def publish(_event):
        return None
    service = DiscoveryService(publish, apps_provider=lambda: (app,),
        devices_provider=lambda: devices, allow_application_capture=False)
    await service.refresh()

    with pytest.raises(ValueError, match='Per-application capture is unavailable'):
        await service.select(scope='application', app_id=app.app_id, microphone_id='mic')


@pytest.mark.asyncio
async def test_linux_discovery_never_marks_app_suggestions_selectable():
    from scribeloop_core.meetings.contracts import ActivitySnapshot, ProcessActivity

    app = AppIdentity(app_id='teams.desktop', name='Microsoft Teams', kind='meeting',
                      bundle_ids=('teams.desktop',))
    class Observer:
        observed_at = 0
        def snapshot(self):
            self.observed_at += 1
            return ActivitySnapshot(observed_at=self.observed_at, available=True, complete=True,
                processes=(ProcessActivity(pid=123, bundle_id=app.app_id,
                                           input_running=True, output_running=True),))
    async def publish(_event):
        return None
    service = DiscoveryService(publish, observer=Observer(), apps_provider=lambda: (app,),
        allow_application_capture=False, clock=lambda: 3.0)
    await service.refresh()
    await service.refresh()
    await service.refresh()

    candidates = service.snapshot()['candidates']
    assert len(candidates) == 1
    assert candidates[0]['selectable'] is False


class SecretBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


def test_linux_settings_default_detection_off_but_preserves_saved_choice():
    backend = SecretBackend()
    settings = DesktopSettings(backend, default_detection_enabled=False)
    assert settings.value.detection_enabled is False
    backend.set_password('ai.scribeloop.providers.v1', 'desktop-settings',
                         '{"detection_enabled":true}')

    reloaded = DesktopSettings(backend, default_detection_enabled=False)

    assert reloaded.value.detection_enabled is True


def test_saving_blank_key_fields_keeps_existing_api_keys():
    settings = DesktopSettings(SecretBackend())
    settings.save({'summary_provider':'OpenAI', 'summary_endpoint':'https://api.openai.com/v1',
                   'summary_model':'gpt-5.4-mini', 'summary_key':'notes-key-123456789',
                   'deepgram_key':'deepgram-key-123456789'})

    settings.save({'summary_provider':'OpenAI', 'summary_endpoint':'https://api.openai.com/v1',
                   'summary_model':'gpt-5.4-mini', 'detection_enabled':False})

    assert settings.value.summary_key == 'notes-key-123456789'
    assert settings.value.deepgram_key == 'deepgram-key-123456789'
    public = settings.value.public()
    assert public['summary_key_preview'] == 'note********'
    assert public['deepgram_configured'] is True
    assert 'notes-key-123456789' not in str(public)
    assert 'deepgram-key-123456789' not in str(public)


def test_rejected_linux_settings_save_returns_settings_error(tmp_path):
    settings = DesktopSettings(SecretBackend())
    app, _token = create_app(database=tmp_path/'linux-settings.sqlite3', storage='public-demo',
        settings=settings, summary_provider=settings.value.summary(), require_launch_key=False)

    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client, client.websocket_connect(
            'ws://127.0.0.1/ws', headers={'origin':'http://127.0.0.1'}) as ws:
        assert ws.receive_json()['type'] == 'snapshot'
        ws.send_json({'action':'save_settings','settings':{'summary_model':''}})
        event = ws.receive_json()

    assert event['type'] == 'error'
    assert event['context'] == 'settings'
    assert 'Settings were not saved' in event['message']


def test_provider_configuration_is_authenticated_and_keys_are_redacted():
    settings = DesktopSettings(SecretBackend())
    contract = DesktopContract(settings, Path('/fake/helper'))
    app, token = create_app(desktop=contract,summary_provider=settings.value.summary(),
                            cleanup_provider=settings.value.cleanup())
    request_id = uuid.uuid4().hex

    def envelope(command):
        return {'protocol':2,'request_id':request_id,'command':command}

    with TestClient(app) as client:
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text('wrong')
            assert ws.receive()['type'] == 'websocket.close'
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['type'] == 'snapshot'
            assert 'deepgram_key' not in snapshot['settings']
            ws.send_json(envelope({'action':'save_settings','settings':{
                'deepgram_key':'unit-test-key-123456789','summary_key':'private-secret'}}))
            response = ws.receive_json()
            assert response['type'] == 'settings'
            assert response['transcription'] == {}
            assert response['settings']['transcription_configured']
            assert 'private-secret' not in str(response)
            assert 'unit-test-key' not in str(response)
            assert settings.value.deepgram_key == 'unit-test-key-123456789'
