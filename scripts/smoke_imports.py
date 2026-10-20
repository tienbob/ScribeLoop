"""Smoke check: import the full dependency stack (dev machine, no audio hardware)."""
import pyannote.audio
import sherpa_onnx
import faster_whisper
import silero_vad
import sqlite_vec
import numpy
import soundfile
import openai
import fastapi

from scribeloop_core.pipeline.vad import SileroVAD  # noqa: F401

print(
    f"SMOKE-OK pyannote={pyannote.audio.__version__} sherpa={sherpa_onnx.__version__} "
    f"faster_whisper={faster_whisper.__version__} numpy={numpy.__version__}"
)
