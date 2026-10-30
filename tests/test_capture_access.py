"""Capture access diagnostics never open devices or expose native error bodies."""
import json
import subprocess
from pathlib import Path

import pytest
from scribeloop_core.capture.devices import check_capture_access
from scribeloop_core.pipeline.failures import failure_diagnostic


def test_permission_check_reads_only_helper_status(monkeypatch):
    def run(command,**kwargs):
        assert command == ['/fake/helper','--check-capture-access']
        return subprocess.CompletedProcess(command,0,stdout=json.dumps({
            'microphone':'authorized','output_access':True,'display_available':True}).encode())
    monkeypatch.setattr(subprocess,'run',run)
    assert check_capture_access(Path('/fake/helper')) == {
        'ok':True,'microphone':'authorized','output_access':True,'display_available':True}


@pytest.mark.parametrize('code',[-3801,-3803,-3818,-3811])
def test_native_startup_error_is_specific_without_error_body(code,monkeypatch):
    secret = 'private-path-or-user-content'
    marker = f'Native capture startup failed: output_start:screen:{code}'
    def run(command,**kwargs):
        raise subprocess.CalledProcessError(1,command,stderr=(marker+'\n'+secret).encode())
    monkeypatch.setattr(subprocess,'run',run)
    result = check_capture_access(Path('/fake/helper'))
    assert not result['ok'] and secret not in str(result)
    assert result['diagnostic']['code'] == f'native_output_start_screen_{code}'
    assert str(code) in result['diagnostic']['message']
    if code == -3811:
        assert 'not proof of a missing permission' in result['diagnostic']['message']


def test_unavailable_check_does_not_claim_permission_denied(monkeypatch):
    def run(*args,**kwargs): raise subprocess.TimeoutExpired('helper',15)
    monkeypatch.setattr(subprocess,'run',run)
    result=check_capture_access(Path('/fake/helper'))
    assert result['diagnostic']['code']=='access_check_unavailable'
    assert 'does not establish' in result['diagnostic']['message']
    assert 'private' not in str(failure_diagnostic(RuntimeError('private'),'capture'))
