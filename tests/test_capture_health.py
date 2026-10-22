import numpy as np
import pytest
from scribeloop_core.capture.health import CaptureSilenceMonitor
from scribeloop_core.capture.source import AudioFrame


def frame(t_ms, value=0, samples=1600, stream='loopback'):
    return AudioFrame(np.full(samples, value, dtype=np.float32), stream, t_ms, 0)


def test_silence_warns_once_recovers_and_can_warn_again():
    monitor = CaptureSilenceMonitor(silence_ms=200)
    assert monitor.feed(frame(0)) == []
    assert monitor.feed(frame(100))[0].kind == 'prolonged_silence'
    assert monitor.feed(frame(200)) == []
    assert monitor.feed(frame(300, .1))[0].kind == 'signal_recovered'
    assert monitor.feed(frame(400)) == []
    assert monitor.feed(frame(500))[0].kind == 'prolonged_silence'


def test_partial_frame_counts_samples_not_nominal_frame_duration():
    monitor = CaptureSilenceMonitor(silence_ms=150)
    assert monitor.feed(frame(0)) == []
    assert monitor.feed(frame(100, samples=400)) == []
    event = monitor.feed(frame(125, samples=400))[0]
    assert event.silent_audio_ms == 150
    assert event.t_ms == 150


def test_gaps_do_not_count_as_silence_or_recovery():
    monitor = CaptureSilenceMonitor(silence_ms=200)
    assert monitor.feed(frame(0)) == []
    assert monitor.feed(frame(1000)) == []
    assert monitor.feed(frame(1100))[0].kind == 'prolonged_silence'
    assert monitor.feed(frame(2000)) == []
    assert monitor.feed(frame(2100, .1))[0].kind == 'signal_recovered'


def test_low_noise_is_silence_and_sound_resets_unwarned_window():
    monitor = CaptureSilenceMonitor(silence_ms=200, threshold_dbfs=-60)
    assert monitor.feed(frame(0, .0001)) == []
    assert monitor.feed(frame(100, .1)) == []
    assert monitor.feed(frame(200, .0001)) == []
    assert monitor.feed(frame(300, .0001))[0].kind == 'prolonged_silence'


def test_invalid_frames_do_not_mutate_state():
    monitor = CaptureSilenceMonitor(silence_ms=200)
    assert monitor.feed(frame(0)) == []
    for invalid in [frame(100, stream='mic'), frame(100, float('nan')),
                    frame(100, samples=0), frame(0)]:
        with pytest.raises(ValueError):
            monitor.feed(invalid)
    assert monitor.feed(frame(100))[0].kind == 'prolonged_silence'


@pytest.mark.parametrize('kwargs', [{'silence_ms': 0}, {'silence_ms': float('nan')},
                                   {'threshold_dbfs': 1}, {'threshold_dbfs': float('inf')}])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        CaptureSilenceMonitor(**kwargs)
