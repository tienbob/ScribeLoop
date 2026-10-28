"""Recorder-backed bounded live MOSS transcription and LocalAgreement.

Capture owns the files. Inference only consumes authenticated snapshots and may
fall behind without applying backpressure to recording. All PCM stays in memory.
"""
from __future__ import annotations

import time

import numpy as np

from ..evaluation.metrics import normalize
from .language import LanguageDetectionError, identify_language
from .local_worker import LocalWorker
from .replay import ReplayChunk

STREAMS = ('loopback', 'mic')


def timeline_pcm(frames, start_ms=0, end_ms=None):
    """Keep native starts and gaps; never concatenate speech across a gap."""
    from ..capture.source import sample_position
    if end_ms is None:
        end_ms = max(((sample_position(f)+f.pcm.size)/16 for f in frames), default=start_ms)
    size = max(0, round((end_ms-start_ms)*16))
    if size > 305*16000:
        raise ValueError('Timeline exceeds development bounds')
    pcm = np.zeros(size, dtype=np.float32)
    for frame in frames:
        start = sample_position(frame)-round(start_ms*16)
        lo, hi = max(0,start), min(size,start+frame.pcm.size)
        if hi > lo:
            pcm[lo:hi] = frame.pcm[lo-start:hi-start]
    return pcm


def speech_regions(pcm):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    return get_speech_timestamps(pcm, VadOptions(min_speech_duration_ms=250,
        min_silence_duration_ms=200, speech_pad_ms=150), sampling_rate=16000)


class LocalAgreement:
    """Confirm the unchanged segment prefix across two successive hypotheses.

    Segment boundaries are model estimates. No fabricated word alignment is
    introduced; uncertain tails stay ephemeral until stable or the final decode.
    """
    def __init__(self):
        self.previous = []

    def update(self, rows, *, final=False):
        stable = []
        if final:
            stable = rows
        else:
            for old, new in zip(self.previous, rows):
                if (normalize(old['text']) != normalize(new['text'])
                        or abs(old['start_ms']-new['start_ms']) > 500
                        or abs(old['end_ms']-new['end_ms']) > 500):
                    break
                stable.append(new)
        self.previous = rows
        return stable

    def reset(self):
        self.previous = []


