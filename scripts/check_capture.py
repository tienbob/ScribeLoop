"""Bounded WAV check of capture silence diagnostics; not native permission validation."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from scribeloop_core.capture.health import CaptureSilenceMonitor
from scribeloop_core.capture.source import WavFileSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', type=Path, required=True)
    parser.add_argument('--silence-ms', type=float, default=5000)
    parser.add_argument('--threshold-dbfs', type=float, default=-70)
    args = parser.parse_args()
    info = sf.info(args.wav)
    if not 0 < info.duration <= 300:
        parser.error('choose a nonempty WAV excerpt of at most five minutes')
    try:
        monitor = CaptureSilenceMonitor(silence_ms=args.silence_ms,
                                        threshold_dbfs=args.threshold_dbfs)
    except ValueError as exc:
        parser.error(str(exc))
    source = WavFileSource(args.wav)
    events = []
    try:
        for frame in source.frames():
            events.extend(asdict(event) for event in monitor.feed(frame))
    finally:
        source.close()
    print(json.dumps({'audio_s': info.duration, 'events': events,
        'guidance': 'If sound was expected, check playback, selected capture source, and system-audio permission.',
        'limitation': 'Silence may be intentional. WAV replay does not validate native capture or permissions.'},
        indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
