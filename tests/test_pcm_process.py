"""Exercise the actual helper pipe and process lifecycle without audio hardware."""
import os
import struct
import sys

import numpy as np
import pytest
from scribeloop_core.capture.pcm_process import PCMProcessSource

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='Native helper transport is POSIX-only')


def helper(tmp_path, body):
    path = tmp_path / 'helper.py'
    path.write_text('import sys, time, struct\n' + body)
    return [sys.executable, '-u', str(path)]


def packet(samples, pts=100):
    return struct.pack('<4sId', b'SLP1', len(samples), pts) + np.array(samples, dtype='<f4').tobytes()


def test_fragmented_pipe_packet_and_sample_timestamps(tmp_path):
    data = packet([.1] * 2000)
    source = PCMProcessSource(helper(tmp_path,
        f'data = {data!r}\nfor i in range(0,len(data),7):\n sys.stdout.buffer.write(data[i:i+7]); sys.stdout.flush()\ntime.sleep(10)\n'))
    frames = source.frames()
    first, tail = next(frames), next(frames)
    assert first.t_ms == 0 and len(first.pcm) == 1600
    assert tail.t_ms == 100 and len(tail.pcm) == 400
    assert np.allclose(first.pcm, .1)
    source.close()
    assert source._process.poll() is not None
    frames.close()


def test_stalled_child_is_terminated(tmp_path):
    source = PCMProcessSource(helper(tmp_path, 'time.sleep(10)\n'), timeout_s=.15)
    with pytest.raises(TimeoutError, match='stalled'):
        next(source.frames())
    assert source._process.poll() is not None


@pytest.mark.parametrize('data', [b'x', struct.pack('<4sId', b'BAD!', 1, 0),
    struct.pack('<4sId', b'SLP1', 16001, 0), packet([float('nan')]),
    struct.pack('<4sId', b'SLP1', 1, float('inf'))])
def test_malformed_or_truncated_packet_fails_closed(tmp_path, data):
    source = PCMProcessSource(helper(tmp_path, f'sys.stdout.buffer.write({data!r})\n'))
    with pytest.raises((ValueError, RuntimeError)):
        next(source.frames())
    assert source._process.poll() is not None


def test_native_error_is_reported_without_hanging_on_stderr(tmp_path):
    source = PCMProcessSource(helper(tmp_path,
        "sys.stderr.write('x'*100000 + 'Permission denied'); sys.exit(3)\n"))
    with pytest.raises(RuntimeError, match='Permission denied'):
        next(source.frames())


def test_health_events_do_not_drop_audio_and_timestamp_gap_survives(tmp_path):
    data = packet([0] * 1600) + packet([0] * 1600, 100.1) + packet([.2] * 1600, 101)
    source = PCMProcessSource(helper(tmp_path,
        f'sys.stdout.buffer.write({data!r}); sys.stdout.flush(); time.sleep(10)\n'), silence_ms=200)
    events = []
    frames = source.monitored_frames(events.append)
    received = [next(frames) for _ in range(3)]
    assert [f.t_ms for f in received] == [0, 100, 1000]
    assert [event.kind for event in events] == ['prolonged_silence', 'signal_recovered']
    assert sum(len(f.pcm) for f in received) == 4800
    frames.close()
    source.close()


def test_missing_helper_cleans_up(tmp_path):
    source = PCMProcessSource([tmp_path / 'missing'])
    with pytest.raises(FileNotFoundError):
        next(source.frames())
    assert source._stderr is None


def test_closed_audio_pipe_with_live_child_times_out(tmp_path):
    source = PCMProcessSource(helper(tmp_path,
        "import os\nos.close(1)\ntime.sleep(10)\n"), timeout_s=.15)
    with pytest.raises(TimeoutError, match='did not exit'):
        next(source.frames())
    assert source._process.poll() is not None


def test_health_callback_failure_stops_capture(tmp_path):
    data = packet([0] * 3200)
    source = PCMProcessSource(helper(tmp_path,
        f'sys.stdout.buffer.write({data!r}); sys.stdout.flush(); time.sleep(10)\n'), silence_ms=100)

    def broken_callback(event):
        raise RuntimeError('consumer failed')

    with pytest.raises(RuntimeError, match='consumer failed'):
        next(source.monitored_frames(broken_callback))
    assert source._process.poll() is not None
