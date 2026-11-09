"""Linux selection, authentication boundary and encrypted notebook regressions."""
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
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


class SecretBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


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
