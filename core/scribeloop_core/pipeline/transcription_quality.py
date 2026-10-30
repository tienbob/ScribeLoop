"""Validated recognition preferences and an authenticated raw-microphone view."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TranscriptionQuality(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    vocabulary: list[str] = Field(default_factory=list, max_length=50)
    asr_microphone_source: Literal['processed', 'raw'] = 'processed'
    final_transcription: bool = False

    @field_validator('vocabulary')
    @classmethod
    def terms(cls, values):
        result = []
        for term in values:
            term = term.strip()
            if not term or len(term) > 80 or any(ord(c) < 32 for c in term):
                raise ValueError('Use short vocabulary terms without control characters')
            if term not in result:
                result.append(term)
        # Conservative bound below the provider's 500-token limit, including Unicode.
        if sum(len(term.encode('utf-8')) for term in result) > 500:
            raise ValueError('Vocabulary is too long; use a shorter list of important terms')
        return result

    def deepgram_options(self):
        return {'keyterm': self.vocabulary} if self.vocabulary else {}


def recognition_audio(audio, options):
    return RawMicrophoneAudio(audio) if options.asr_microphone_source == 'raw' else audio


class RawMicrophoneAudio:
    """Switch only the authenticated mic file; preserve its mic AAD and timeline.

    Missing raw audio fails closed instead of silently uploading processed audio.
    The recording/playback store and the meeting-audio track remain unchanged.
    """
    def __init__(self, audio):
        self.audio = audio

    def __getattr__(self, name):
        return getattr(self.audio, name)

    def path(self, sid, stream):
        if stream != 'mic':
            return self.audio.path(sid, stream)
        return self.audio.original_path(sid, stream).with_name('mic.raw.slpa')

    def cursor(self, sid, stream, *, original=False):
        if stream != 'mic':
            return self.audio.cursor(sid, stream, original=original)
        from ..storage.audio_cursor import AudioCursor
        return AudioCursor(self.path(sid, stream), sid, stream, self.audio._key(sid))

    def frames(self, sid, stream):
        from ..storage.audio import read_audio
        return read_audio(self.path(sid,stream),sid,stream,self.audio._key(sid))

    def scan(self, sid, stream, *, original=False):
        with self.cursor(sid, stream, original=original) as cursor:
            while True:
                _, report = cursor.read(max_frames=64)
                if report['caught_up']:
                    return report
