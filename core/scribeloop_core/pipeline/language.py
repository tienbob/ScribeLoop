"""Detect a meeting language once from bounded early speech, never per chunk."""
from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
SUPPORTED = {'vi', 'en'}


class LanguageDetectionError(ValueError):
    pass


def early_speech(tracks, speech_timestamps):
    """At most six voiced seconds per track, within its opening 30 seconds."""
    excerpts = []
    for pcm in tracks.values():
        opening = pcm[:30 * SAMPLE_RATE]
        remaining = 6 * SAMPLE_RATE
        for span in speech_timestamps(opening):
            start = max(0, int(span['start']))
            end = min(len(opening), int(span['end']), start + remaining)
            if end > start:
                excerpts.append(opening[start:end])
                remaining -= end - start
            if remaining == 0:
                break
    if sum(len(part) for part in excerpts) < 2 * SAMPLE_RATE:
        raise LanguageDetectionError('Not enough early speech; select a language manually')
    return np.concatenate(excerpts).astype(np.float32, copy=False)


def identify_language(tracks, *, detector=None, speech_timestamps=None):
    if speech_timestamps is None:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        def speech_timestamps(pcm):
            return get_speech_timestamps(pcm, VadOptions(min_speech_duration_ms=250,
                min_silence_duration_ms=200, speech_pad_ms=30), sampling_rate=SAMPLE_RATE)
    sample = early_speech(tracks, speech_timestamps)
    if detector is None:
        from faster_whisper import WhisperModel
        model_path = Path(__file__).resolve().parents[3] / 'models/faster-whisper-small'
        try:
            detector = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=2,
                                    local_files_only=True)
        except (OSError, RuntimeError, ValueError):
            raise LanguageDetectionError('Local language detector unavailable; select a language manually') from None
    language, probability, probabilities = detector.detect_language(sample, vad_filter=False,
        language_detection_segments=1)
    runner_up = max((float(p) for code,p in probabilities if code != language), default=0)
    if (language not in SUPPORTED or not np.isfinite(probability) or probability < .7
            or probability - runner_up < .2):
        raise LanguageDetectionError('Early language evidence is uncertain; select a language manually')
    return {'language':language, 'confidence':float(probability), 'mode':'auto',
            'sample_s':sample.size / SAMPLE_RATE}
