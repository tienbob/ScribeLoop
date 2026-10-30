"""Read-only native device enumeration; never requests access or starts capture."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..meetings.contracts import MetadataModel


class MicrophoneDevice(MetadataModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    is_default: bool

    @model_validator(mode='after')
    def valid(self):
        if not self.id.strip() or self.id.startswith('-') or self.id == 'default':
            raise ValueError('Invalid or reserved microphone identity')
        if not self.name.strip():
            raise ValueError('Empty microphone name')
        return self


class CaptureDevices(MetadataModel):
    available: bool
    permission: Literal['authorized', 'denied', 'restricted', 'not_determined', 'unknown', 'not_checked']
    microphones: tuple[MicrophoneDevice, ...] = ()
    running_bundle_ids: tuple[str, ...] = ()

    @model_validator(mode='after')
    def valid(self):
        if len({row.id for row in self.microphones}) != len(self.microphones):
            raise ValueError('Duplicate microphone identity')
        if sum(row.is_default for row in self.microphones) > 1:
            raise ValueError('Ambiguous default microphone')
        return self


def macos_capture_devices(helper_path: Path | None = None) -> CaptureDevices:
    try:
        if sys.platform != 'darwin':
            raise OSError('macOS only')
        helper = Path(helper_path or 'data/native/scribeloop-meeting').resolve()
        result = subprocess.run([str(helper), '--devices'], capture_output=True,
                                timeout=3, check=True)
        if len(result.stdout) > 1_048_576:
            raise ValueError('Oversized device snapshot')
        payload = json.loads(result.stdout)
        if set(payload) != {'version', 'permission', 'microphones', 'running_bundle_ids'}:
            raise ValueError('Invalid device snapshot')
        if type(payload['version']) is not int or payload['version'] != 1:
            raise ValueError('Unsupported device snapshot')
        bundles = payload['running_bundle_ids']
        if (not isinstance(bundles, list) or len(bundles) > 10000
                or any(not isinstance(item, str) or not item.strip() or len(item) > 256
                       for item in bundles)):
            raise ValueError('Invalid running application identities')
        if not isinstance(payload['microphones'], list) or len(payload['microphones']) > 256:
            raise ValueError('Invalid microphone list')
        rows = tuple(MicrophoneDevice.model_validate(row) for row in payload['microphones'])
        return CaptureDevices(available=True, permission=payload['permission'],
            microphones=rows, running_bundle_ids=tuple(bundles))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return CaptureDevices(available=False, permission='not_checked')


def request_microphone_access(helper_path=None):
    """Explicit user action only; no devices/streams are opened here."""
    helper = Path(helper_path or 'data/native/scribeloop-meeting').resolve()
    try:
        result = subprocess.run([str(helper), '--request-microphone-access'],
                                capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('Microphone permission request failed; check System Settings') from None
    return result.stdout.decode().strip()


def check_capture_access(helper_path):
    """Read-only native source enumeration; no audio streams or paid requests."""
    from ..pipeline.failures import failure_diagnostic
    try:
        result = subprocess.run([str(Path(helper_path).resolve()),'--check-capture-access'],
            capture_output=True,timeout=15,check=True)
        if len(result.stdout)>2048:
            raise ValueError('Oversized capture access result')
        value = json.loads(result.stdout)
        if (set(value)!={'microphone','output_access','display_available'} or
                value['microphone'] not in {'authorized','denied','restricted','not_determined','unknown'} or
                type(value['output_access']) is not bool or type(value['display_available']) is not bool):
            raise ValueError('Invalid capture access result')
        return {'ok':True,**value}
    except subprocess.CalledProcessError as exc:
        marker = exc.stderr[-2048:].decode('utf-8',errors='replace')
        return {'ok':False,'diagnostic':failure_diagnostic(RuntimeError(marker),'capture')}
    except (OSError,subprocess.TimeoutExpired,ValueError,TypeError,KeyError):
        return {'ok':False,'diagnostic':{'code':'access_check_unavailable',
            'message':'Could not check capture access. This does not establish that permission is missing.'}}
