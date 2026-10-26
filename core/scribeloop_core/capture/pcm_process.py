"""Fail-closed SLP1 pipe transport for native audio helpers (POSIX).

Read on a consumer thread, not a realtime callback. stdout carries only framed
PCM. The child is terminated on timeout, protocol errors, or source closure.
"""
from __future__ import annotations

import math
import os
import select
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .health import CaptureSilenceMonitor
from .source import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame

HEADER = struct.Struct('<4sId')


class PCMProcessSource:
    stream = 'loopback'

    def __init__(self, command: list[str | Path], *, timeout_s: float = 10,
                 silence_ms: float = 5000):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('timeout_s must be positive and finite')
        self.command = [str(arg) for arg in command]
        self.timeout_s = timeout_s
        self.monitor = CaptureSilenceMonitor(self.stream, silence_ms=silence_ms)
        self._process: subprocess.Popen | None = None
        self._stderr = None
        self._closed = False
        self._started = False
        self._lifecycle_lock = threading.RLock()

    def _start_process(self, *, stdin):
        # Closing must not miss a child that Popen has created but not returned.
        with self._lifecycle_lock:
            if self._started or self._closed:
                raise RuntimeError('Capture source is single-use')
            self._started = True
            self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 - owned by close()
            try:
                self._process = subprocess.Popen(self.command, stdout=subprocess.PIPE,
                    stderr=self._stderr, stdin=stdin, bufsize=0)
            except BaseException:
                self.close()
                raise

    def _read(self, count: int, *, allow_eof: bool = False, deadline: float | None = None) -> bytes:
        deadline = min(deadline if deadline is not None else float('inf'),
                       time.monotonic() + self.timeout_s)
        data = bytearray()
        while len(data) < count and not self._closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Native capture stream stalled: no complete audio packet received')
            ready, _, _ = select.select([self._process.stdout], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self._process.stdout.fileno(), count - len(data))
            if self._closed:
                return b''
            if not chunk:
                if allow_eof and not data:
                    return b''
                raise RuntimeError('Truncated native audio packet: '+self._diagnostic())
            data.extend(chunk)
        return bytes(data)

    def _diagnostic(self) -> str:
        with self._lifecycle_lock:
            if self._stderr is None:
                return ''
            self._stderr.seek(0, os.SEEK_END)
            size = self._stderr.tell()
            self._stderr.seek(max(0, size - 2048))
            return self._stderr.read().decode('utf-8', errors='replace').strip()

    def frames(self) -> Iterator[AudioFrame]:
        self._start_process(stdin=subprocess.DEVNULL)
        origin = None
        previous_end = None
        try:
            while not self._closed:
                header = self._read(HEADER.size, allow_eof=True)
                if self._closed:
                    return
                if not header:
                    try:
                        status = self._process.wait(timeout=self.timeout_s)
                    except subprocess.TimeoutExpired as exc:
                        raise TimeoutError('Native helper closed audio but did not exit') from exc
                    raise RuntimeError(f'Native capture ended (exit {status}): {self._diagnostic()}')
                magic, count, pts = HEADER.unpack(header)
                if magic != b'SLP1' or not 0 < count <= SAMPLE_RATE or not math.isfinite(pts):
                    raise ValueError('Invalid native audio packet header')
                if previous_end is not None and pts < previous_end - .0005:
                    raise ValueError('Native capture timestamps overlap or move backwards')
                payload = self._read(count * 4)
                if self._closed:
                    return
                pcm = np.frombuffer(payload, dtype='<f4').astype(np.float32)
                if not np.isfinite(pcm).all():
                    raise ValueError('Nonfinite native audio samples')
                if origin is None:
                    origin = pts
                previous_end = pts + count / SAMPLE_RATE
                for i in range(0, count, FRAME_SAMPLES):
                    yield AudioFrame(pcm[i:i+FRAME_SAMPLES], self.stream,
                        round((pts - origin + i / SAMPLE_RATE) * 1000), time.monotonic())
        except (OSError, ValueError):
            if not self._closed:
                raise
        finally:
            self.close()

    def monitored_frames(self, on_health) -> Iterator[AudioFrame]:
        """Deliver health events without dropping or modifying recorder audio."""
        try:
            for frame in self.frames():
                for event in self.monitor.feed(frame):
                    on_health(event)
                yield frame
        finally:
            self.close()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._process is not None and self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=2)
            finally:
                if self._process is not None:
                    self._process.stdout.close()
                    if self._process.stdin:
                        self._process.stdin.close()
                if self._stderr is not None:
                    self._stderr.close()
                    self._stderr = None
