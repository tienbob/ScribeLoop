"""Upload only the pinned public five-minute AMI fixture to Groq for comparison.

One request per model, no automatic retries, no local ML, no private meetings.
Only aggregate results are saved; API keys and transcript text are never reported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import soundfile as sf
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words
from scribeloop_core.pipeline.groq_asr import MODELS, GroqError, load_groq_key, transcribe_groq

FIXTURE = Path('data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav')
BASELINE = FIXTURE.with_suffix('.json')
REFERENCE = FIXTURE.with_suffix('.reference.txt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consent-public-upload',action='store_true',required=True,
                        help='Authorize sending the pinned public AMI fixture to Groq')
    parser.add_argument('--models',nargs='+',choices=list(MODELS),default=list(MODELS))
    parser.add_argument('--output',type=Path,default=Path('data/phase1/groq-public-comparison.json'))
    args = parser.parse_args()
    if len(args.models) != len(set(args.models)):
        parser.error('Each model may be requested only once')
    if args.output.exists():
        parser.error('Report already exists; choose another output to avoid accidental re-runs')
    baseline = json.loads(BASELINE.read_text())
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if digest != baseline['audio_sha256']:
        parser.error('Public fixture no longer matches the established benchmark')
    pcm,sr = sf.read(FIXTURE,dtype='float32')
    if sr != 16000 or pcm.ndim != 1 or pcm.size != 300*16000:
        parser.error('Expected the pinned mono 16 kHz five-minute public fixture')
    reference = REFERENCE.read_text()
    if score_words(reference,'').reference_words != baseline['reference_words']:
        parser.error('Reference does not match the existing benchmark')
    api_key = load_groq_key()
    report = {'source':'public AMI ES2004a mixed-headset seconds 300–600',
        'audio_sha256':digest,'reference_sha256':hashlib.sha256(reference.encode()).hexdigest(),
        'audio_s':300,'normalization':NORMALIZATION,'reference_words':baseline['reference_words'],
        'local_baseline':{key:baseline[key] for key in ('model','dtype','attention','wer','word_errors','processing_s')},
        'results':[],'private_meetings_uploaded':False,'local_inference_used':False,
        'limitations':['English public mixed-headset recording; not Vietnamese or VoIP acceptance.',
            'File request timing is not live partial/confirmed latency.',
            'No speaker diarization or overlap-attribution score is provided.',
            'Paid-equivalent estimates are not actual billing; check the Groq usage dashboard.',
            'FLAC transport uses PCM16; source hashes identify the input fixture.']}
    for model in args.models:
        print('Testing '+model+' on public audio…',flush=True)
        began = time.perf_counter()
        try:
            result = transcribe_groq(pcm,api_key=api_key,model=model,language='en')
            elapsed = time.perf_counter()-began
            score = score_words(reference,result['text'])
            row = {'model':model,'status':'completed','request_s':elapsed,
                'speed_realtime':300/elapsed,'wer':score.wer,'word_errors':score.errors,
                'reference_words':score.reference_words,'segments':len(result['segments']),
                'detected_language':result['language'],'upload_bytes':result['upload_bytes'],
                'nominal_paid_equivalent_usd':300/3600*MODELS[model]}
        except GroqError as exc:
            row = {'model':model,'status':'failed','request_s':time.perf_counter()-began,
                   'message':str(exc)}  # Adapter errors deliberately contain no key/body.
        report['results'].append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
        if row['status'] != 'completed':
            break  # Auth/quota/network failure never triggers more API work.
    usage = resource.getrusage(resource.RUSAGE_SELF)
    report['peak_client_rss_mb'] = usage.ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as output:
        json.dump(report,output,indent=2,allow_nan=False)
        output.write('\n')
    print('Saved aggregate report: '+str(args.output),flush=True)
    if any(row['status'] != 'completed' for row in report['results']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
