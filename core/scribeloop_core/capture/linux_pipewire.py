"""Linux pull-based GStreamer capture via PulseAudio or PipeWire's Pulse server.

No stream is opened until frames() is iterated. A single pipeline clock aligns
microphone and monitor tracks. Per-application routing is deliberately rejected.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Iterator
from itertools import pairwise

import numpy as np

from .devices import CaptureDevices, MicrophoneDevice
from .source import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame


def pulse_query(*args):
    result = subprocess.run(['pactl', '--format=json', *args], capture_output=True,
                            check=True, timeout=3)
    if len(result.stdout) > 1_048_576:
        raise ValueError('Oversized audio device response')
    return json.loads(result.stdout)


def linux_capture_devices():
    """Enumerate microphones without opening an audio stream."""
    try:
        info = pulse_query('info')
        rows = pulse_query('list', 'sources')
        microphones = tuple(MicrophoneDevice(id=row['name'],
            name=row.get('description') or row['name'],
            is_default=row['name'] == info['default_source_name'])
            for row in rows if row.get('monitor_of_sink') in (None, 4294967295)
            and not row['name'].endswith('.monitor'))
        return CaptureDevices(available=True, permission='authorized', microphones=microphones)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return CaptureDevices(available=False, permission='not_checked')


def resolve_devices(selection):
    info = pulse_query('info')
    sources = pulse_query('list', 'sources')
    mic = (info['default_source_name'] if selection.microphone_device_id == 'default'
           else selection.microphone_device_id)
    microphones = {row['name'] for row in sources
                   if row.get('monitor_of_sink') in (None, 4294967295)
                   and not row['name'].endswith('.monitor')}
    if mic not in microphones:
        raise ValueError('Selected microphone is unavailable')
    devices = [('mic', mic)]
    if selection.scope == 'system':
        sinks = pulse_query('list', 'sinks')
        sink = next((row for row in sinks if row['name'] == info['default_sink_name']), None)
        if sink is None or not sink.get('monitor_source'):
            raise ValueError('Default output monitor is unavailable')
        devices.append(('loopback', sink['monitor_source']))
    return devices


class LinuxMeetingAudioSource:
    stream = 'multi'

    def __init__(self, selection, *, consent_confirmed, timeout_s=10):
        from ..meetings.contracts import CaptureSelection
        if consent_confirmed is not True:
            raise ValueError('Explicit recording consent is required')
        if not isinstance(selection, CaptureSelection):
            raise TypeError('A validated capture selection is required')
        if selection.scope == 'application':
            raise ValueError('Linux preview supports microphone or system audio only')
        self.selection = selection
        self.timeout_s = timeout_s
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._pipeline = None
        self._started = False

    def frames(self) -> Iterator[AudioFrame]:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        with self._lock:
            if self._started or self._closed.is_set():
                raise RuntimeError('Capture source is single-use')
            self._started = True
        pipeline = Gst.Pipeline.new('scribeloop-capture')
        tracks = []
        try:
            for stream, device in resolve_devices(self.selection):
                elements = [Gst.ElementFactory.make(name) for name in
                            ('pulsesrc', 'audioconvert', 'audioresample', 'capsfilter', 'appsink')]
                if any(element is None for element in elements):
                    raise RuntimeError('Install GStreamer base and good plugins')
                source, _, _, caps, sink = elements
                source.set_property('device', device)
                caps.set_property('caps', Gst.Caps.from_string(
                    'audio/x-raw,format=F32LE,rate=16000,channels=1,layout=interleaved'))
                sink.set_property('max-buffers', 8)
                sink.set_property('drop', False)
                sink.set_property('sync', False)
                sink.set_property('wait-on-eos', False)
                for element in elements:
                    pipeline.add(element)
                for left, right in pairwise(elements):
                    if not left.link(right):
                        raise RuntimeError('Could not link Linux audio pipeline')
                tracks.append((stream, sink))
            with self._lock:
                if self._closed.is_set():
                    return
                self._pipeline = pipeline
                if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError('Could not start Linux audio capture')
            last_received = dict.fromkeys((track[0] for track in tracks), time.monotonic())
            previous_end = {}
            while not self._closed.is_set():
                message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message is not None:
                    raise RuntimeError('Audio capture ended; check selected devices and sound server')
                for stream, sink in tracks:
                    sample = sink.emit('try-pull-sample', 50 * Gst.MSECOND)
                    if self._closed.is_set():
                        return
                    if sample is None:
                        if time.monotonic() - last_received[stream] > self.timeout_s:
                            raise TimeoutError('Linux audio capture stalled')
                        continue
                    last_received[stream] = time.monotonic()
                    buffer = sample.get_buffer()
                    if buffer.pts == Gst.CLOCK_TIME_NONE:
                        raise ValueError('Audio capture has no timestamp')
                    ok, mapped = buffer.map(Gst.MapFlags.READ)
                    if not ok:
                        raise RuntimeError('Could not read audio buffer')
                    try:
                        pcm = np.frombuffer(mapped.data, dtype='<f4').astype(np.float32, copy=True)
                    finally:
                        buffer.unmap(mapped)
                    if not pcm.size or not np.isfinite(pcm).all():
                        raise ValueError('Invalid captured audio')
                    offset = round(buffer.pts * SAMPLE_RATE / Gst.SECOND)
                    end = previous_end.get(stream)
                    if end is not None and abs(offset - end) <= 16:
                        offset = end
                    if end is not None and offset < end:
                        raise ValueError('Linux audio timestamps overlap')
                    previous_end[stream] = offset + pcm.size
                    for start in range(0, pcm.size, FRAME_SAMPLES):
                        position = offset + start
                        yield AudioFrame(pcm[start:start + FRAME_SAMPLES], stream,
                            round(position * 1000 / SAMPLE_RATE), time.monotonic(), position)
        finally:
            pipeline.set_state(Gst.State.NULL)
            self.close()

    def close(self):
        self._closed.set()
        with self._lock:
            if self._pipeline is not None:
                from gi.repository import Gst
                self._pipeline.set_state(Gst.State.NULL)
                self._pipeline = None


class PipeWireSource(LinuxMeetingAudioSource):
    """Compatibility name; requires an explicit consented capture selection."""
