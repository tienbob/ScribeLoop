"""In-memory discovery service for authenticated UI clients; never opens PCM."""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from uuid import uuid4

from .contracts import ActivitySnapshot, AppIdentity, CaptureSelection
from .detector import MeetingDetector


class DiscoveryService:
    def __init__(self, publish, *, observer=None, apps_provider=lambda: (),
                 clock=time.monotonic, poll_s=1, freshness_s=5, debounce_s=3):
        for value in (poll_s, freshness_s, debounce_s):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError('Discovery intervals must be finite and positive')
        self._publish = publish
        self._observer = observer
        self._apps_provider = apps_provider
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
        self._lock = asyncio.Lock()

    async def start(self):
        await self.refresh()
        if self._observer is not None:
            self._task = asyncio.create_task(self._run())

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

    async def refresh(self):
        async with self._lock:
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
            'status': ('disabled' if self._observer is None else
                       'available' if available else 'unavailable'),
            'recording_enabled': False,
            'permission': 'not_checked',
            'apps': [{'id': app.app_id, 'name': app.name, 'kind': app.kind}
                     for app in self._apps],
            # Actual device enumeration/permission checks are part of M3.
            'microphones': [{'id': 'default', 'name': 'System default microphone',
                             'verified': False}],
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
        if state != self._previous:
            self._previous = state
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
            try:
                await self._load_apps()
            except Exception:  # noqa: BLE001 - sanitize installation-provider failures
                raise ValueError('Cannot verify selected installation') from None
            await self._broadcast()
            if microphone_id != 'default':
                raise ValueError('Microphone is not available')
            app = next((app for app in self._apps if app.app_id == app_id), None)
            if scope == 'application' and app is None:
                raise ValueError('App is not available')
            if scope == 'system' and (app_id is not None or candidate_id is not None):
                raise ValueError('System scope cannot select an app suggestion')
            if candidate_id is not None:
                candidate = next((row for row in self.snapshot()['candidates']
                                  if row['id'] == candidate_id), None)
                if not candidate or not candidate['selectable'] or candidate['app_id'] != app_id:
                    raise ValueError('Suggestion is stale or unavailable')
            selection = CaptureSelection(
                scope=scope, bundle_ids=(app.app_id,) if scope == 'application' else (),
                microphone_device_id=microphone_id)
            return {'scope': selection.scope, 'app_id': app_id,
                    'name': app.name if app else 'All system audio',
                    'microphone_id': microphone_id, 'candidate_id': candidate_id,
                    'recording_enabled': False}


def native_discovery(publish):
    from .macos import MacOSActivityObserver, installed_apps

    return DiscoveryService(publish, observer=MacOSActivityObserver(), apps_provider=installed_apps)


def manual_discovery(publish):
    from .macos import installed_apps

    return DiscoveryService(publish, apps_provider=installed_apps)
