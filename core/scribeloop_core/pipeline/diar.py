"""Delayed community-1 turns with conservative session-local identity matching.

No speaker names or anonymous embeddings are persisted. Concurrent turns are
preserved; uncertain matches remain unknown instead of merging two identities.
"""
from __future__ import annotations

import numpy as np

from .live import timeline_pcm
from .local_worker import LocalWorker


class SpeakerMatcher:
    def __init__(self, *, threshold=.75, margin=.1, next_label=1):
        self.threshold, self.margin = threshold, margin
        self.exemplars = {}
        self.next_label = next_label

    def match(self, embeddings, anchors=None):
        mapping = {}
        used = set()
        for local, raw in embeddings.items():
            anchor = (anchors or {}).get(local)
            if anchor in self.exemplars and anchor not in used:
                mapping[local] = (anchor,None)
                used.add(anchor)
                continue
            vector = np.asarray(raw,dtype=np.float32)
            norm = np.linalg.norm(vector)
            if vector.ndim != 1 or not np.isfinite(vector).all() or norm < 1e-6:
                mapping[local] = (None,None)
                continue
            vector = vector/norm
            scores = sorted(((float(np.dot(vector,other)),label)
                             for label,other in self.exemplars.items() if other.shape == vector.shape),reverse=True)
            top = scores[0][0] if scores else -1
            runner_up = scores[1][0] if len(scores)>1 else -1
            if scores and top >= self.threshold and top-runner_up >= self.margin and scores[0][1] not in used:
                label = scores[0][1]
                mapping[local] = (label,top)
                used.add(label)
            elif not scores or top < .6:
                if len(self.exemplars) >= 16:
                    mapping[local] = (None,None)
                    continue
                label = f'Speaker {self.next_label}'
                self.next_label += 1
                self.exemplars[label] = vector
                mapping[local] = (label,None)
                used.add(label)
            else:
                mapping[local] = (None,None)
        return mapping


class NearOnlineChunked:
    """30 s windows with 5 s context; identities span windows in this process."""
    name = 'near-online-community-1'

    def __init__(self, *, worker=None, next_label=1):
        self.worker = worker or LocalWorker('diar_worker',moss=False,timeout_s=180)
        self.matcher = SpeakerMatcher(next_label=next_label)
        self.context_turns = []

    def window(self, pcm, start_ms, commit_start, commit_end, cancelled):
        result = self.worker.infer({'loopback':pcm},'auto',cancelled)
        # Shared audio provides stronger continuity evidence than a short noisy
        # embedding alone. Require a clear temporal match; overlaps can remain
        # ambiguous and must not force an identity assignment.
        evidence = {}
        for row in result['turns']:
            start,end = row['start_ms']+start_ms,row['end_ms']+start_ms
            for previous in self.context_turns:
                overlap = min(end,previous['end_ms'])-max(start,previous['start_ms'])
                if overlap > 0 and previous.get('label'):
                    scores = evidence.setdefault(row['local_speaker'],{})
                    scores[previous['label']] = scores.get(previous['label'],0)+overlap
        anchors = {}
        for local,scores in evidence.items():
            ranked = sorted(((score,label) for label,score in scores.items()),reverse=True)
            top,label = ranked[0]
            other = ranked[1][0] if len(ranked)>1 else 0
            if top >= 1000 and top >= 4*other:
                anchors[local] = label
        mapping = self.matcher.match(result['embeddings'],anchors=anchors)
        turns = []
        context = []
        for row in result['turns']:
            start, end = row['start_ms']+start_ms, row['end_ms']+start_ms
            if not start_ms <= start < end <= start_ms+round(pcm.size/16):
                raise ValueError('Diarization exceeds supplied window')
            label, confidence = mapping.get(row['local_speaker'],(None,None))
            context.append({'start_ms':start,'end_ms':end,'label':label})
            start, end = max(commit_start,start), min(commit_end,end)
            if end > start:
                turns.append({'start_ms':start,'end_ms':end,'label':label,'confidence':confidence})
        self.context_turns = context
        return turns

    def close(self):
        self.worker.close()


def diarize_live(audio, session_id, emit, cancelled, capture_done, *, checkpoint=0,
                 backend_factory=NearOnlineChunked, next_label=1):
    backend = backend_factory(next_label=next_label)
    position = checkpoint
    try:
        while not cancelled.is_set():
            finished = capture_done.is_set()
            try:
                frames, report = audio.prefix(session_id,'loopback')
            except FileNotFoundError:
                if finished:
                    raise
                cancelled.wait(.5)
                continue
            end = int(report['end_ms'])
            if finished and not report['complete']:
                raise ValueError('Recover interrupted audio before diarization')
            if finished and position >= end:
                return
            if not finished and end-position < 30000:
                cancelled.wait(.5)
                continue
            start = max(0,position-5000)
            window_end = min(end,start+35000)
            commit_end = window_end if finished else window_end-5000
            pcm = timeline_pcm(frames,start,window_end)
            turns = backend.window(pcm,start,position,commit_end,cancelled)
            emit({'type':'speaker_turns','stream':'loopback','turns':turns,'end_ms':commit_end})
            position = commit_end
    finally:
        backend.close()


def attribution(segment, turns):
    """Assign only a single speaker covering >=80%, with no competing turn.

    This is segment-level attribution, never claimed as word alignment.
    """
    if segment['stream'] == 'mic':
        return 'Microphone'
    overlaps = [t for t in turns if t['start_ms'] < segment['end_ms'] and t['end_ms'] > segment['start_ms']]
    labels = {t.get('label') for t in overlaps}
    if len(labels) != 1 or None in labels:
        return 'Overlapping / uncertain speakers' if len(labels)>1 else 'Unknown speaker'
    intervals = sorted((max(t['start_ms'],segment['start_ms']),min(t['end_ms'],segment['end_ms'])) for t in overlaps)
    covered, end = 0, -1
    for start, stop in intervals:
        covered += max(0,stop-max(start,end))
        end = max(end,stop)
    return next(iter(labels)) if covered >= .8*(segment['end_ms']-segment['start_ms']) else 'Unknown speaker'
