"""Small public Vietnamese Groq comparison with a standalone three-column report."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import html
import io
import json
import tarfile
import time
from pathlib import Path

import httpx
import soundfile as sf
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words
from scribeloop_core.pipeline.groq_asr import MODELS, GroqError, load_groq_key, transcribe_groq

REVISION = '3cbfb2502e5e84776b4b778b020a09759f723f52'
BASE = f'https://huggingface.co/datasets/AILAB-VNUHCM/vivos/resolve/{REVISION}/data/'
ROOT = Path('data/phase1/groq-vietnamese-inspection')


class BoundedReader(io.RawIOBase):
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.buffer = b''
        self.received = 0
    def readable(self):
        return True
    def readinto(self, target):
        if not self.buffer:
            self.buffer = next(self.chunks,b'')
            self.received += len(self.buffer)
            if self.received > 96*1024**2:
                raise ValueError('Stopped at the public archive download bound')
        size = min(len(target),len(self.buffer))
        target[:size] = self.buffer[:size]
        self.buffer = self.buffer[size:]
        return size


def prepare():
    manifest = ROOT/'samples.json'
    if manifest.exists():
        return json.loads(manifest.read_text())
    (ROOT/'audio').mkdir(parents=True,exist_ok=True)
    references = {}
    with httpx.Client(timeout=40,follow_redirects=True) as client:
        for split in ('test','train'):
            response = client.get(BASE+f'prompts-{split}.txt.gz')
            response.raise_for_status()
            references[split] = dict(line.split(' ',1) for line in
                gzip.decompress(response.content).decode().splitlines() if ' ' in line)
        rows = []
        with client.stream('GET',BASE+'vivos.tar.gz') as response:
            response.raise_for_status()
            reader = BoundedReader(response.iter_bytes(65536))
            with tarfile.open(fileobj=io.BufferedReader(reader),mode='r|gz') as archive:
                for member in archive:
                    parts = Path(member.name).parts
                    if (not member.isfile() or not member.name.endswith('.wav') or len(parts)<4
                            or parts[0]!='vivos' or parts[1] not in references):
                        continue
                    name,split = Path(member.name).name,parts[1]
                    stem = Path(name).stem
                    if stem not in references[split] or not 0 < member.size <= 2_000_000:
                        continue
                    contents = archive.extractfile(member).read()
                    pcm,sr = sf.read(io.BytesIO(contents),dtype='float32')
                    if sr != 16000 or pcm.ndim != 1 or not 2 <= len(pcm)/sr <= 30:
                        continue
                    # Basename-only output; never extract arbitrary archive paths.
                    target = ROOT/'audio'/name
                    target.write_bytes(contents)
                    rows.append({'filename':name,'split':split,'reference':references[split][stem],
                        'duration_s':len(pcm)/sr,'sha256':hashlib.sha256(contents).hexdigest()})
                    if len(rows)==5:
                        break
        if len(rows)!=5:
            raise ValueError('Could not obtain five bounded public reference-labelled clips')
    manifest.write_text(json.dumps({'dataset':'AILAB-VNUHCM/vivos','revision':REVISION,
        'license':'CC BY-NC-SA 4.0; research diagnostics only','selection':'First five archive WAVs lasting 2–30 seconds; no result-based selection',
        'samples':rows},ensure_ascii=False,indent=2)+'\n')
    print('Prepared five public Vietnamese clips; compressed archive bytes read:',reader.received,flush=True)
    return json.loads(manifest.read_text())


def render(manifest,results,output):
    esc = html.escape
    tables = []
    for model in MODELS:
        records = results.get(model,{})
        body = []
        errors = words = 0
        for sample in manifest['samples']:
            row = records.get(sample['filename'])
            encoded = base64.b64encode((ROOT/'audio'/sample['filename']).read_bytes()).decode()
            filename = esc(sample['filename'])
            sound = f'<strong>{filename}</strong><small>{sample["duration_s"]:.2f} s · {esc(sample["split"])} split</small><audio controls preload="none" aria-label="{filename}" src="data:audio/wav;base64,{encoded}"></audio>'
            if row and row['status']=='completed':
                transcription = esc(row['text'])
                wer = f'{row["wer"]:.2%}'
                errors += row['word_errors']
                words += row['reference_words']
                detail = f'{row["word_errors"]} errors / {row["reference_words"]} words'
            else:
                transcription = esc(row['message'] if row else 'Not run')
                wer,detail = 'N/A','No score'
            transcript = f'<p>{transcription}</p><details><summary>Reference transcript</summary><p>{esc(sample["reference"])}</p></details>'
            body.append(f'<tr><td>{sound}</td><td>{transcript}</td><td><strong>{wer}</strong><small>{detail}</small></td></tr>')
        overall = f'{errors/words:.2%}' if words else 'N/A'
        tables.append(f'<section><h2>{esc(model)}</h2><p class="muted">Weighted WER: <b>{overall}</b> · {words} reference words scored</p><div class="table-wrap"><table><thead><tr><th>Sound file name</th><th>Transcribe result in text</th><th>WER ↓</th></tr></thead><tbody>{"".join(body)}</tbody></table></div></section>')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Groq Vietnamese transcription inspection</title>
<style>body{font:16px/1.55 system-ui,sans-serif;color:#24352e;background:#f5f3ec;margin:0;padding:36px}main{max-width:1400px;margin:auto}h1{font:38px/1.2 Georgia,serif}h2{font-size:22px;margin:0}section{margin:36px 0}p{margin:0 0 14px}.muted,small{color:#596253}small{display:block;font-size:12px;margin-top:8px}table{border-collapse:collapse;width:100%;table-layout:fixed;background:#fffef9}th,td{padding:18px;border:1px solid #cbd0c5;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#e7ebdf}th:first-child{width:26%}th:last-child{width:15%}audio{width:100%;margin-top:14px}details{font-size:14px;color:#596253}summary{cursor:pointer}details p{margin-top:12px}td p{white-space:pre-wrap}a{color:#295d49}.table-wrap{overflow:auto}footer{border-top:1px solid #cbd0c5;padding-top:20px;font-size:13px}@media(max-width:700px){body{padding:16px}table{min-width:720px}h1{font-size:30px}}:focus-visible{outline:3px solid #3b715a;outline-offset:3px}</style>
<main><h1>Vietnamese transcription inspection</h1><p>Five public Vietnamese read-speech clips · Groq cloud · 23 October 2026</p><p class="muted">Play each sound in column 1. Read the complete model output in column 2; expand Reference transcript to compare. Lower WER is better. Each model has the same three columns.</p>
'''+''.join(tables)+f'''<footer><p>Source: <a href="https://huggingface.co/datasets/AILAB-VNUHCM/vivos">VIVOS, AILAB / VNUHCM University of Science</a>, revision <code>{REVISION}</code>. CC BY-NC-SA 4.0; research diagnostics only. Selection: {esc(manifest['selection'])}.</p><p>Scoring: {NORMALIZATION}. Unicode NFKC, lowercase and punctuation removal; accents retained; whitespace-delimited words (Vietnamese syllables), no digit-to-word normalization. Weighted WER sums word errors and reference words across completed clips. This small read-speech sample does not establish meeting, overlap or speaker accuracy. No private meetings or API keys are included.</p></footer></main></html>''',encoding='utf-8')
    print('Inspection report:',output,flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--consent-public-upload',action='store_true')
    parser.add_argument('--render-only',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'results.html')
    args = parser.parse_args()
    manifest = prepare()
    if args.prepare_only:
        return
    cache = ROOT/'results.json'
    results = json.loads(cache.read_text()) if cache.exists() else {}
    if not args.render_only:
        if not args.consent_public_upload:
            parser.error('Explicit --consent-public-upload is required')
        key = load_groq_key()
        for model in MODELS:
            model_rows = results.setdefault(model,{})
            for sample in manifest['samples']:
                if sample['filename'] in model_rows:
                    continue  # Preserve successes/failures; rendering never repeats uploads.
                path = ROOT/'audio'/sample['filename']
                if hashlib.sha256(path.read_bytes()).hexdigest()!=sample['sha256']:
                    raise ValueError('Public sample changed; refusing upload')
                pcm,sr = sf.read(path,dtype='float32')
                if sr != 16000:
                    raise ValueError('Expected 16 kHz public sample')
                print(model+': '+sample['filename'],flush=True)
                started = time.perf_counter()
                try:
                    result = transcribe_groq(pcm,api_key=key,model=model,language='vi')
                    score = score_words(sample['reference'],result['text'])
                    row = {'status':'completed','text':result['text'],'wer':score.wer,
                        'word_errors':score.errors,'reference_words':score.reference_words,
                        'request_s':time.perf_counter()-started}
                except GroqError as exc:
                    row = {'status':'failed','message':str(exc)}
                model_rows[sample['filename']] = row
                cache.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
                if row['status']!='completed':
                    render(manifest,results,args.output)
                    raise SystemExit('Stopped after request failure; completed results retained')
    render(manifest,results,args.output)


if __name__=='__main__':
    main()
