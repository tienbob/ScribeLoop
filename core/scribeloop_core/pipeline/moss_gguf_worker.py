"""Isolated local inference. PCM enters via stdin; no plaintext audio files."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main():
    runtime, model, language = sys.argv[1:]
    runtime = Path(runtime).resolve()
    sys.path.insert(0,str(runtime/'bindings/python/src'))
    os.environ['TRANSCRIBE_LIBRARY'] = str(runtime/'build/src'/(
        'libtranscribe.dylib' if sys.platform == 'darwin' else 'libtranscribe.so'))
    import numpy as np
    import transcribe_cpp

    raw = sys.stdin.buffer.read(305000*64+1)
    if not raw or len(raw) % 4 or len(raw) > 305000*64:
        raise ValueError('Invalid PCM length')
    pcm = np.frombuffer(raw,dtype='<f4').copy()
    with transcribe_cpp.Model(model,backend='auto') as loaded, loaded.session(n_threads=4) as session:
        result = session.run(pcm,language=None if language == 'auto' else language,
                             timestamps='segment',diarize='on')
    print(json.dumps({'language':result.language,'text':result.text,
        'segments':[{'start_ms':s.t0_ms,'end_ms':s.t1_ms,'speaker':s.speaker_id,
                     'text':s.text} for s in result.segments]}))


if __name__ == '__main__':
    main()
