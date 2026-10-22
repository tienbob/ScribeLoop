"""Read-only native device enumeration; never requests access or starts capture."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..meetings.contracts import MetadataModel


class MicrophoneDevice(MetadataModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    is_default: bool


class CaptureDevices(MetadataModel):
    available: bool
    permission: Literal['authorized', 'denied', 'restricted', 'not_determined', 'unknown', 'not_checked']
    microphones: tuple[MicrophoneDevice, ...] = ()
    running_bundle_ids: tuple[str, ...] = ()


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
        rows = tuple(MicrophoneDevice.model_validate(row) for row in payload['microphones'])
        if len({row.id for row in rows}) != len(rows):
            raise ValueError('Duplicate device identity')
        return CaptureDevices(available=True, permission=payload['permission'],
            microphones=rows, running_bundle_ids=tuple(bundles))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return CaptureDevices(available=False, permission='not_checked')
