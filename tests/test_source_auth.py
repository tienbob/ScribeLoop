"""Capture source tests (source.py) — WAV replay + auth helpers."""
import numpy as np
import soundfile as sf

from scribeloop_core.api.auth import check_first_message, host_allowed, new_token
from scribeloop_core.capture.source import (
    FRAME_MS,
    SAMPLE_RATE,
    SilenceSource,
    WavFileSource,
)


def _write_wav(path, seconds=1.0):
    n = int(seconds * SAMPLE_RATE)
    data = 0.1 * np.sin(np.linspace(0, 440 * 2 * np.pi, n)).astype(np.float32)
    sf.write(path, data, SAMPLE_RATE)
    return n


def test_wav_source_frames(tmp_path):
    wav = tmp_path / "tone.wav"
    n = _write_wav(wav, seconds=1.0)
    src = WavFileSource(wav)
    frames = list(src.frames())
    assert frames, "no frames emitted"
    assert all(f.stream == "loopback" for f in frames)
    assert frames[0].t_ms == 0
    assert frames[-1].t_ms == (len(frames) - 1) * FRAME_MS
    assert sum(len(f.pcm) for f in frames) == n


def test_wav_source_missing_file(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        WavFileSource(tmp_path / "nope.wav")


def test_silence_source_bounded():
    src = SilenceSource(max_ms=1_000)
    frames = list(src.frames())
    assert len(frames) == 1_000 // FRAME_MS
    assert all(np.all(f.pcm == 0) for f in frames)


def test_auth_helpers():
    token = new_token()
    assert len(token) >= 32
    assert check_first_message(token, token)
    assert check_first_message(f"  {token} \n", token)
    assert not check_first_message("wrong", token)
    assert host_allowed("127.0.0.1") and host_allowed("localhost:8757")
    assert not host_allowed("evil.example.com")
    assert not host_allowed("")
