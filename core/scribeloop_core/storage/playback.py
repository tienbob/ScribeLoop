"""Authenticated random access after a complete SLPA scan; no plaintext disk cache."""
from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict

import numpy as np

from ..capture.source import SAMPLE_RATE
from .audio import AudioIntegrityError


def signature(path):
    stat=path.stat()
    return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns


class PlaybackIndex:
    """Keep sparse authenticated cursor checkpoints for at most four tracks."""
    def __init__(self,audio):
        self.audio=audio
        self.cache=OrderedDict()

    def index(self,sid,stream):
        path=self.audio.path(sid,stream)
        stamp=signature(path)
        cached=self.cache.get((sid,stream))
        if cached and cached['signature']==stamp:
            self.cache.move_to_end((sid,stream))
            return cached
        points=[]
        with self.audio.cursor(sid,stream) as cursor:
            while not cursor.complete:
                points.append((cursor.end_ms,cursor.offset,cursor.sequence,cursor.samples))
                _,report=cursor.read(max_frames=64)
                if report['caught_up'] and not report['complete']:
                    raise AudioIntegrityError('Recover interrupted audio before playback')
            duration=cursor.end_ms
        if signature(path)!=stamp:
            raise AudioIntegrityError('Audio changed during indexing')
        result={'signature':stamp,'points':points,'times':[p[0] for p in points],
                'duration_ms':round(duration)}
        self.cache[(sid,stream)]=result
        self.cache.move_to_end((sid,stream))
        while len(self.cache)>4:
            self.cache.popitem(last=False)
        return result

    def window(self,sid,stream,start_ms,end_ms):
        index=self.index(sid,stream)
        path=self.audio.path(sid,stream)
        duration=index['duration_ms']
        if start_ms>=duration:
            return np.zeros(0,dtype=np.float32)
        end_ms=min(end_ms,duration)
        pcm=np.zeros(round((end_ms-start_ms)*SAMPLE_RATE/1000),dtype=np.float32)
        point=index['points'][max(0,bisect_right(index['times'],start_ms)-1)]
        with self.audio.cursor(sid,stream) as cursor:
            cursor.end_ms,cursor.offset,cursor.sequence,cursor.samples=point
            while cursor.end_ms<end_ms and not cursor.complete:
                batch,report=cursor.read(max_frames=8)
                for frame in batch:
                    offset=round((frame.t_ms-start_ms)*SAMPLE_RATE/1000)
                    lo,hi=max(0,offset),min(len(pcm),offset+len(frame.pcm))
                    if hi>lo:
                        pcm[lo:hi]=frame.pcm[lo-offset:hi-offset]
                if report['caught_up'] and not report['complete']:
                    raise AudioIntegrityError('Audio became incomplete')
        if signature(path)!=index['signature']:
            self.cache.pop((sid,stream),None)
            raise AudioIntegrityError('Audio changed during playback')
        return pcm
