"""Waveform fidelity across fractional-ms native packet boundaries; no audio output."""
import threading

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.cloud_meeting import pcm_window
from scribeloop_core.pipeline.deepgram_batch import _blocks
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.playback import PlaybackIndex
from test_recording import Keys


@pytest.mark.parametrize('chunk', [170, 341, 371])
def test_fractional_packet_audio_is_sample_exact_across_seeks_and_upload(tmp_path, chunk):
    audio = SessionAudioStore(tmp_path/'audio', Keys())
    sid = 'a'*32
    audio.create_session(sid)
    # Multiple sparse-index checkpoints, waveform phase and real 50 ms gap.
    before = np.sin(np.arange(32000)*2*np.pi*997/16000).astype('float32')*.3
    after = np.sin(np.arange(16000)*2*np.pi*2201/16000).astype('float32')*.2
    expected = np.concatenate([np.zeros(1600, dtype='float32'), before,
                               np.zeros(800, dtype='float32'), after])
    with audio.writer(sid, 'mic') as writer:
        for start, samples in [(1600, before), (1600+len(before)+800, after)]:
            for offset in range(0, len(samples), chunk):
                writer.write(AudioFrame(samples[offset:offset+chunk], 'mic',
                    round((start+offset)/16), 0))
    index = PlaybackIndex(audio)
    assert index.index(sid, 'mic')['duration_samples'] == len(expected)
    restored = index.window(sid, 'mic', 0, 5000)
    np.testing.assert_array_equal(restored, expected)
    np.testing.assert_array_equal(audio.window(sid, 'mic', 0, 5000), expected)
    for start_ms in [101, 787, 1499, 2101, 2150, 2900]:
        np.testing.assert_array_equal(index.window(sid, 'mic', start_ms, 5000),
                                      expected[start_ms*16:])
    frames = list(audio.frames(sid, 'mic'))
    np.testing.assert_array_equal(pcm_window(frames, 0, len(expected)/16), expected)
    with audio.cursor(sid, 'mic') as cursor:
        upload = np.concatenate(list(_blocks(cursor, len(expected), threading.Event())))
    np.testing.assert_array_equal(upload, (expected*32767).astype('<i2'))
    from scribeloop_core.storage.audio_quality import inspect_track
    report = inspect_track(audio, sid, 'mic')
    assert report['rounding_gap_samples'] > 0
    assert report['rounding_overwritten_samples'] > 0
    assert report['clipped_percent'] == 0
    assert report['samples'] == len(before)+len(after)
