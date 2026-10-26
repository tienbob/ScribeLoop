"""Bounded long generated-signal transport/resampler check; no hardware or audio files."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from scribeloop_core.capture.multiplexed import MultiplexedPCMSource
from scribeloop_core.capture.source import SAMPLE_RATE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, default=Path('data/native/scribeloop-meeting'))
    parser.add_argument('--duration-s',type=int,choices=(300,3600,10800),default=300)
    parser.add_argument('--output', type=Path, help='optional aggregate JSON; never PCM')
    args = parser.parse_args()
    source = MultiplexedPCMSource([args.helper.resolve(), '--self-test','--seconds',str(args.duration_s)])
    stats = {}
    samples = {}
    started = time.monotonic()
    for frame in source.frames():
        row = stats.setdefault(frame.stream, {'samples': 0, 'first_ms': frame.t_ms,
                                              'end_ms': frame.t_ms, 'sum_squares': 0.0})
        row['samples'] += len(frame.pcm)
        row['end_ms'] = frame.t_ms + len(frame.pcm) * 1000 / SAMPLE_RATE
        row['sum_squares'] += float(np.square(frame.pcm.astype(np.float64)).sum())
        snippet = samples.setdefault(frame.stream, [])
        if sum(len(item) for item in snippet) < SAMPLE_RATE:
            snippet.append(frame.pcm)
    offset = stats['mic']['first_ms'] - stats['loopback']['first_ms']
    end_offset = stats['mic']['end_ms'] - stats['loopback']['end_ms']
    for stream, row in stats.items():
        row['rms'] = (row.pop('sum_squares') / row['samples']) ** .5
        # Ignore 10 ms of filter startup transient when estimating tone frequency.
        snippet = np.concatenate(samples[stream])[160:SAMPLE_RATE]
        crossings = np.flatnonzero((snippet[:-1] <= 0) & (snippet[1:] > 0))
        row['frequency_hz'] = (len(crossings) - 1) * SAMPLE_RATE / (crossings[-1] - crossings[0])
        if row['samples'] != args.duration_s * SAMPLE_RATE or not 439 < row['frequency_hz'] < 441:
            raise RuntimeError('Synthetic resampling check failed')
    if offset != 150 or abs(end_offset - offset) > 1:
        raise RuntimeError('Shared-clock offset/drift check failed')
    report = {'mode': 'generated signals; no devices opened', 'audio_saved': False,'duration_s':args.duration_s,
        'streams': stats, 'start_offset_ms': offset, 'offset_change_ms': end_offset - offset,
        'wall_s': time.monotonic() - started, 'physical_alignment_validated': False}
    encoded = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n')
    print(encoded)


if __name__ == '__main__':
    main()
