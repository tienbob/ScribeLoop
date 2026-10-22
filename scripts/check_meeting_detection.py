"""Watch metadata-only candidate suggestions; never capture PCM or start recording."""
from __future__ import annotations

import argparse
import json
import math
import time

from scribeloop_core.meetings.detector import MeetingDetector
from scribeloop_core.meetings.macos import MacOSActivityObserver, installed_apps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-s', type=float, default=10)
    args = parser.parse_args()
    if not math.isfinite(args.duration_s) or not 0 < args.duration_s <= 300:
        parser.error('Duration must be positive and at most five minutes')
    apps = installed_apps()
    detector = MeetingDetector(apps)
    observer = MacOSActivityObserver()
    print(json.dumps({'type': 'capabilities', 'recording_enabled': False,
                      'apps': [app.model_dump() for app in apps]}), flush=True)
    deadline = time.monotonic() + args.duration_s
    previous = None
    while time.monotonic() < deadline:
        snapshot = observer.snapshot()
        candidates = detector.update(snapshot)
        # Do not dump process inventory or persist an activity log.
        state = {'available': detector.available,
                 'candidates': [candidate.model_dump(exclude={'last_seen'})
                                for candidate in candidates]}
        if state != previous:
            print(json.dumps({'type': 'discovery', **state}), flush=True)
            previous = state
        time.sleep(min(1, max(0, deadline - time.monotonic())))


if __name__ == '__main__':
    main()
