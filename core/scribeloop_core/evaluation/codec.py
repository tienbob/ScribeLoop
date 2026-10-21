"""Timing checks for codec round trips before reusing transcript/speaker labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf


def check_alignment(reference: np.ndarray, decoded: np.ndarray, sample_rate: int) -> dict:
    """Check exact sample count and 10 ms energy-envelope correlation at three positions.

    This verifies waveform timing, not human annotation quality or codec realism.
    Silent windows are excluded because they cannot establish alignment.
    """
    if (reference.ndim != 1 or decoded.ndim != 1 or sample_rate <= 0
            or not len(reference) or len(reference) != len(decoded)
            or not np.isfinite(reference).all() or not np.isfinite(decoded).all()):
        return {"passed": False, "reason": "expected nonempty mono arrays of equal length"}
    window = min(len(reference), sample_rate * 5)
    block = max(1, sample_rate // 100)
    usable = window // block * block
    if usable < block * 2:
        return {"passed": False, "reason": "too short to establish envelope alignment"}
    envelope_length = usable // block
    max_lag = min(10, envelope_length - 1)
    lags = np.arange(-max_lag, max_lag + 1)
    n_fft = 1 << (2 * envelope_length - 1).bit_length()
    measurements = []
    for start in sorted({0, (len(reference) - window) // 2, len(reference) - window}):
        a = reference[start:start + window].astype(np.float64)
        b = decoded[start:start + window].astype(np.float64)
        if np.sqrt(np.mean(a * a)) < 1e-4:
            continue
        # Codec filtering changes waveform phase. Energy envelopes assess speech
        # timing without interpreting those phase changes as a recording delay.
        a = np.sqrt(np.mean(a[:usable].reshape(-1, block) ** 2, axis=1))
        b = np.sqrt(np.mean(b[:usable].reshape(-1, block) ** 2, axis=1))
        a -= a.mean()
        b -= b.mean()
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            measurements.append({"start_sample": start, "lag_samples": None, "correlation": 0.0})
            continue
        correlation = np.fft.irfft(np.fft.rfft(a, n_fft) *
                                   np.conj(np.fft.rfft(b, n_fft)), n_fft)[lags % n_fft] / norm
        best = int(np.argmax(correlation))
        measurements.append({"start_sample": start, "lag_samples": int(lags[best]) * block,
                             "correlation": float(correlation[best])})
    passed = bool(measurements) and all(
        item["lag_samples"] is not None and item["lag_samples"] == 0
        and item["correlation"] >= 0.9 for item in measurements
    )
    return {"passed": passed, "samples": len(reference), "sample_rate": sample_rate,
            "envelope_frame_ms": block * 1000 / sample_rate,
            "maximum_lag_frames": 0, "minimum_correlation": 0.9, "windows": measurements,
            "reason": "checked" if measurements else "no non-silent window to establish alignment"}


def load_aligned_variant(
    source_wav: str | Path, decoded_wav: str | Path, provenance_path: str | Path
) -> tuple[np.ndarray, dict]:
    """Load a codec copy only when timing evidence and both file hashes match."""
    source_wav, decoded_wav, provenance_path = map(Path, (source_wav, decoded_wav, provenance_path))
    metadata = json.loads(provenance_path.read_text())
    if not isinstance(metadata, dict):
        raise TypeError("codec provenance must be an object")
    alignment = metadata.get("alignment")
    if not isinstance(alignment, dict) or alignment.get("passed") is not True:
        raise ValueError("codec timing checks did not pass")
    if (metadata.get("source_sha256") != hashlib.sha256(source_wav.read_bytes()).hexdigest()
            or metadata.get("decoded_sha256") != hashlib.sha256(decoded_wav.read_bytes()).hexdigest()):
        raise ValueError("codec provenance hashes do not match the selected audio")
    source_info = sf.info(source_wav)
    waveform, sr = sf.read(decoded_wav, dtype="float32")
    if (waveform.ndim != 1 or source_info.channels != 1 or sr != source_info.samplerate
            or len(waveform) != source_info.frames):
        raise ValueError("codec audio must preserve sample rate, channels, and sample count")
    return waveform, metadata
