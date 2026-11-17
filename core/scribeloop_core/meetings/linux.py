"""Metadata-only activity discovery for PulseAudio and PipeWire-Pulse."""
from __future__ import annotations

import subprocess
import time

from ..capture.linux_pipewire import pulse_query
from .contracts import ActivitySnapshot, AppIdentity, ProcessActivity

_APPS = (
    ('zoom', 'Zoom', 'meeting', ('zoom', 'zoom.us')),
    ('teams', 'Microsoft Teams', 'meeting', ('teams', 'microsoft teams', 'teams-for-linux')),
    ('slack', 'Slack', 'meeting', ('slack',)),
    ('chrome', 'Chrome', 'browser', ('chrome', 'google-chrome', 'google chrome')),
    ('chromium', 'Chromium', 'browser', ('chromium', 'chromium-browser')),
    ('firefox', 'Firefox', 'browser', ('firefox', 'firefox-esr')),
    ('brave', 'Brave', 'browser', ('brave', 'brave-browser')),
    ('edge', 'Microsoft Edge', 'browser', ('microsoft-edge', 'microsoft edge')),
)


def known_apps():
    return tuple(AppIdentity(app_id=app_id, name=name, kind=kind, bundle_ids=(app_id,))
                 for app_id, name, kind, _aliases in _APPS)


def _identity(properties):
    names = {str(properties.get(key, '')).strip().lower() for key in
             ('application.name', 'application.process.binary')}
    return next((app_id for app_id, _name, _kind, aliases in _APPS
                 if names.intersection(aliases)), None)


class LinuxActivityObserver:
    def snapshot(self):
        try:
            sources = pulse_query('list', 'sources')
            inputs = pulse_query('list', 'source-outputs')
            outputs = pulse_query('list', 'sink-inputs')
            if not all(isinstance(rows, list) for rows in (sources, inputs, outputs)):
                raise ValueError('Invalid audio metadata')
            microphones = {row['index'] for row in sources
                           if row.get('monitor_of_sink') in (None, 4294967295)
                           and not row['name'].endswith('.monitor')}
            activity = []
            for rows, is_input in ((inputs, True), (outputs, False)):
                for row in rows:
                    if row.get('corked') is True or (is_input and row['source'] not in microphones):
                        continue
                    properties = row['properties']
                    app_id = _identity(properties)
                    if not app_id:
                        continue
                    try:
                        pid = int(properties.get('application.process.id', 0))
                    except (ValueError, TypeError):
                        continue
                    if pid > 0:
                        activity.append(ProcessActivity(pid=pid, bundle_id=app_id,
                            input_running=is_input, output_running=not is_input))
            return ActivitySnapshot(observed_at=time.monotonic(), available=True,
                                    complete=True, processes=tuple(activity))
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
            return ActivitySnapshot(observed_at=time.monotonic(), available=False, complete=False)