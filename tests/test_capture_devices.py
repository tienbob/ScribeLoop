"""Device discovery queries metadata only and fails closed on malformed results."""
import json
import subprocess

import pytest
from scribeloop_core.capture.devices import macos_capture_devices
from scribeloop_core.capture.macos_client import MacOSMeetingAudioSource, MacOSMicrophoneSource
from scribeloop_core.meetings.contracts import CaptureSelection

PAYLOAD = {'version': 1, 'permission': 'not_determined',
    'microphones': [{'id': 'usb-mic', 'name': 'USB microphone', 'is_default': True}],
    'running_bundle_ids': ['example.teams']}


def mock_query(monkeypatch, payload=PAYLOAD, error=None):
    monkeypatch.setattr('scribeloop_core.capture.devices.sys.platform', 'darwin')

    def run(command, **kwargs):
        assert command[-1] == '--devices' and '--consent-confirmed' not in command
        assert kwargs['timeout'] == 3
        if error:
            raise error
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode())

    monkeypatch.setattr('scribeloop_core.capture.devices.subprocess.run', run)
    return macos_capture_devices()


def test_device_metadata_without_permission_prompt(monkeypatch):
    state = mock_query(monkeypatch)
    assert state.available and state.permission == 'not_determined'
    assert state.microphones[0].id == 'usb-mic'


@pytest.mark.parametrize('changes', [
    {'version': True}, {'permission': 'granted'}, {'running_bundle_ids': 'example.teams'},
    {'running_bundle_ids': [123]}, {'microphones': [{'id': 'usb-mic'}]},
    {'microphones': PAYLOAD['microphones'] * 2}, {'raw_audio': 'forbidden'},
    {'microphones': {}},
    {'microphones': [{'id': ' ', 'name': 'USB', 'is_default': True}]},
    {'microphones': [{'id': 'default', 'name': 'Alias collision', 'is_default': True}]},
    {'microphones': [*PAYLOAD['microphones'],
                     {'id': 'other', 'name': 'Other', 'is_default': True}]},
])
def test_bad_device_protocol_is_unavailable(monkeypatch, changes):
    state = mock_query(monkeypatch, {**PAYLOAD, **changes})
    assert not state.available and state.permission == 'not_checked'
    assert state.microphones == ()


@pytest.mark.parametrize('error', [FileNotFoundError(), subprocess.TimeoutExpired('helper', 3)])
def test_unavailable_helper_is_not_permission_denial(monkeypatch, error):
    assert mock_query(monkeypatch, error=error).permission == 'not_checked'


@pytest.mark.parametrize('factory,args', [(MacOSMicrophoneSource, ()),
    (MacOSMeetingAudioSource, (CaptureSelection(scope='system', microphone_device_id='default'),))])
def test_source_requires_strict_consent_before_native_work(factory, args):
    for consent in (False, 'true', 1):
        with pytest.raises(ValueError, match='consent'):
            factory(*args, consent_confirmed=consent)


def test_source_arguments_are_explicit_and_construction_is_lazy(monkeypatch, tmp_path):
    monkeypatch.setattr('sys.platform', 'darwin')
    helper = tmp_path / 'native'
    helper.touch()
    selection = CaptureSelection(scope='application', bundle_ids=('example.teams',),
                                  microphone_device_id='usb-mic')
    source = MacOSMeetingAudioSource(selection, consent_confirmed=True, helper_path=helper)
    assert source._source._process is None
    assert source._source.command[1:] == ['--consent-confirmed', '--scope', 'application',
        '--microphone-id', 'usb-mic', '--bundle-id', 'example.teams']
    source.close()
    mic = MacOSMicrophoneSource('usb-mic', consent_confirmed=True, helper_path=helper)
    assert mic._source.expected_streams == ('mic',) and mic._source._process is None
    assert '--microphone-only' in mic._source.command
    mic.close()
