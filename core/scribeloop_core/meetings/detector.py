"""Stateful audio-activity suggestions; audio activity is never call proof."""

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
    strong_start: bool = False
    startup_allowed: bool = False
    confirmations: int = 0
    retry_on_next_input: bool = False


class MeetingDetector:
    """Confirm microphone transitions; preserve established calls through mute.

    Unknown/incomplete observations cannot confirm a start or accumulate end
    grace. Suggestions are ephemeral and never recording authorization.
    """

    def __init__(
        self,
        apps: tuple[AppIdentity, ...],
        *,
        confirmation_samples: int = 2,
        startup_samples: int = 3,
        end_grace_s: float = 15,
        max_sample_gap_s: float = 5,
        dismiss_cooldown_s: float = 300,
    ):
        for count in (confirmation_samples, startup_samples):
            if type(count) is not int or not 2 <= count <= 16:
                raise ValueError("Confirmation counts must be integers from 2 to 16")
        if startup_samples < confirmation_samples:
            raise ValueError("Startup confirmation cannot be weaker than a known transition")
        for value in (end_grace_s, max_sample_gap_s, dismiss_cooldown_s):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Detector intervals must be finite and positive")
        self.apps = {app.app_id: app for app in apps}
        if len(self.apps) != len(apps):
            raise ValueError("Duplicate app identity")
        self._bundles = {}
        for app in apps:
            for bundle in app.bundle_ids:
                if not bundle.strip() or bundle in self._bundles:
                    raise ValueError("Empty or ambiguous bundle mapping")
                self._bundles[bundle] = app.app_id
        self.confirmation_samples, self.startup_samples = confirmation_samples, startup_samples
        self.end_grace_s, self.max_sample_gap_s = end_grace_s, max_sample_gap_s
        self.dismiss_cooldown_s = dismiss_cooldown_s
        self._episodes: dict[str, _Episode] = {}
        self._baselines: dict[str, set[str]] = {}
        self._dismissed_until: dict[str, float] = {}
        self._previous: float | None = None
        self._healthy = False
        self._generation = 0
        self.available = False
        self.activity = {}
        self.input_activity = {}

    @property
    def candidates(self) -> tuple[MeetingCandidate, ...]:
        return tuple(
            episode.candidate
            for _, episode in sorted(self._episodes.items())
            if episode.candidate is not None and not episode.dismissed
        )

    def dismiss(self, candidate_id: str) -> bool:
        for app_id, episode in self._episodes.items():
            if episode.candidate and episode.candidate.id == candidate_id:
                episode.dismissed = True
                self._dismissed_until[app_id] = (self._previous or 0) + self.dismiss_cooldown_s
                return True
        return False

    def recording_failed(self, app_id: str):
        episode = self._episodes.get(app_id)
        if episode and episode.dismissed:
            # Do not prompt repeatedly during the failed call. A later healthy
            # microphone off→on transition can represent a fresh call/retry.
            episode.retry_on_next_input = True

    def reconfigure(self, apps):
        replacement = MeetingDetector(
            apps,
            confirmation_samples=self.confirmation_samples,
            startup_samples=self.startup_samples,
            end_grace_s=self.end_grace_s,
            max_sample_gap_s=self.max_sample_gap_s,
            dismiss_cooldown_s=self.dismiss_cooldown_s,
        )
        unchanged = {
            identity
            for identity, app in self.apps.items()
            if identity in replacement.apps and replacement.apps[identity].kind == app.kind
        }
        self._episodes = {i: e for i, e in self._episodes.items() if i in unchanged}
        self._baselines = {i: e for i, e in self._baselines.items() if i in unchanged}
        self.apps, self._bundles = replacement.apps, replacement._bundles
        self.activity = {i: state for i, state in self.activity.items() if i in self.apps}
        self.input_activity = {i: state for i, state in self.input_activity.items() if i in self.apps}

    def update(self, snapshot: ActivitySnapshot) -> tuple[MeetingCandidate, ...]:
        now = snapshot.observed_at
        if self._previous is not None and now <= self._previous:
            raise ValueError("Snapshots must have increasing monotonic timestamps")
        continuous = (
            self._healthy
            and self._previous is not None
            and now - self._previous <= self.max_sample_gap_s
        )
        self._previous = now
        self.available = snapshot.available and snapshot.complete
        self._healthy = self.available
        if not self.available:
            self.activity = {}
            self.input_activity = {}
            self._baselines.clear()
            return self.candidates
        if not continuous:
            self._baselines.clear()
            for episode in self._episodes.values():
                episode.inactive_since = None
                if episode.candidate is None:
                    episode.confirmations = 0
                    episode.strong_start = False
                    episode.startup_allowed = False
                    episode.first_seen = now
        active: dict[str, set[str]] = {}
        unknown = set()
        for process in snapshot.processes:
            app_id = self._bundles.get(process.bundle_id)
            if app_id is None:
                continue
            evidence = active.setdefault(app_id, set())
            if process.input_running:
                evidence.add("input_active")
            if process.output_running:
                evidence.add("output_active")
            if process.input_running is None or process.output_running is None:
                unknown.add(app_id)
        self.activity = {
            i: ("active" if active.get(i) else "unknown" if i in unknown else "inactive")
            for i in self.apps
        }
        self.input_activity = {
            i: ('active' if 'input_active' in active.get(i,set()) else
                'unknown' if i in unknown else 'inactive') for i in self.apps
        }
        for app_id, app in self.apps.items():
            evidence = active.get(app_id, set())
            episode = self._episodes.get(app_id)
            if app_id in unknown:
                self._baselines.pop(app_id, None)
                if episode:
                    episode.inactive_since = None
                    if episode.candidate is None:
                        episode.confirmations = 0
                        episode.strong_start = False
                        episode.startup_allowed = False
                continue
            previous = self._baselines.get(app_id)
            rising_input = (
                previous is not None
                and "input_active" not in previous
                and "input_active" in evidence
            )
            duplex = evidence == {"input_active", "output_active"}
            self._baselines[app_id] = evidence.copy()
            if (episode is not None and episode.dismissed and rising_input and continuous
                    and (episode.retry_on_next_input or
                         (episode.inactive_since is not None and now-episode.inactive_since >= 2))):
                # A quick redial can occur before the ordinary 15-second end
                # grace expires. Re-confirm the new input edge after known full
                # inactivity instead of reusing its dismissed candidate ID.
                del self._episodes[app_id]
                episode = None
            if not evidence:
                if episode:
                    episode.confirmations = 0
                    episode.strong_start = False
                    episode.startup_allowed = False
                    if episode.inactive_since is None:
                        episode.inactive_since = now
                    if now - episode.inactive_since >= self.end_grace_s:
                        del self._episodes[app_id]
                continue
            if episode is None:
                if "input_active" not in evidence:
                    continue  # Chat alerts/media alone never arm a call.
                episode = self._episodes[app_id] = _Episode(
                    now,
                    now,
                    strong_start=rising_input,
                    startup_allowed=previous is None and duplex,
                    dismissed=not rising_input and now < self._dismissed_until.get(app_id, 0),
                )
            episode.inactive_since = None
            episode.last_seen = now
            if episode.candidate is None:
                if rising_input:
                    episode.strong_start = True
                    episode.confirmations = 0
                    episode.first_seen = now
                if previous is None and duplex:
                    episode.startup_allowed = True
                if not duplex:
                    episode.confirmations = 0
                    if "input_active" not in evidence:
                        episode.strong_start = False
                        episode.startup_allowed = False
                    continue
                episode.confirmations += 1
                required = (
                    self.startup_samples
                    if app.kind == "browser" or not episode.strong_start
                    else self.confirmation_samples
                )
                if (
                    not (episode.strong_start or episode.startup_allowed)
                    or episode.confirmations < required
                ):
                    continue
                self._generation += 1
            candidate_id = (
                episode.candidate.id if episode.candidate else f"{app_id}:{self._generation}"
            )
            episode.candidate = MeetingCandidate(
                id=candidate_id,
                app_id=app_id,
                name=app.name,
                kind=app.kind,
                evidence=tuple(sorted(evidence)),
                confidence="ambiguous" if app.kind == "browser" or not duplex else "possible",
                first_seen=episode.candidate.first_seen
                if episode.candidate
                else episode.first_seen,
                last_seen=now,
            )
        return self.candidates
