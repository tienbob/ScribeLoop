"""Offline-only persistent community-1 worker; anonymous embeddings stay in RAM."""
from __future__ import annotations

import io
import json
import struct
import sys
from contextlib import redirect_stdout
from pathlib import Path


def main():
    import numpy as np
    import torch
    from pyannote.audio import Pipeline
    torch.set_num_threads(2)
    root = Path(__file__).resolve().parents[3]
    cache = root / 'models/pyannote-cache'
    # Resolve a locally cached revision. Offline env forbids downloads and login.
    ref = cache / 'models--pyannote--speaker-diarization-community-1/refs/main'
    if ref.exists():
        snapshot = ref.parent.parent / 'snapshots' / ref.read_text().strip()
    else:
        snapshots = sorted((cache / 'models--pyannote--speaker-diarization-community-1/snapshots').glob('*'))
        if len(snapshots) != 1:
            raise RuntimeError('Cache exactly one community-1 revision before use')
        snapshot = snapshots[0]
    with redirect_stdout(sys.stderr):
        pipeline = Pipeline.from_pretrained(snapshot, cache_dir=cache)
        pipeline.to(torch.device('cpu'))
    while True:
        header = sys.stdin.buffer.read(4)
        if not header:
            return
        if len(header) != 4:
            raise ValueError('Incomplete request')
        size = struct.unpack('<I',header)[0]
        if not 0 < size <= 4_000_000:
            raise ValueError('Invalid diarization payload size')
        payload = sys.stdin.buffer.read(size)
        if len(payload) != size:
            raise ValueError('Incomplete diarization payload')
        with np.load(io.BytesIO(payload),allow_pickle=False) as archive:
            pcm = np.array(archive['loopback'],dtype=np.float32)
        if pcm.ndim != 1 or not 0 < pcm.size <= 35*16000 or not np.isfinite(pcm).all():
            raise ValueError('Invalid diarization PCM')
        with redirect_stdout(sys.stderr):
            result = pipeline({'waveform':torch.from_numpy(pcm).unsqueeze(0),'sample_rate':16000})
        annotation = result.speaker_diarization
        labels = annotation.labels()
        turns = [{'start_ms':max(0,round(turn.start*1000)),
                  'end_ms':min(round(pcm.size/16),round(turn.end*1000)),
                  'local_speaker':str(label)} for turn, _, label in annotation.itertracks(yield_label=True)]
        embeddings = {str(label):result.speaker_embeddings[i].tolist()
                      for i,label in enumerate(labels)} if result.speaker_embeddings is not None else {}
        print(json.dumps({'turns':turns,'embeddings':embeddings},allow_nan=False),flush=True)


if __name__ == '__main__':
    main()
