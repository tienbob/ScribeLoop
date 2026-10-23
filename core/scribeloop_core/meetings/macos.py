"""Read-only macOS observer client and installed-app identity discovery.

Bundle mappings come from specific installed app plists, not substring guesses.
This identifies apps for suggestions; it does not verify publisher signatures or
prove that all audio helper processes are covered by per-app recording.
"""
from __future__ import annotations

import json
import math
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from .contracts import ActivitySnapshot, AppIdentity, ProcessActivity

_APPS = (
    ('Microsoft Teams.app', 'Microsoft Teams', 'meeting'),
    ('Microsoft Teams classic.app', 'Microsoft Teams classic', 'meeting'),
    ('Slack.app', 'Slack', 'meeting'),
    ('zoom.us.app', 'Zoom', 'meeting'),
    ('Brave Browser.app', 'Brave', 'browser'),
    ('Google Chrome.app', 'Chrome', 'browser'),
    ('Safari.app', 'Safari', 'browser'),
    ('Firefox.app', 'Firefox', 'browser'),
)


def installed_apps(roots: tuple[Path, ...] = (Path('/Applications'),)) -> tuple[AppIdentity, ...]:
    """No app launches, process traversal, URLs, or browser content inspection."""
    apps = {}
    owners = set()
    for root in roots:
        for filename, name, kind in _APPS:
            app = root / filename
            try:
                with (app / 'Contents/Info.plist').open('rb') as source:
                    bundle = plistlib.load(source)['CFBundleIdentifier']
                if (not isinstance(bundle, str) or not bundle.strip()
                        or len(bundle) > 256 or bundle in owners):
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            bundles = {bundle}
            # These are actual nested bundles inside the selected app installation.
            frameworks = app / 'Contents/Frameworks'
            helpers = set(frameworks.glob('*.app/Contents/Info.plist'))
            helpers.update(frameworks.glob(
                '*.framework/Versions/*/Helpers/*.app/Contents/Info.plist'))
            for helper in sorted(helpers):
                try:
                    with helper.open('rb') as source:
                        metadata = plistlib.load(source)
                    identity = metadata.get('CFBundleIdentifier') if isinstance(metadata, dict) else None
                    if (isinstance(identity, str) and identity.strip()
                            and len(identity) <= 256 and identity not in owners):
                        bundles.add(identity)
                except (OSError, ValueError, TypeError):
                    continue
            owners.update(bundles)
            apps[bundle] = AppIdentity(app_id=bundle, name=name, kind=kind,
                                       bundle_ids=tuple(sorted(bundles)))
    return tuple(apps.values())


class MacOSActivityObserver:
    """One bounded metadata query; failures are unavailable, never idle."""

    def __init__(self, helper_path: Path | None = None, *, timeout_s: float = 3):
        if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('Observer timeout must be finite and positive')
        self.helper_path = helper_path or Path('data/native/scribeloop-observer')
        self.timeout_s = timeout_s

    def snapshot(self) -> ActivitySnapshot:
        try:
            if sys.platform != 'darwin':
                raise OSError('macOS required')
            result = subprocess.run([str(self.helper_path.resolve())], capture_output=True,
                                    timeout=self.timeout_s, check=True)
            if len(result.stdout) > 1_048_576:
                raise ValueError('Oversized metadata snapshot')
            payload = json.loads(result.stdout)
            if set(payload) != {'version', 'available', 'complete', 'processes'}:
                raise ValueError('Invalid snapshot envelope')
            if type(payload['version']) is not int or payload['version'] != 1:
                raise ValueError('Unsupported observer version')
            rows = tuple(ProcessActivity.model_validate(row) for row in payload['processes'])
            return ActivitySnapshot(observed_at=time.monotonic(), available=payload['available'],
                                    complete=payload['complete'], processes=rows)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            return ActivitySnapshot(observed_at=time.monotonic(), available=False, complete=False)
