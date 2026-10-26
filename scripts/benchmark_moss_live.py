"""Local-only live-window feasibility on pinned public AMI audio; never captures devices."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import soundfile as sf
from scribeloop_core.evaluation.metrics import NORMALIZATION, normalize, score_words
from scribeloop_core.pipeline.moss_meeting import MossConfig

SOURCE = Path('data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav')
SOURCE_SHA = 'f4e64336510ce0076a8e313527d48737d0e952d3a262589823bac14c7acb922f'
REFERENCE = Path('data/corpus-v1/investigation/reference-words.json')


def join_text(previous,current):
    """Experimental boundary deduplication, not a guaranteed word alignment."""
    old,new=previous.split(),current.split()
    overlap=0
    for count in range(1,min(24,len(old),len(new))+1):
        if normalize(' '.join(old[-count:])) == normalize(' '.join(new[:count])):
            overlap=count
    return (previous+' '+' '.join(new[overlap:])).strip(),overlap


def analyze(report,words):
    """Rescore cached outputs with app timestamp bounds and inspect identity resets."""
    reference=report['reference']
    for name,strategy in report['strategies'].items():
        text=''
        rejected=[]
        previous={}
        switches=[]
        first=None
        for index,chunk in enumerate(strategy['chunks']):
            invalid=[s for s in chunk['segments'] if not
                chunk['context_start_s'] <= s['start_s'] < s['end_s'] <= chunk['audio_end_s']]
            # Match app policy: reject the complete response when any timing is invalid.
            rows=[] if invalid else chunk['segments']
            if invalid:
                rejected.append(index)
            if name in {'whole','growing_context10'}:
                text=' '.join(s['text'] for s in rows)
            else:
                incoming=' '.join(s['text'] for s in rows if s['end_s']>chunk['core_start_s'])
                text,_=join_text(text,incoming)
            if first is None and rows:
                first=chunk['simulated_ready_s']
            votes=defaultdict(Counter)
            for row in rows:
                for word in words:
                    midpoint=(word['start']+word['end'])/2-300
                    if row['start_s'] <= midpoint < row['end_s']:
                        votes[row['speaker']][word['speaker']]+=1
            chunk['reference_speaker_votes']={str(k):dict(v) for k,v in votes.items()}
            for speaker,counts in votes.items():
                dominant,count=counts.most_common(1)[0]
                if count>=2 and count/sum(counts.values())>=.6:
                    if speaker in previous and previous[speaker]!=dominant:
                        switches.append({'chunk':index,'raw_speaker_id':speaker,
                            'previous_reference':previous[speaker],'current_reference':dominant})
                    previous[speaker]=dominant
            chunk['invalid_timestamp_rows']=len(invalid)
        score=score_words(reference,text)
        strategy['validated_text']=text
        strategy['validated_wer']=score.wer
        strategy['rejected_window_indices']=rejected
        strategy['raw_id_dominant_reference_switches']=switches
        strategy['simulated_first_valid_text_after_first_ref_s']=None if first is None else first-(words[0]['start']-300)


def save_summary(report,output):
    (output/'results.json').write_text(json.dumps(report,indent=2))
    lines=['# Local MOSS live-window feasibility','',
        f'Public AMI, {report["audio_s"]}s, backend {report["backend"]}; model load {report["model_load_s"]:.3f}s.',
        '', '| Strategy | Raw WER | WER after timing rejection | Median decode | Max decode | Simulated final tail |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for name,result in report['strategies'].items():
        lines.append(f'| {name} | {result["wer"]:.2%} | {result["validated_wer"]:.2%} | {result["median_inference_s"]:.3f}s | {result["max_inference_s"]:.3f}s | {result["simulated_final_tail_s"]:.3f}s |')
    lines+=['',f'Peak process RSS: {report["peak_rss_mib"]:.1f} MiB. This is not a VRAM measurement.',
        '', 'Serial arrival times are simulated from measured decode times. No physical live capture or end-to-end latency was measured.',
        'When included, whole-audio runs first after loading; subsequent strategies are warm. Load time is excluded from window timings.',
        'Overlap deduplication is a heuristic; independent speaker IDs are not persistent person identities.',
        'Responses with out-of-input timestamps are rejected as a whole; raw outputs and rejected-window indices remain in JSON.',
        '', '## Transcripts and speaker checks']
    for name,result in report['strategies'].items():
        lines+=['',f'### {name}','',
            f'Rejected windows: {result["rejected_window_indices"]}; raw speaker-ID reference switches: {result["raw_id_dominant_reference_switches"]}.',
            '',result['validated_text']]
    (output/'results.md').write_text('\n'.join(lines)+'\n')
    print(f'Saved {output}/results.md',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=60,choices=range(20,301))
    parser.add_argument('--output',type=Path,default=Path('data/phase1/moss-live-trial'))
    parser.add_argument('--rescore',action='store_true',help='analyze cached outputs without model inference')
    parser.add_argument('--strategies',nargs='+',choices=['whole','5s_overlap2','10s_overlap2','growing_context10'],
        default=['whole','5s_overlap2','10s_overlap2','growing_context10'])
    args=parser.parse_args()
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:
        raise SystemExit('Pinned public fixture hash changed')
    pcm,rate=sf.read(SOURCE,stop=args.seconds*16000,dtype='float32')
    if rate!=16000 or pcm.shape!=(args.seconds*16000,):
        raise SystemExit('Expected bounded mono 16 kHz public audio')
    words=json.loads(REFERENCE.read_text())
    words=sorted((w for w in words if 300<=w['start']<300+args.seconds),key=lambda w:(w['start'],w['speaker']))
    def reference(end):
        return ' '.join(w['text'] for w in words if w['start']<300+end)
    if args.rescore:
        report=json.loads((args.output/'results.json').read_text())
        if report['audio_s']!=args.seconds or report['sha256']!=SOURCE_SHA:
            raise SystemExit('Cached report does not match this public fixture/duration')
        analyze(report,words)
        save_summary(report,args.output)
        return
    config=MossConfig()
    if not config.public()['configured']:
        raise SystemExit('Run scripts/setup_moss_local.py first')
    sys.path.insert(0,str(config.runtime/'bindings/python/src'))
    os.environ['TRANSCRIBE_LIBRARY']=str(config.runtime/'build/src'/(
        'libtranscribe.dylib' if sys.platform=='darwin' else 'libtranscribe.so'))
    import transcribe_cpp
    transcribe_cpp.set_log_callback(None)

    args.output.mkdir(parents=True,exist_ok=True)
    report={'fixture':str(SOURCE),'sha256':SOURCE_SHA,'audio_s':args.seconds,
        'normalization':NORMALIZATION,'reference':reference(args.seconds),
        'scope':'public offline inference simulation; no native capture, pacing or cloud',
        'strategies':{}}
    started=time.monotonic()
    with transcribe_cpp.Model(config.model,backend='auto') as model, model.session(n_threads=4) as session:
        report['model_load_s']=time.monotonic()-started
        report['backend']=model.backend
        for name,step,context in [('whole',args.seconds,0),('5s_overlap2',5,2),
                                 ('10s_overlap2',10,2),('growing_context10',10,args.seconds)]:
            if name not in args.strategies:
                continue
            rows=[]
            hypothesis=''
            chunks=[]
            cpu_free=0
            for start in range(0,args.seconds,step):
                end=min(args.seconds,start+step)
                begin=max(0,start-context)
                timer=threading.Timer(120,session.cancel)
                tick=time.monotonic()
                timer.start()
                try:
                    result=session.run(pcm[begin*16000:end*16000],language='en',
                        timestamps='segment',diarize='on')
                finally:
                    timer.cancel()
                elapsed=time.monotonic()-tick
                # Simulate serial inference as windows arrive; no real sleeping.
                cpu_free=max(end,cpu_free)+elapsed
                segments=[{'start_s':begin+s.t0_ms/1000,'end_s':begin+s.t1_ms/1000,
                    'speaker':s.speaker_id,'text':s.text.strip()} for s in result.segments]
                discarded=0
                if name=='growing_context10' or name=='whole':
                    hypothesis=result.text.strip()
                    rows=segments
                else:
                    incoming=[s for s in segments if s['end_s']>start]
                    text=' '.join(s['text'] for s in incoming)
                    hypothesis,discarded=join_text(hypothesis,text)
                    rows.extend(incoming)
                score=score_words(reference(end),hypothesis)
                chunk={'context_start_s':begin,'core_start_s':start,'audio_end_s':end,
                    'inference_s':elapsed,'simulated_ready_s':cpu_free,
                    'simulated_backlog_s':cpu_free-end,'speaker_ids':sorted({s['speaker'] for s in segments}),
                    'segments':segments,'text':result.text.strip(),'deduplicated_words':discarded,
                    'cumulative_wer':score.wer}
                chunks.append(chunk)
                wer_text='no reference speech' if score.wer is None else f'{score.wer:.2%}'
                print(f'{name}: audio {begin}–{end}s, decode {elapsed:.3f}s, WER {wer_text}',flush=True)
            score=score_words(reference(args.seconds),hypothesis)
            timings=[c['inference_s'] for c in chunks]
            first=next((c for c in chunks if c['text']),None)
            report['strategies'][name]={'chunks':chunks,'text':hypothesis,'rows':rows,
                'wer':score.wer,'errors':score.errors,'reference_words':score.reference_words,
                'total_inference_s':sum(timings),'median_inference_s':statistics.median(timings),
                'max_inference_s':max(timings),'simulated_final_tail_s':cpu_free-args.seconds,
                'simulated_first_text_after_first_ref_s':None if first is None else first['simulated_ready_s']-(words[0]['start']-300),
                'speaker_warning':'Speaker IDs are scoped to each independent generation; do not equate identical IDs across windows.'}
            (args.output/'results.json').write_text(json.dumps(report,indent=2))
    report['peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
    analyze(report,words)
    save_summary(report,args.output)


if __name__=='__main__':
    main()
