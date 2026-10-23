"""Streaming adapter contract tests without model files or ML inference."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.asr import SherpaOnnxStreaming


class FakeStream:
    def __init__(self):
        self.finished = False
        self.accepted = 0

    def accept_waveform(self, sample_rate, samples):
        self.accepted += len(samples)

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    def __init__(self):
        self.streams = []
        self.resets = []

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream):
        return False

    def get_result(self, stream):
        return "tail" if stream.finished else "hello"

    def is_endpoint(self, stream):
        return True

    def reset(self, stream):
        self.resets.append(stream)


@pytest.fixture()
def model(tmp_path, monkeypatch):
    for part in ("encoder", "decoder", "joiner"):
        # Create quantized files first to expose the old glob-order bug.
        (tmp_path / f"{part}-test.int8.onnx").touch()
        (tmp_path / f"{part}-test.onnx").touch()
    recognizer = FakeRecognizer()
    config = {}

    def factory(**kwargs):
        config.update(kwargs)
        return recognizer

    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        OnlineRecognizer=SimpleNamespace(from_transducer=factory)
    ))
    return tmp_path, recognizer, config


@pytest.mark.parametrize("int8", [False, True])
def test_explicit_precision_and_endpoint_configuration(model, int8):
    path, _, config = model
    SherpaOnnxStreaming(path, int8=int8)
    assert config["enable_endpoint_detection"] is True
    for part in ("encoder", "decoder", "joiner"):
        assert config[part].endswith(".int8.onnx") == int8


def test_endpoint_preserves_buffered_stream_and_flushes_tail(model):
    path, recognizer, _ = model
    backend = SherpaOnnxStreaming(path)
    frame = AudioFrame(np.zeros(1600, dtype=np.float32), "loopback", 0, 0.0)
    result = backend.feed(frame)
    assert result[0]["is_final"] and result[0]["text"] == "hello"
    stream = recognizer.streams[0]
    assert backend._stream is stream
    assert recognizer.resets == [stream]
    result = backend.flush()
    assert result[0]["text"] == "tail"
    assert stream.finished
    assert backend.flush() == []
    assert len(recognizer.streams) == 1


def test_ambiguous_model_files_fail_instead_of_silently_selecting(model):
    path, _, _ = model
    (path / "encoder-other.onnx").touch()
    with pytest.raises(ValueError, match="ambiguous"):
        SherpaOnnxStreaming(path)


def test_whisper_replay_uses_cached_model_and_bounds_decoder_padding(monkeypatch):
    from scribeloop_core.pipeline.asr import ChunkedFasterWhisper

    config = {}

    class Model:
        def __init__(self, name, **kwargs):
            config.update(kwargs)

        def transcribe(self, audio, **kwargs):
            return [SimpleNamespace(text='valid', start=-.1, end=1.5, avg_logprob=-.2),
                    SimpleNamespace(text='past audio', start=1.1, end=2, avg_logprob=-.2)], \
                   SimpleNamespace(language='en')

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    backend = ChunkedFasterWhisper(chunk_s=1, local_files_only=True)
    assert config['local_files_only'] is True
    assert config['cpu_threads'] == 2
    frame = AudioFrame(np.zeros(16000, dtype=np.float32), 'loopback', 0, 0)
    result = backend.feed(frame)
    assert len(result) == 1
    assert result[0]['start_ms'] == 0 and result[0]['end_ms'] == 1000
    result = backend.feed(frame)
    assert result[0]['start_ms'] == 1000 and result[0]['end_ms'] == 2000
