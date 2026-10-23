"""Metadata observer protocol failures are never interpreted as idle meetings."""
import json
import plistlib
import subprocess

import pytest
from scribeloop_core.meetings.macos import MacOSActivityObserver, installed_apps


def app(root, path, bundle):
    plist = root / path / 'Contents/Info.plist'
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps({'CFBundleIdentifier': bundle}))


def test_installed_registry_reads_exact_outer_and_nested_bundles(tmp_path):
    app(tmp_path, 'Slack.app', 'example.slack')
    app(tmp_path, 'Slack.app/Contents/Frameworks/Slack Helper.app', 'example.helper')
    app(tmp_path, 'Slack.app/Contents/Frameworks/Engine.framework/Versions/1/Helpers/'
        'Audio Helper.app', 'example.audio')
    app(tmp_path, 'RandomSlack.app', 'example.impostor')
    rows = installed_apps((tmp_path, tmp_path))
    assert len(rows) == 1
    assert rows[0].bundle_ids == ('example.audio', 'example.helper', 'example.slack')
    assert rows[0].kind == 'meeting'


def test_corrupt_plist_is_not_advertised(tmp_path):
    bad = tmp_path / 'Slack.app/Contents/Info.plist'
    bad.parent.mkdir(parents=True)
    bad.write_text('invalid plist')
    assert installed_apps((tmp_path,)) == ()


def test_malformed_helper_does_not_break_installed_app_discovery(tmp_path):
    app(tmp_path, 'Slack.app', 'example.slack')
    helper = tmp_path / 'Slack.app/Contents/Frameworks/Bad.app/Contents/Info.plist'
    helper.parent.mkdir(parents=True)
    helper.write_bytes(plistlib.dumps(['unexpected', 'array']))
    assert installed_apps((tmp_path,))[0].bundle_ids == ('example.slack',)


def invoke(monkeypatch, payload=None, error=None):
    monkeypatch.setattr('scribeloop_core.meetings.macos.sys.platform', 'darwin')

    def run(command, **kwargs):
        assert command[0].endswith('scribeloop-observer')
        assert kwargs['timeout'] == 3 and kwargs['check']
        if error:
            raise error
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode())

    monkeypatch.setattr('scribeloop_core.meetings.macos.subprocess.run', run)
    return MacOSActivityObserver().snapshot()


def test_valid_metadata_only_payload(monkeypatch):
    row = invoke(monkeypatch, {'version': 1, 'available': True, 'complete': True,
        'processes': [{'pid': 123, 'bundle_id': 'example.slack',
                       'input_running': False, 'output_running': True}]})
    assert row.available and row.complete
    assert row.processes[0].output_running


@pytest.mark.parametrize('payload', [
    {}, [], {'version': True, 'available': True, 'complete': True, 'processes': []},
    {'version': 2, 'available': True, 'complete': True, 'processes': []},
    {'version': 1, 'available': True, 'complete': True, 'processes': [], 'pcm': 'forbidden'},
    {'version': 1, 'available': True, 'complete': True, 'processes': [{'pid': 0}]},
    {'version': 1, 'available': 'yes', 'complete': True, 'processes': []},
    {'version': 1, 'available': False, 'complete': True, 'processes': []},
])
def test_invalid_protocol_is_unavailable(monkeypatch, payload):
    row = invoke(monkeypatch, payload)
    assert not row.available and not row.complete and not row.processes


@pytest.mark.parametrize('error', [FileNotFoundError(), subprocess.TimeoutExpired('helper', 3),
                                  subprocess.CalledProcessError(1, 'helper')])
def test_native_error_is_unavailable(monkeypatch, error):
    assert not invoke(monkeypatch, error=error).available
