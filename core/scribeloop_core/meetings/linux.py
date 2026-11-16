"""Linux application and audio-activity metadata providers.

Discovery reads XDG desktop entries and PulseAudio-protocol stream metadata only.
It never reads PCM, launches applications, or inspects browser content.
"""
from __future__ import annotations

import configparser
import math
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

from .contracts import ActivitySnapshot, AppIdentity, ProcessActivity

_APPLICATIONS = {
    'meeting': {
        'teams', 'teams-for-linux', 'microsoft teams', 'slack', 'zoom', 'zoom workplace',
    },
    'browser': {
        'brave', 'brave-browser', 'brave browser', 'chrome', 'google-chrome',
        'google chrome', 'chromium', 'chromium-browser', 'firefox', 'mozilla firefox',
    },
}
_MAX_OUTPUT = 1_048_576
_CATALOG_REFRESH_S = 30


def _data_dirs() -> tuple[Path, ...]:
    home = Path.home()
    data_home = Path(os.environ.get('XDG_DATA_HOME', home / '.local/share'))
    data_dirs = os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')
    roots = (data_home, home / '.local/share/flatpak/exports/share',
             Path('/var/lib/flatpak/exports/share'),
             *(Path(value) for value in data_dirs if value))
    return tuple(dict.fromkeys(root / 'applications' for root in roots))


def _app_kind(*values: str) -> str | None:
    names = {value.strip().casefold() for value in values if value.strip()}
    for kind, known in _APPLICATIONS.items():
        if names & known:
            return kind
    return None


def _desktop_entries() -> tuple[tuple[AppIdentity, frozenset[str]], ...]:
    entries = {}
    for root in _data_dirs():
        try:
            files = sorted(root.rglob('*.desktop'))
        except OSError:
            continue
        for path in files:
            try:
                relative = path.relative_to(root).as_posix()
                identity = relative.removesuffix('.desktop').replace('/', '-')
                if identity in entries:
                    continue
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                with path.open(encoding='utf-8', errors='replace') as source:
                    parser.read_file(source)
                desktop = parser['Desktop Entry']
                if desktop.get('Type', 'Application') != 'Application' or desktop.getboolean('Hidden', fallback=False):
                    continue
                name = desktop.get('Name', '').strip()
                command = shlex.split(desktop.get('Exec', ''), comments=False)
                executable = Path(command[0]).name if command else ''
                startup_class = desktop.get('StartupWMClass', '').strip()
                flatpak_id = desktop.get('X-Flatpak', '').strip()
                kind = _app_kind(identity, startup_class, flatpak_id, name, executable)
                if not name or len(identity) > 256 or kind is None:
                    continue
                aliases = {identity.casefold(), startup_class.casefold(), flatpak_id.casefold(),
                           name.casefold(), executable.casefold()}
                app = AppIdentity(app_id=identity, name=name[:256], kind=kind,
                                  bundle_ids=(identity,))
                entries[app.app_id] = (
                    app,
                    frozenset(alias for alias in aliases if alias),
                )
            except (OSError, configparser.Error, KeyError, ValueError):
                continue
    return tuple(entries[key] for key in sorted(entries))


class LinuxApplicationCatalog:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at = float('-inf')
        self._entries = ()

    def entries(self):
        with self._lock:
            now = self._clock()
            if now - self._checked_at >= _CATALOG_REFRESH_S:
                self._entries = _desktop_entries()
                self._checked_at = now
            return self._entries

    def apps(self) -> tuple[AppIdentity, ...]:
        return tuple(app for app, _aliases in self.entries())


_CATALOG = LinuxApplicationCatalog()


def installed_apps() -> tuple[AppIdentity, ...]:
    """List known meeting and browser apps without launching or inspecting them."""
    return _CATALOG.apps()


def _stream_rows(kind: str, timeout_s: float) -> list[dict]:
    result = subprocess.run(
        ['pactl', '--format=json', 'list', kind], capture_output=True,
        timeout=timeout_s, check=True,
    )
    if len(result.stdout) > _MAX_OUTPUT:
        raise ValueError('Oversized audio activity response')
    import json

    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid audio activity response')
    return rows


def _aliases(entries) -> dict[str, str | None]:
    candidates: dict[str, set[str]] = {}
    for app, values in entries:
        for value in (*values, app.app_id.casefold()):
            candidates.setdefault(value, set()).add(app.app_id)
    return {value: next(iter(app_ids)) if len(app_ids) == 1 else None
            for value, app_ids in candidates.items()}


def _row_app(row: dict, aliases: dict[str, str | None]) -> tuple[str | None, int | None]:
    properties = row.get('properties')
    if not isinstance(properties, dict):
        return None, None
    pid_value = properties.get('application.process.id')
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        pid = None
    if pid is not None and pid <= 0:
        pid = None
    values = (
        properties.get('application.id'),
        properties.get('application.name'),
        properties.get('application.process.binary'),
    )
    app_id = None
    for value in values:
        if isinstance(value, str):
            normalized = value.strip().casefold()
            app_id = aliases.get(normalized)
            if app_id is None:
                app_id = aliases.get(Path(normalized).name)
            if app_id is not None:
                break
    return app_id, pid


class LinuxActivityObserver:
    """Read stream activity from PulseAudio or PipeWire's Pulse server."""

    def __init__(self, *, timeout_s: float = 3):
        if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('Observer timeout must be finite and positive')
        self.timeout_s = timeout_s

    def snapshot(self) -> ActivitySnapshot:
        observed_at = time.monotonic()
        try:
            entries = _CATALOG.entries()
            aliases = _aliases(entries)
            playback = _stream_rows('sink-inputs', self.timeout_s)
            capture = _stream_rows('source-outputs', self.timeout_s)
            activity: dict[tuple[str, int], list[bool]] = {}
            incomplete = False
            for rows, direction in ((capture, 0), (playback, 1)):
                for row in rows:
                    app_id, pid = _row_app(row, aliases)
                    if app_id is None:
                        continue
                    if pid is None:
                        incomplete = True
                        continue
                    activity.setdefault((app_id, pid), [False, False])[direction] = True
            processes = tuple(
                ProcessActivity(pid=pid, bundle_id=app_id,
                                input_running=states[0], output_running=states[1])
                for (app_id, pid), states in sorted(activity.items())
            )
            return ActivitySnapshot(observed_at=observed_at, available=True, complete=not incomplete,
                                    processes=processes)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            return ActivitySnapshot(observed_at=observed_at, available=False, complete=False)