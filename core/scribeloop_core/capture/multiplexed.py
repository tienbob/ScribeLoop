"""SLP2: independent streams with one native host-clock origin, no audio files."""
from __future__ import annotations

import struct
import time

import numpy as np

from .clock import SessionClock
from .health import CaptureSilenceMonitor
from .pcm_process import PCMProcessSource
from .source import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame

ORIGIN = struct.Struct('<4sQ')
PACKET = struct.Struct('<4sIB3sQ')
STREAMS = {0: 'loopback', 1: 'mic'}


class MultiplexedPCMSource(PCMProcessSource):
    stream = 'multi'

    def __init__(self, command, *, expected_streams=('loopback', 'mic'), timeout_s=10):
        if (not expected_streams or len(set(expected_streams)) != len(expected_streams)
                or not set(expected_streams) <= set(STREAMS.values())):
            raise ValueError('Select valid independent streams')
        super().__init__(command, timeout_s=timeout_s)
        self.expected_streams = tuple(expected_streams)
        self.clock: SessionClock | None = None
        self.monitors = {stream: CaptureSilenceMonitor(stream) for stream in self.expected_streams}

    def frames(self):
        import subprocess
        self._start_process(stdin=subprocess.PIPE)
        previous_end = {}
        seen = set()
        last_received = {stream: time.monotonic() for stream in self.expected_streams}
        try:
            prefix = self._read(ORIGIN.size, allow_eof=True)
            if self._closed:
                return
            if not prefix:
                try:
                    self._process.wait(timeout=self.timeout_s)
                except subprocess.TimeoutExpired:
                    raise TimeoutError('Native helper failed to start or exit') from None
                raise RuntimeError(f'Native capture could not start: {self._diagnostic()}')
            magic, origin = ORIGIN.unpack(prefix)
            if magic != b'SLC2' or origin > 2**63 - 1:
                raise ValueError('Invalid shared-clock origin')
            self.clock = SessionClock(origin)
            while not self._closed:
                deadline = min(last_received.values()) + self.timeout_s
                header = self._read(PACKET.size, allow_eof=True, deadline=deadline)
                if self._closed:
                    return
                if not header:
                    try:
                        status = self._process.wait(timeout=self.timeout_s)
                    except subprocess.TimeoutExpired:
                        raise TimeoutError('Native helper closed audio but did not exit') from None
                    raise RuntimeError(f'Native capture ended (exit {status}): {self._diagnostic()}')
                magic, count, tag, reserved, timestamp = PACKET.unpack(header)
                if magic == b'SLE2':
                    if (count != 0 or tag != 255 or reserved != b'\0\0\0' or timestamp != 0
                            or seen != set(self.expected_streams)):
                        raise ValueError('Invalid stream completion')
                    if self._read(1, allow_eof=True, deadline=deadline):
                        raise ValueError('Unexpected bytes after stream completion')
                    try:
                        status = self._process.wait(timeout=self.timeout_s)
                    except subprocess.TimeoutExpired:
                        raise TimeoutError('Native helper did not finish shutdown') from None
                    if status != 0:
                        raise RuntimeError('Native helper failed during shutdown')
                    return
                stream = STREAMS.get(tag)
                if (magic != b'SLP2' or reserved != b'\0\0\0'
                        or stream not in self.expected_streams or not 0 < count <= SAMPLE_RATE
                        or timestamp > 2**63 - 1):
                    raise ValueError('Invalid multiplexed audio packet')
                self.clock.milliseconds(timestamp)  # Reject frames before the shared origin.
                if stream in previous_end and timestamp < previous_end[stream] - 500_000:
                    raise ValueError('Capture timestamps overlap or move backwards')
                payload = self._read(count * 4, deadline=deadline)
                if self._closed:
                    return
                pcm = np.frombuffer(payload, dtype='<f4').astype(np.float32)
                if not np.isfinite(pcm).all():
                    raise ValueError('Nonfinite native audio samples')
                previous_end[stream] = timestamp + count * 1_000_000_000 // SAMPLE_RATE
                seen.add(stream)
                now = time.monotonic()
                if any(now - value > self.timeout_s for value in last_received.values()):
                    raise TimeoutError('A selected capture stream stalled')
                last_received[stream] = now
                for index in range(0, count, FRAME_SAMPLES):
                    yield AudioFrame(pcm[index:index + FRAME_SAMPLES], stream,
                        self.clock.milliseconds(timestamp + index * 1_000_000_000 // SAMPLE_RATE),
                        time.monotonic())
        except (OSError, ValueError):
            if not self._closed:
                raise
        finally:
            self.close()

    def request_stop(self):
        """Ask native capture to stop and drain; keep consuming until SLE2."""
        with self._lifecycle_lock:
            if self._closed or self._process is None or self._process.stdin.closed:
                return False
            try:
                self._process.stdin.write(b'STOP\n')
                self._process.stdin.flush()
                self._process.stdin.close()
                return True
            except OSError:
                return False

    def monitored_frames(self, on_health):
        try:
            for frame in self.frames():
                for event in self.monitors[frame.stream].feed(frame):
                    on_health(event)
                yield frame
        finally:
            self.close()
