import math

import numpy as np
import pytest
from scribeloop_core.capture.microphone_processing import MicrophoneProcessing, MicrophoneProcessor
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.storage.audio import read_audio
from test_recording import RecordingRunner, Source, wait_for
from test_recording import setup as recording_setup


def process(pcm, *, gain=0., mode='standard', chunk=171):
    processor = MicrophoneProcessor(MicrophoneProcessing(gain_db=gain, noise_filter=mode))
    result = []
    for offset in range(0, len(pcm), chunk):
        frame = AudioFrame(pcm[offset:offset+chunk], 'mic', round(offset/16), 0, offset)
        output = processor.process(frame)
        assert output.t_ms == frame.t_ms and output.sample_offset == offset
        result.append(output.pcm)
    return np.concatenate(result)


def rms(pcm):
    return np.sqrt(np.mean(pcm.astype(np.float64)**2))


def test_off_gain_and_limiter_do_not_change_length_or_other_track():
    pcm = np.full(16000, .1, dtype='float32')
    np.testing.assert_array_equal(process(pcm, mode='off'), pcm)
    np.testing.assert_allclose(process(pcm, gain=6., mode='off'), pcm*10**(.3), rtol=1e-6)
    limited = process(np.ones(16000, dtype='float32'), gain=24., mode='off')
    assert np.isfinite(limited).all() and np.max(limited) <= 1
    frame = AudioFrame(pcm, 'loopback', 0, 0)
    processor = MicrophoneProcessor(MicrophoneProcessing(gain_db=24., noise_filter='strong'))
    assert processor.process(frame) is frame


def test_quiet_noise_reduction_and_speech_band_preservation():
    rng = np.random.default_rng(17)
    noise = rng.normal(0, .0005, 32000).astype('float32')
    standard = process(noise)
    strong = process(noise, mode='strong')
    assert rms(standard[16000:])/rms(noise[16000:]) < .15
    assert rms(strong[16000:])/rms(noise[16000:]) < .05
    speech = (.1*np.sin(np.arange(32000)*2*math.pi*1000/16000)).astype('float32')
    filtered = process(speech)
    assert .95 < rms(filtered[16000:])/rms(speech[16000:]) < 1.01


def test_rumble_filter_and_packet_boundary_independence():
    pcm = (.15*np.sin(np.arange(32000)*2*math.pi*30/16000)).astype('float32')
    a = process(pcm, chunk=171)
    b = process(pcm, chunk=1024)
    np.testing.assert_array_equal(a, b)
    assert rms(a[16000:])/rms(pcm[16000:]) < .4


@pytest.mark.parametrize('patch', [{'gain_db':float('nan')}, {'gain_db':25.},
    {'gain_db':-13.}, {'noise_filter':'unknown'}, {'gain_db':True}, {'noise_threshold_db':-20.}])
def test_invalid_processing_settings_rejected(patch):
    with pytest.raises(ValueError): MicrophoneProcessing.model_validate(patch)


@pytest.fixture()
def recording(tmp_path):
    yield from recording_setup.__wrapped__(tmp_path)


@pytest.mark.asyncio
async def test_recording_keeps_encrypted_original_and_applies_only_to_mic(recording):
    import asyncio
    store, audio, events, publish = recording
    source = Source()
    runner = RecordingRunner(store, audio, lambda *a, **k:source, publish,
                              transcriber=lambda *a, **k:None)
    session = runner.start(None, {'scope':'application', 'microphone_processing':{
        'gain_db':6., 'noise_filter':'off'}}, 'Synthetic', consent_confirmed=True)
    await wait_for(lambda:any(e.get('session', {}).get('status') == 'recording' for e in events))
    runner.stop(session['id'])
    await asyncio.wait_for(runner.task, 3)
    sid = session['id']
    assert store.get(sid)['status'] == 'completed'
    path = audio.original_path(sid, 'mic').with_name('mic.raw.slpa')
    raw = np.concatenate([f.pcm for f in read_audio(path, sid, 'mic', audio._key(sid))])
    mic = np.concatenate([f.pcm for f in audio.frames(sid, 'mic')])
    loopback = np.concatenate([f.pcm for f in audio.frames(sid, 'loopback')])
    np.testing.assert_array_equal(raw, np.full(len(raw), .02, dtype='float32'))
    np.testing.assert_array_equal(loopback, np.full(len(loopback), .02, dtype='float32'))
    np.testing.assert_allclose(mic, raw*10**(.3), rtol=1e-6)
    assert store.get(sid)['capture']['microphone_processing']['gain_db'] == 6.
    audio.delete_session(sid)
    assert not path.exists()
