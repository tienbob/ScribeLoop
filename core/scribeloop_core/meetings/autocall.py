"""Consented recording end heuristic. No audio or model access."""
from __future__ import annotations

import math
import time


class AutoCallMonitor:
    def __init__(self, *, clock=time.monotonic, end_grace_s=60, max_gap_s=5):
        self.clock = clock
        self.end_grace_s = end_grace_s
        self.max_gap_s = max_gap_s
        self.session_id = self.app_id = None
        self.inactive_since = self.last_observed = None

    def track(self, session_id, app_id):
        self.session_id,self.app_id = session_id,app_id
        self.inactive_since = self.last_observed = None

    def clear(self):
        self.track(None,None)

    def keep_recording(self, session_id):
        if session_id != self.session_id:
            raise ValueError('Unknown automatic recording')
        self.inactive_since = self.clock()

    def observe(self, snapshot):
        now = self.clock()
        observed = snapshot.get('activity_observed_at')
        healthy = (snapshot.get('status') == 'available' and isinstance(observed,(int,float)) and
                   math.isfinite(observed) and 0 <= now-observed <= self.max_gap_s)
        activity = snapshot.get('activity',{}).get(self.app_id,'unknown')
        if (not healthy or activity != 'inactive' or (self.last_observed is not None and
                observed-self.last_observed > self.max_gap_s)):
            self.inactive_since = None
        self.last_observed = observed if healthy else None
        if not healthy or activity == 'unknown':
            return {'state':'unknown','remaining_s':None},False
        if activity != 'inactive':
            return {'state':'recording','remaining_s':None},False
        if self.inactive_since is None:
            self.inactive_since = now
        remaining = max(0,math.ceil(self.end_grace_s-(now-self.inactive_since)))
        return {'state':'ending','remaining_s':remaining},remaining == 0
