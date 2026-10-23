"""Bounded cancellable RPC to cached local models, no plaintext files."""
from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]


class LocalWorker:
    def __init__(self, module='moss_worker', *, moss=True, timeout_s=120):
        self.module, self.moss, self.timeout_s = module, moss, timeout_s
        self.process = None

    def _start(self):
        env = os.environ.copy()
        paths = ('data/moss-runtime','data/moss-source','core') if self.moss else ('core',)
        env.update(PYTHONPATH=os.pathsep.join(str(ROOT / item) for item in paths),
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0')
        if self.moss:
            env['HF_HOME'] = str(ROOT / 'data/moss-hf')
        self.process = subprocess.Popen([sys.executable, '-m',
            'scribeloop_core.pipeline.' + self.module, '--stream'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=ROOT)

    def warm(self):
        if self.process is None:
            self._start()

    def infer(self, tracks, language, cancelled):
        if cancelled.is_set():
            raise InterruptedError('Inference cancelled')
        if self.process is None:
            self._start()
        payload = io.BytesIO()
        np.savez(payload, language=np.array(language), **tracks)
        data = payload.getvalue()
        if len(data) > 40_000_000:
            raise ValueError('Inference payload exceeds limit')
        output, errors = [], []
        process = self.process
        def transfer():
            try:
                process.stdin.write(struct.pack('<I', len(data)) + data)
                process.stdin.flush()
                reply = process.stdout.readline(4_000_001)
                if not reply.endswith(b'\n') or len(reply) > 4_000_000:
                    raise ValueError('Invalid or oversized local model response')
                output.append(json.loads(reply))
            except Exception as exc:  # noqa: BLE001 - propagate across transfer thread
                errors.append(exc)
        worker = threading.Thread(target=transfer, daemon=True)
        worker.start()
        deadline = time.monotonic() + self.timeout_s
        try:
            while worker.is_alive():
                worker.join(.05)
                if cancelled.is_set():
                    raise InterruptedError('Inference cancelled')
                if time.monotonic() > deadline:
                    raise TimeoutError('Local model timed out')
            if errors:
                raise errors[0]
            return output[0]
        except BaseException:
            self.close()
            raise
        finally:
            worker.join(timeout=3)

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for pipe in (process.stdin, process.stdout):
            pipe.close()
