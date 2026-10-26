"""In-memory discovery service for authenticated UI clients; never opens PCM."""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from uuid import uuid4

from ..capture.devices import CaptureDevices
from .contracts import ActivitySnapshot, AppIdentity, CaptureSelection
from .detector import MeetingDetector


class DiscoveryService:
    def __init__(self, publish, *, observer=None, apps_provider=None,
                 devices_provider=None, clock=time.monotonic, poll_s=1, freshness_s=5, debounce_s=3):
        for value in (poll_s, freshness_s, debounce_s):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError('Discovery intervals must be finite and positive')
        self._publish = publish
        self._observer = observer
        self._apps_provider = apps_provider or (lambda: ())
        self._has_providers = observer is not None or apps_provider is not None or devices_provider is not None
        self._devices_provider = devices_provider
        self._devices = CaptureDevices(available=False, permission='not_checked')
        self._clock = clock
        self._poll_s = poll_s
        self._freshness_s = freshness_s
        self._debounce_s = debounce_s
        self._apps: tuple[AppIdentity, ...] = ()
        self._detector = MeetingDetector((), debounce_s=debounce_s)
        self._incarnation = uuid4().hex
        self._observed_at = None
        self._task = None
        self._previous = None
        self.enabled = True
        self._lock = asyncio.Lock()

    async def start(self):
        await self.refresh()
        if self._has_providers:
            self._task = asyncio.create_task(self._run())

    async def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Invalid detection state')
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if enabled:
            await self.start()
        else:
            await self.close()
            self._detector = MeetingDetector(self._apps, debounce_s=self._debounce_s)
            self._incarnation = uuid4().hex
            self._observed_at = None
        await self._broadcast()

    async def close(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self):
        while True:
            await asyncio.sleep(self._poll_s)
            await self.refresh()

    def _replace_apps(self, apps):
        apps = tuple(apps)
        if apps != self._apps:
            detector = MeetingDetector(apps, debounce_s=self._debounce_s)
            self._apps = apps
            self._detector = detector
            self._incarnation = uuid4().hex
            self._observed_at = None

    async def _load_apps(self):
        # Installation metadata can be read without a working activity observer.
        self._replace_apps(await asyncio.to_thread(self._apps_provider))

    async def _load_devices(self):
        if self._devices_provider:
            try:
                devices = await asyncio.to_thread(self._devices_provider)
                if not isinstance(devices, CaptureDevices):
                    raise TypeError('Invalid device provider response')
                self._devices = devices
            except Exception:  # noqa: BLE001 - device discovery must not disable manual choices
                self._devices = CaptureDevices(available=False, permission='not_checked')

    async def refresh(self):
        async with self._lock:
            # Input-device health must refresh even when installation lookup fails.
            await self._load_devices()
            try:
                await self._load_apps()
                if self._observer is not None:
                    snapshot = await asyncio.to_thread(self._observer.snapshot)
                    self._detector.update(snapshot)
                    self._observed_at = snapshot.observed_at
            except Exception:  # noqa: BLE001 - discovery must not disable replay/manual controls
                now = self._clock()
                # Preserve dismissal across failures; never interpret them as idle.
                if self._observed_at is None or now > self._observed_at:
                    self._detector.update(ActivitySnapshot(
                        observed_at=now, available=False, complete=False))
                    self._observed_at = now
            await self._broadcast()

    def _fresh(self, timestamp):
        return timestamp is not None and 0 <= self._clock() - timestamp <= self._freshness_s

    def snapshot(self):
        available = self._detector.available and self._fresh(self._observed_at)
        return {
            'enabled': self.enabled,
            'status': ('paused' if not self.enabled else 'disabled' if self._observer is None else
                       'available' if available else 'unavailable'),
            'recording_enabled': False,
            'activity': dict(self._detector.activity) if available else {},
            'activity_observed_at': self._observed_at,
            'permission': self._devices.permission,
            'devices_available': self._devices.available,
            'apps': [{'id': app.app_id, 'name': app.name, 'kind': app.kind,
                      'running': app.app_id in self._devices.running_bundle_ids
                      if self._devices.available else None}
                     for app in self._apps],
            'microphones': [{'id': 'default', 'name': 'System default microphone',
                             'enumerated': any(row.is_default for row in self._devices.microphones)},
                            *[{'id': row.id, 'name': row.name, 'enumerated': True}
                              for row in sorted(self._devices.microphones, key=lambda row: row.id)]],
            'candidates': [
                {'id': self._incarnation + ':' + candidate.id,
                 'app_id': candidate.app_id, 'name': candidate.name, 'kind': candidate.kind,
                 'evidence': list(candidate.evidence), 'confidence': candidate.confidence,
                 'selectable': available and self._fresh(candidate.last_seen)}
                for candidate in self._detector.candidates
            ],
        }

    async def _broadcast(self):
        state = self.snapshot()
        # The backend monitor reads fresh timestamps directly. UI clients need
        # state changes, not one event per observation heartbeat.
        comparable = {key:value for key,value in state.items() if key != 'activity_observed_at'}
        if comparable != self._previous:
            self._previous = comparable
            await self._publish({'type': 'discovery', 'discovery': state})

    async def dismiss(self, candidate_id):
        async with self._lock:
            candidates = {row['id']: row for row in self.snapshot()['candidates']}
            if candidate_id not in candidates:
                raise ValueError('Suggestion expired')
            self._detector.dismiss(candidate_id[len(self._incarnation) + 1:])
            await self._broadcast()

    async def select(self, *, scope, app_id, microphone_id, candidate_id=None):
        async with self._lock:
            # Re-read installed identities, not just client-supplied app/bundle IDs.
            if scope != 'microphone':
                try:
                    await self._load_apps()
                except Exception:  # noqa: BLE001 - sanitize installation-provider failures
                    raise ValueError('Cannot verify selected installation') from None
            await self._load_devices()
            await self._broadcast()
            microphone = next((row for row in self._devices.microphones if row.id == microphone_id), None)
            if microphone_id != 'default' and microphone is None:
                raise ValueError('Microphone is not available')
            app = next((app for app in self._apps if app.app_id == app_id), None)
            if scope == 'application' and app is None:
                raise ValueError('App is not available')
            if scope in {'system','microphone'} and (app_id is not None or candidate_id is not None):
                raise ValueError('System/microphone scope cannot select an app suggestion')
            if candidate_id is not None:
                candidate = next((row for row in self.snapshot()['candidates']
                                  if row['id'] == candidate_id), None)
                if not candidate or not candidate['selectable'] or candidate['app_id'] != app_id:
                    raise ValueError('Suggestion is stale or unavailable')
            selection = CaptureSelection(
                scope=scope, bundle_ids=(app.app_id,) if scope == 'application' else (),
                microphone_device_id=microphone_id)
            return {'scope': selection.scope, 'app_id': app_id,
                    'name': 'Microphone only' if scope == 'microphone' else app.name if app else 'All system audio',
                    'kind':app.kind if app else None,
                    'microphone_id': microphone_id, 'candidate_id': candidate_id,
                    'microphone_name': microphone.name if microphone else 'System default microphone',
                    'recording_enabled': False}


def native_discovery(publish):
    from ..capture.devices import macos_capture_devices
    from .macos import MacOSActivityObserver, installed_apps

    return DiscoveryService(publish, observer=MacOSActivityObserver(), apps_provider=installed_apps,
                            devices_provider=macos_capture_devices)


def manual_discovery(publish):
    from ..capture.devices import macos_capture_devices
    from .macos import installed_apps

    return DiscoveryService(publish, apps_provider=installed_apps, devices_provider=macos_capture_devices)