def transcribe_live(audio, session_id, emit, cancelled, capture_done, *, language='auto',
                    locked_language=None, checkpoints=None, confirmed_text=None, worker_factory=LocalWorker,
                    vad=speech_regions, detector=identify_language, poll_s=.25):
    checkpoints = checkpoints or {}
    confirmed = {s:checkpoints.get(s,{}).get('confirmed_ms',0) for s in STREAMS}
    agreements = {s:LocalAgreement() for s in STREAMS}
    last_attempt = {s:(-1,-1) for s in STREAMS}
    deferred = set()
    lock = locked_language
    worker = worker_factory()
    history = {s:(confirmed_text or {}).get(s,'') for s in STREAMS}
    if hasattr(worker,'warm'):
        worker.warm()  # Load once during early audio, before the first decode.
    started = time.monotonic()
    inference_s = 0.0
    decodes = 0
    try:
        while not cancelled.is_set():
            finished = capture_done.is_set()
            snapshots = {}
            for stream in STREAMS:
                try:
                    snapshots[stream] = audio.prefix(session_id,stream,durable=True)
                except FileNotFoundError:
                    if finished:
                        raise
            if len(snapshots) != 2:
                cancelled.wait(poll_s)
                continue
            ends = {s:int(r['end_ms']) for s,(_,r) in snapshots.items()}
            if finished and any(not report['complete'] for _, report in snapshots.values()):
                raise ValueError('Recover incomplete audio before resuming transcription')
            for stream in STREAMS:
                emit({'type':'recorded_checkpoint', 'stream':stream, 'end_ms':ends[stream]})
            if lock is None:
                if language in {'vi','en'}:
                    lock = {'language':language,'mode':'manual','confidence':None,'sample_s':0}
                elif max(ends.values()) >= 6000 or finished:
                    opening = {s:timeline_pcm(frames,0,min(30000,ends[s]))
                               for s,(frames,_) in snapshots.items()}
                    try:
                        lock = detector(opening)
                    except LanguageDetectionError:
                        if finished or max(ends.values()) >= 30000:
                            raise
                if lock is None:
                    cancelled.wait(poll_s)
                    continue
                emit({'type':'language_locked', **lock})
            advanced = False
            for stream in STREAMS:
                start = confirmed[stream]
                available = ends[stream]
                if available <= start:
                    continue
                if not finished and (stream in deferred or available-start >= 60000):
                    deferred.add(stream)
                    emit({'type':'transcription_progress','stream':stream,
                          'confirmed_ms':start,'recorded_ms':available,'lag_ms':available-start,
                          'mode':'deferred','decodes':decodes,'inference_s':inference_s,
                          'wall_s':time.monotonic()-started})
                    continue
                # Up to 20 s of unconfirmed context; cadence 4 s while recording.
                end = min(available,start+20000)
                previous_start, previous_end = last_attempt[stream]
                if not finished and end-start < 4000:
                    continue
                # A full fixed window needs a second hypothesis to confirm.
                if (not finished and previous_start == start and end-previous_end < 4000
                        and end-start < 20000):
                    continue
                frames, _ = snapshots[stream]
                context_start = max(0,start-2000)
                pcm = timeline_pcm(frames,context_start,end)
                spans = vad(pcm)
                if not any(span['end'] > (start-context_start)*16 for span in spans):
                    emit({'type':'confirmed_window','stream':stream,'end_ms':end,'segments':[]})
                    confirmed[stream] = end
                    agreements[stream].reset()
                    advanced = True
                    continue
                began = time.monotonic()
                response = worker.infer({stream:pcm},lock['language'],cancelled)
                inference_s += time.monotonic()-began
                decodes += 1
                rows = []
                for raw in response['segments']:
                    if raw.get('stream') != stream:
                        raise ValueError('Model returned the wrong stream')
                    row = ReplayChunk.model_validate(raw).model_dump()
                    if row['end_ms'] > end-context_start:
                        raise ValueError('Model timestamp exceeds live window')
                    if row['end_ms'] <= row['start_ms']:
                        continue
                    if not any(row['start_ms']*16 < span['end'] and row['end_ms']*16 > span['start']
                               for span in spans):
                        continue
                    row.update(start_ms=row['start_ms']+context_start,end_ms=row['end_ms']+context_start,
                               stream=stream,is_final=True)
                    if row['end_ms'] <= start:
                        continue
                    if row['start_ms'] < start:
                        # Context is decoding support, not new transcript. Remove
                        # exact repeated tokens only for a timestamp-crossing row.
                        tokens = row['text'].split()
                        previous = history[stream].split()[-64:]
                        overlap = 0
                        for count in range(1,min(len(tokens),len(previous))+1):
                            if normalize(' '.join(tokens[:count])) == normalize(' '.join(previous[-count:])):
                                overlap = count
                        tokens = tokens[overlap:]
                        if not tokens:
                            continue
                        row['text'] = ' '.join(tokens)
                        row['start_ms'] = start
                    rows.append(row)
                rows.sort(key=lambda row:(row['start_ms'],row['end_ms']))
                final = finished
                stable = agreements[stream].update(rows,final=final)
                emit({'type':'partial','stream':stream,
                      'segment':{'text':' '.join(r['text'] for r in rows[len(stable):]),
                                 'end_ms':max((r['end_ms'] for r in rows),default=end)}})
                if stable or final:
                    boundary = end if final else max(r['end_ms'] for r in stable)
                    emit({'type':'confirmed_window','stream':stream,'end_ms':boundary,
                          'segments':stable})
                    confirmed[stream] = boundary
                    history[stream] = (history[stream]+' '+' '.join(r['text'] for r in stable))[-4096:]
                    agreements[stream].reset()
                    advanced = True
                last_attempt[stream] = (start,end)
                emit({'type':'transcription_progress','stream':stream,
                      'confirmed_ms':confirmed[stream], 'recorded_ms':available,
                      'lag_ms':max(0,available-confirmed[stream]),
                      'mode':'catching_up' if available-confirmed[stream] > 12000 else 'live',
                      'decodes':decodes,'inference_s':inference_s,
                      'wall_s':time.monotonic()-started,'model_metrics':response.get('metrics',{})})
            if finished and all(confirmed[s] >= ends[s] for s in STREAMS):
                return
            if not advanced:
                cancelled.wait(poll_s)
    finally:
        worker.close()
