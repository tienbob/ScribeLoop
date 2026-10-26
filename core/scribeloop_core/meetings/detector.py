"""Pure, clock-injected candidate detection. Audio activity is not call proof."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import ActivitySnapshot, AppIdentity, MeetingCandidate


@dataclass
class _Episode:
    first_seen: float
    last_seen: float
    candidate: MeetingCandidate | None = None
    dismissed: bool = False
    inactive_since: float | None = None


class MeetingDetector:
    """One episode per exact app identity, aggregating its known helpers.

    Incomplete/unavailable samples never end an episode. Observation gaps reset
    debounce and end-grace accumulation, but preserve emitted IDs/dismissals.
    Returned candidates are in-memory suggestions, never recording authorization.
    """

    def __init__(self, apps: tuple[AppIdentity, ...], *, debounce_s: float = 3,
                 end_grace_s: float = 15, max_sample_gap_s: float = 5):
        for value in (debounce_s, end_grace_s, max_sample_gap_s):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError('Detector intervals must be finite and positive')
        self.apps = {app.app_id: app for app in apps}
        if len(self.apps) != len(apps):
            raise ValueError('Duplicate app identity')
        self._bundles = {}
        for app in apps:
            for bundle in app.bundle_ids:
                if not bundle.strip() or bundle in self._bundles:
                    raise ValueError('Empty or ambiguous bundle mapping')
                self._bundles[bundle] = app.app_id
        self.debounce_s = debounce_s
        self.end_grace_s = end_grace_s
        self.max_sample_gap_s = max_sample_gap_s
        self._episodes: dict[str, _Episode] = {}
        self._previous: float | None = None
        self._healthy = False
        self._generation = 0
        self.available = False
        self.activity = {}

    @property
    def candidates(self) -> tuple[MeetingCandidate, ...]:
        return tuple(episode.candidate for _, episode in sorted(self._episodes.items())
                     if episode.candidate is not None and not episode.dismissed)

    def dismiss(self, candidate_id: str) -> bool:
        for episode in self._episodes.values():
            if episode.candidate and episode.candidate.id == candidate_id:
                episode.dismissed = True
                return True
        return False

    def update(self, snapshot: ActivitySnapshot) -> tuple[MeetingCandidate, ...]:
        now = snapshot.observed_at
        if self._previous is not None and now <= self._previous:
            raise ValueError('Snapshots must have increasing monotonic timestamps')
        continuous = (self._healthy and self._previous is not None
                      and now - self._previous <= self.max_sample_gap_s)
        self._previous = now
        self.available = snapshot.available and snapshot.complete
        self._healthy = self.available
        if not self.available:
            self.activity = {}
            # Keep existing suggestions but mark discovery unavailable separately.
            # A partial snapshot is not evidence that absent processes are idle.
            return self.candidates
        if not continuous:
            for episode in self._episodes.values():
                episode.first_seen = now
                episode.inactive_since = None

        active: dict[str, set[str]] = {}
        unknown: set[str] = set()
        for process in snapshot.processes:
            app_id = self._bundles.get(process.bundle_id)
            if app_id is None:
                continue
            evidence = active.setdefault(app_id, set())
            if process.input_running:
                evidence.add('input_active')
            if process.output_running:
                evidence.add('output_active')
            if process.input_running is None or process.output_running is None:
                unknown.add(app_id)

        self.activity = {app_id:('active' if active.get(app_id) else
            'unknown' if app_id in unknown else 'inactive') for app_id in self.apps}
        for app_id, app in self.apps.items():
            evidence = active.get(app_id, set())
            episode = self._episodes.get(app_id)
            if not evidence:
                if episode is None:
                    continue
                if app_id in unknown:
                    episode.first_seen = now
                    episode.inactive_since = None
                    continue
                if episode.inactive_since is None:
                    episode.inactive_since = now
                if now - episode.inactive_since >= self.end_grace_s:
                    del self._episodes[app_id]
                elif episode.candidate is None:
                    # Debounce requires continuous positive observations.
                    episode.first_seen = now
                continue
            if episode is None:
                episode = self._episodes[app_id] = _Episode(now, now)
            if episode.inactive_since is not None and episode.candidate is None:
                episode.first_seen = now
            episode.inactive_since = None
            episode.last_seen = now
            if now - episode.first_seen < self.debounce_s and episode.candidate is None:
                continue
            if episode.candidate is None:
                self._generation += 1
            candidate_id = (episode.candidate.id if episode.candidate else
                            f'{app_id}:{self._generation}')
            episode.candidate = MeetingCandidate(
                id=candidate_id, app_id=app_id, name=app.name, kind=app.kind,
                evidence=tuple(sorted(evidence)),
                confidence='ambiguous' if app.kind == 'browser' or len(evidence) < 2
                else 'possible',
                first_seen=episode.candidate.first_seen if episode.candidate else episode.first_seen,
                last_seen=now)
        return self.candidates
