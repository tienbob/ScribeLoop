"""Reject timing shifts before codec copies reuse source reference labels."""
from __future__ import annotations

import numpy as np
import pytest
from scribeloop_core.evaluation.codec import check_alignment


def signal():
    return np.random.default_rng(42).normal(0, 0.1, 32000).astype(np.float32)


def test_gain_and_small_codec_noise_preserve_alignment():
    source = signal()
    decoded = source * 0.7 + np.random.default_rng(7).normal(0, 0.001, len(source))
    result = check_alignment(source, decoded, 16000)
    assert result["passed"]
    assert all(window["lag_samples"] == 0 for window in result["windows"])


@pytest.mark.parametrize("shift", [-320, 320])
def test_codec_delay_is_rejected(shift):
    source = signal()
    decoded = np.roll(source, shift)
    result = check_alignment(source, decoded, 16000)
    assert not result["passed"]
    assert abs(result["windows"][0]["lag_samples"]) == abs(shift)


def test_truncation_silence_and_unrelated_audio_cannot_validate_timing():
    source = signal()
    assert not check_alignment(source, source[:-1], 16000)["passed"]
    assert not check_alignment(np.zeros(32000), np.zeros(32000), 16000)["passed"]
    assert not check_alignment(source, np.random.default_rng(1).normal(size=len(source)), 16000)["passed"]
    assert not check_alignment(source, np.zeros_like(source), 16000)["passed"]


def test_variant_hashes_bind_timing_evidence_to_the_exact_audio(tmp_path):
    import hashlib
    import json

    import soundfile as sf
    from scribeloop_core.evaluation.codec import load_aligned_variant

    source, decoded, provenance = (tmp_path / name for name in ("source.wav", "copy.wav", "copy.json"))
    sf.write(source, signal(), 16000)
    sf.write(decoded, signal() * 0.8, 16000)
    metadata = {"alignment": {"passed": True},
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "decoded_sha256": hashlib.sha256(decoded.read_bytes()).hexdigest()}
    provenance.write_text(json.dumps(metadata))
    waveform, _ = load_aligned_variant(source, decoded, provenance)
    assert len(waveform) == 32000
    sf.write(source, signal() * 0.5, 16000)
    with pytest.raises(ValueError, match="hashes"):
        load_aligned_variant(source, decoded, provenance)


def test_failed_timing_and_mismatched_lengths_cannot_reuse_labels(tmp_path):
    import hashlib
    import json

    import soundfile as sf
    from scribeloop_core.evaluation.codec import load_aligned_variant

    source, decoded, provenance = (tmp_path / name for name in ("source.wav", "copy.wav", "copy.json"))
    sf.write(source, signal(), 16000)
    sf.write(decoded, signal()[:-160], 16000)
    metadata = {"alignment": {"passed": False},
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "decoded_sha256": hashlib.sha256(decoded.read_bytes()).hexdigest()}
    provenance.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="timing"):
        load_aligned_variant(source, decoded, provenance)
    metadata["alignment"]["passed"] = True
    provenance.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="sample count"):
        load_aligned_variant(source, decoded, provenance)
