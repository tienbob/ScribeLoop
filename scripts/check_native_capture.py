"""Brief local capture diagnostic; analyzes signal only and saves no audio."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from scribeloop_core.capture.macos_client import MacOSScreenAudioSource
from scribeloop_core.capture.source import SAMPLE_RATE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consent-confirmed', action='store_true', required=True,
                        help='confirm capture is authorized; prefer controlled test playback')
    parser.add_argument('--duration-s', type=float, default=5)
    parser.add_argument('--bundle-id', action='append')
    parser.add_argument('--helper', type=Path, default=Path('data/native/scribeloop-sck'))
    args = parser.parse_args()
    if not 0 < args.duration_s <= 30:
        parser.error('diagnostic duration must be between 0 and 30 seconds')
    source = MacOSScreenAudioSource(args.bundle_id, helper_path=args.helper)
    events = []
    samples = 0
    sum_squares = 0.0
    peak = 0.0
    started = time.monotonic()
    try:
        for frame in source.monitored_frames(lambda event: events.append(asdict(event))):
            samples += frame.pcm.size
            sum_squares += float(np.square(frame.pcm.astype(np.float64)).sum())
            peak = max(peak, float(np.abs(frame.pcm).max()))
            if frame.t_ms / 1000 + frame.pcm.size / SAMPLE_RATE >= args.duration_s:
                break
    except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
        parser.exit(1, f'Capture diagnostic failed: {exc}\n')
    finally:
        source.close()
    print(json.dumps({'audio_s': samples / SAMPLE_RATE, 'wall_s': time.monotonic()-started,
        'rms': (sum_squares / samples) ** .5 if samples else None,
        'peak': peak, 'health_events': events, 'audio_saved': False,
        'signed_permission_validation': False}, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
