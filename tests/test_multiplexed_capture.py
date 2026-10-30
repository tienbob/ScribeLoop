"""Real child-pipe tests for independent tracks, shared clocks, drain, and failures."""
import os
import struct
import sys

import numpy as np
import pytest
from scribeloop_core.capture.multiplexed import MultiplexedPCMSource

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='Native transport is POSIX')
BASE = 10**18
PREFIX = struct.pack('<4sQ', b'SLC2', BASE)
END = struct.pack('<4sIB3sQ', b'SLE2', 0, 255, b'\0\0\0', 0)


def packet(tag, ms, count=1600, *, value=.1, reserved=b'\0\0\0', magic=b'SLP2'):
    return struct.pack('<4sIB3sQ', magic, count, tag, reserved, BASE + ms * 1_000_000) + np.full(count, value, '<f4').tobytes()


def helper(tmp_path, data, tail=''):
    path = tmp_path / 'helper.py'
    path.write_text('import sys,time\n'
                    f'sys.stdout.buffer.write({data!r}); sys.stdout.flush()\n' + tail)
    return [sys.executable, '-u', str(path)]


def test_interleaved_tracks_keep_offset_and_gaps_with_one_origin(tmp_path):
    # Arrival order need not be timestamp order between independent streams.
    data = PREFIX + packet(0, 100) + packet(1, 250) + packet(0, 200) + packet(1, 1000) + END
    source = MultiplexedPCMSource(helper(tmp_path, data))
    frames = list(source.frames())
    assert [(f.stream, f.t_ms) for f in frames] == [('loopback', 100), ('mic', 250),
                                                   ('loopback', 200), ('mic', 1000)]
    assert source._process.poll() == 0 and source.clock.origin_host_ns == BASE


def test_graceful_stop_drains_tail_and_requires_completion(tmp_path):
    data = PREFIX + packet(1, 50)
    tail = f"assert sys.stdin.buffer.read(5)==b'STOP\\n'\nsys.stdout.buffer.write({packet(1, 150, 400) + END!r}); sys.stdout.flush()\n"
    source = MultiplexedPCMSource(helper(tmp_path, data, tail), expected_streams=('mic',))
    assert source.request_stop() is False
    frames = source.frames()
    assert next(frames).stream == 'mic'
    assert source.request_stop() is True
    rest = list(frames)
    assert len(rest) == 1 and len(rest[0].pcm) == 400 and rest[0].t_ms == 150
    assert source.request_stop() is False
    source.close()  # Idempotent.


@pytest.mark.parametrize('data', [
    b'bad', struct.pack('<4sQ', b'BAD!', BASE),
    PREFIX + packet(2, 100), PREFIX + packet(0, -1),
    PREFIX + packet(0, 100, value=float('nan')),
    PREFIX + packet(0, 100, reserved=b'bad'), PREFIX + packet(0, 100, magic=b'BAD!'),
    PREFIX + packet(0, 100) + packet(0, 150),
    PREFIX + packet(0, 100) + END,  # Missing mic cannot be complete.
    PREFIX + packet(0, 100) + packet(1, 100),  # Missing completion.
    PREFIX + packet(0, 100) + packet(1, 100) + END + b'extra',
    PREFIX + struct.pack('<4sIB3sQ', b'SLP2', 16001, 0, b'\0\0\0', BASE),
])
def test_malformed_or_incomplete_stream_fails_closed(tmp_path, data):
    source = MultiplexedPCMSource(helper(tmp_path, data))
    with pytest.raises((ValueError, RuntimeError)):
        list(source.frames())
    assert source._process.poll() is not None


def test_one_busy_stream_does_not_hide_other_stream_stall(tmp_path):
    path = tmp_path / 'stall.py'
    path.write_text('import sys,time,struct\n' + f'sys.stdout.buffer.write({PREFIX!r})\n'
        'for i in range(20):\n'
        f' sys.stdout.buffer.write(struct.pack("<4sIB3sQ",b"SLP2",1600,0,b"\\0\\0\\0",{BASE}+i*100000000)+b"\\0"*6400); sys.stdout.flush(); time.sleep(.05)\n')
    source = MultiplexedPCMSource([sys.executable, '-u', str(path)], timeout_s=.15)
    with pytest.raises(TimeoutError, match='stream stalled'):
        list(source.frames())
    assert source._process.poll() is not None


def test_health_monitor_is_independent_per_stream(tmp_path):
    data = PREFIX + packet(0, 100) + packet(1, 250) + END
    events = []
    frames = list(MultiplexedPCMSource(helper(tmp_path, data)).monitored_frames(events.append))
    assert len(frames) == 2 and events == []


def test_startup_denial_reports_native_reason_instead_of_truncated_audio(tmp_path):
    command = helper(tmp_path, b'', "sys.stderr.write('Microphone access is not authorized'); sys.exit(1)\n")
    source = MultiplexedPCMSource(command)
    with pytest.raises(RuntimeError, match='Microphone access is not authorized'):
        list(source.frames())
    assert source._process.poll() == 1


def test_late_packet_cannot_reset_expired_stream_deadline(tmp_path):
    # A late mic packet must not reset its expired deadline before it is checked.
    initial = PREFIX + packet(0, 0) + packet(1, 0)
    body = (f'time.sleep(.12)\nsys.stdout.buffer.write({packet(0, 100)!r}); sys.stdout.flush()\n'
            f'time.sleep(.12)\nsys.stdout.buffer.write({packet(1, 100)!r}); sys.stdout.flush()\n'
            f'sys.stdout.buffer.write({END!r}); sys.stdout.flush()\n')
    source = MultiplexedPCMSource(helper(tmp_path, initial, body), timeout_s=.2)
    with pytest.raises(TimeoutError, match='stalled'):
        list(source.frames())


@pytest.mark.parametrize('initial', [b'', packet(0, 0)], ids=['no-pcm', 'one-track'])
def test_accepted_startup_stop_allows_missing_tracks(tmp_path, initial):
    # Synchronize STOP before reading completion; never open capture devices.
    tail = ("assert sys.stdin.buffer.read(5)==b'STOP\\n'\n"
            f"sys.stdout.buffer.write({END!r}); sys.stdout.flush()\n")
    source = MultiplexedPCMSource(helper(tmp_path, PREFIX + initial, tail))
    start = source._start_process
    def start_and_stop(**kwargs):
        start(**kwargs)
        assert source.request_stop() is True
    source._start_process = start_and_stop
    assert len(list(source.frames())) == bool(initial)
    assert source._process.returncode == 0


@pytest.mark.parametrize('ending,exit_code', [(END + b'extra', 0), (END, 1), (b'', 0)])
def test_startup_stop_still_requires_valid_completion_and_success(tmp_path, ending, exit_code):
    tail = ("assert sys.stdin.buffer.read(5)==b'STOP\\n'\n"
            f"sys.stdout.buffer.write({ending!r}); sys.stdout.flush(); sys.exit({exit_code})\n")
    source = MultiplexedPCMSource(helper(tmp_path, PREFIX, tail))
    start = source._start_process
    def start_and_stop(**kwargs):
        start(**kwargs)
        assert source.request_stop() is True
    source._start_process = start_and_stop
    with pytest.raises((ValueError, RuntimeError)):
        list(source.frames())
