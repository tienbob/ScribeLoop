"""Isolated local MOSS worker: bounded PCM over stdin; transcript JSON over stdout.

Run with the pinned MOSS dependency environment. Never writes input audio or
transcript files. Keystore access stays in the parent; only PCM crosses the pipe.
"""
from __future__ import annotations

import copy
import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path


def main():
    import resource

    import numpy as np
    import torch
    from moss_transcribe_diarize import parse_transcript
    from moss_transcribe_diarize.inference_utils import DEFAULT_PROMPT
    from transformers import AutoModelForCausalLM, AutoProcessor

    torch.set_num_threads(4)
    if not torch.backends.mps.is_available():
        raise RuntimeError('This local MOSS profile requires Apple MPS; no silent model fallback')
    device = torch.device('mps')
    dtype = torch.float16
    root = Path(__file__).resolve().parents[3]
    model_path = root / 'models/MOSS-Transcribe-Diarize'
    # Model/runtime status is diagnostic, never mixed with the response protocol.
    with redirect_stdout(sys.stderr):
        model = AutoModelForCausalLM.from_pretrained(str(model_path), trust_remote_code=True,
            local_files_only=True, dtype=dtype, attn_implementation='sdpa').to(device).eval()
        processor = AutoProcessor.from_pretrained(str(model_path), trust_remote_code=True,
                                                  local_files_only=True)
    def decode(payload):
        began = time.monotonic()
        with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
            language = str(archive['language'].item())
            if language not in {'vi', 'en', 'auto'}:
                raise ValueError('Unsupported language hint')
            tracks = {stream: np.array(archive[stream], dtype=np.float32)
                      for stream in ('loopback','mic') if stream in archive}
        if not tracks or any(pcm.ndim != 1 or not 0 < pcm.size <= 305 * 16000 or not np.isfinite(pcm).all()
               for pcm in tracks.values()):
            raise ValueError('Invalid PCM input')
        with redirect_stdout(sys.stderr):
            prompt = DEFAULT_PROMPT + ' Do not translate.'
            if language == 'vi':
                prompt += ' The conversation is in Vietnamese. Transcribe in Vietnamese.'
            elif language == 'en':
                prompt += ' The conversation is in English. Transcribe in English.'
            rows = []
            for stream, pcm in tracks.items():
                messages = [{'role':'user', 'content':[{'type':'audio','audio':'in-memory'},
                                                       {'type':'text','text':prompt}]}]
                text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                # Pass numpy directly: upstream path helper coerces inputs to filenames.
                inputs = processor(text=text, audio=[pcm], max_length=131072,
                                   return_tensors='pt').to(device)
                prompt_len = int(inputs['attention_mask'][0].sum().item())
                config = copy.deepcopy(model.generation_config)
                config.do_sample = False
                config.max_new_tokens = 16384
                with torch.inference_mode():
                    output = model.generate(**{name:inputs[name] for name in (
                        'input_ids','attention_mask','input_features','audio_feature_lengths',
                        'audio_chunk_mapping')}, generation_config=config)
                generated = output[0][prompt_len:]
                if generated.numel() >= config.max_new_tokens:
                    raise RuntimeError('MOSS token limit reached; transcript is incomplete')
                raw = processor.tokenizer.decode(generated, skip_special_tokens=True).strip()
                duration = pcm.size * 1000 / 16000
                for segment in parse_transcript(raw):
                    start = max(0, round(segment.start * 1000))
                    end = min(round(duration), round(segment.end * 1000))
                    if segment.text.strip() and end > start:
                        rows.append({'stream':stream, 'text':segment.text.strip(),
                                     'start_ms':start, 'end_ms':end, 'language':None if language == 'auto' else language,
                                     'confidence':None, 'is_final':True})
                del inputs, output
            torch.mps.synchronize()
        return {'engine':'MOSS-Transcribe-Diarize', 'dtype':'float16', 'attention':'sdpa',
                'language':language, 'segments':rows,
                'metrics':{'decode_s':time.monotonic()-began,
                    'peak_rss_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024),
                    'mps_tensor_mb':torch.mps.current_allocated_memory()/1024**2,
                    'mps_driver_mb':torch.mps.driver_allocated_memory()/1024**2}}

    if '--stream' in sys.argv:
        import struct
        while True:
            header = sys.stdin.buffer.read(4)
            if not header:
                break
            if len(header) != 4:
                raise ValueError('Incomplete request header')
            size = struct.unpack('<I', header)[0]
            if not 0 < size <= 40_000_000:
                raise ValueError('Invalid request size')
            payload = sys.stdin.buffer.read(size)
            if len(payload) != size:
                raise ValueError('Incomplete request')
            print(json.dumps(decode(payload), ensure_ascii=False), flush=True)
    else:
        payload = sys.stdin.buffer.read(40_000_001)
        if len(payload) > 40_000_000:
            raise ValueError('Recording exceeds development limit')
        print(json.dumps(decode(payload), ensure_ascii=False))


if __name__ == '__main__':
    main()
