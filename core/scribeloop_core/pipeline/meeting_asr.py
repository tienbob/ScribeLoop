"""Local MOSS adapter with encrypted input and a cancellable isolated process."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .replay import ReplayChunk

ROOT = Path(__file__).resolve().parents[3]


def recording_pcm(audio, session_id):
    """Authenticate complete containers, retain timestamps/gaps, bound memory."""
    tracks = {}
    for stream in ('loopback','mic'):
        frames = []
        count = 0
        for frame in audio.frames(session_id, stream):
            count += frame.pcm.size
            if count > 305 * 16000 or frame.t_ms < 0 or frame.t_ms > 305000:
                raise ValueError('Recording exceeds development bounds')
            frames.append(frame)  # Footer checked before use.
        if not frames:
            raise ValueError('Recording has an empty selected stream')
        end = max(round(frame.t_ms * 16) + frame.pcm.size for frame in frames)
        if not 0 < end <= 305 * 16000:
            raise ValueError('Recording exceeds five-minute development limit')
        pcm = np.zeros(end, dtype=np.float32)
        for frame in frames:
            start = round(frame.t_ms * 16)
            pcm[start:start + frame.pcm.size] = frame.pcm
        tracks[stream] = pcm
    return tracks


def transcribe_moss(audio, session_id, emit, cancelled, *, language='auto', locked_language=None):
    if language not in {'vi','en','auto'}:
        raise ValueError('Unsupported language hint')
    tracks = recording_pcm(audio, session_id)
    if cancelled.is_set():
        return
    from .language import identify_language
    if language == 'auto':
        lock = locked_language or identify_language(tracks)
        if lock.get('language') not in {'vi','en'} or lock.get('mode') != 'auto':
            raise ValueError('Invalid session language lock')
    else:
        lock = {'language':language, 'confidence':None, 'mode':'manual', 'sample_s':0}
    language = lock['language']
    if cancelled.is_set():
        return
    emit({'type':'language_locked', **lock})
    payload = io.BytesIO()
    np.savez(payload, language=np.array(language), **tracks)
    env = os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join(str(ROOT / item) for item in (
        'data/moss-runtime','data/moss-source','core')), HF_HOME=str(ROOT / 'data/moss-hf'),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    process = subprocess.Popen([sys.executable, '-m', 'scribeloop_core.pipeline.moss_worker'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        cwd=ROOT)
    try:
        result = []
        errors = []
        def communicate():
            try:
                result.append(process.communicate(input=payload.getvalue()))
            except Exception as exc:  # noqa: BLE001 - transfer I/O failure back to owner thread
                errors.append(exc)
        transfer = threading.Thread(target=communicate, daemon=True)
        transfer.start()
        deadline = time.monotonic() + 600
        while transfer.is_alive():
            transfer.join(timeout=.2)
            if cancelled.is_set():
                return
            if time.monotonic() > deadline:
                raise TimeoutError('Local MOSS inference timed out')
        if errors:
            raise errors[0]
        output, _ = result[0]
        if process.returncode:
            raise RuntimeError('Local MOSS inference failed; no fallback model was used')
        if len(output) > 4_000_000:
            raise ValueError('MOSS response exceeds limit')
        result = json.loads(output)
        if result.get('metrics'):
            emit({'type':'model_metrics','stage':'final','metrics':result['metrics']})
        for row in result['segments']:
            stream = row['stream']
            if stream not in tracks:
                raise ValueError('Invalid ASR stream')
            chunk = ReplayChunk.model_validate(row).model_dump()
            if chunk['end_ms'] > round(tracks[stream].size / 16):
                raise ValueError('ASR timestamps exceed recorded audio')
            if cancelled.is_set():
                return
            emit({**chunk, 'stream':stream})
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if 'transfer' in locals():
            transfer.join(timeout=3)
        for pipe in (process.stdin, process.stdout):
            pipe.close()
