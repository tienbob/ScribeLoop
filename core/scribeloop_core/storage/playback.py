"""Authenticated random access after a complete SLPA scan; no plaintext disk cache."""
from __future__ import annotations

from bisect import bisect_right
from math import ceil

import numpy as np

from ..capture.source import SAMPLE_RATE, sample_position
from .audio import AudioIntegrityError


def signature(path):
    stat=path.stat()
    return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns


class PlaybackIndex:
    """Shared-budget sparse indexes and authenticated minute-sized PCM blocks."""
    def __init__(self,audio):
        self.audio=audio
        self.cache=audio.memory_cache.namespace('playback_index',owner=lambda key:key[0])

    def index(self,sid,stream):
        path=self.audio.path(sid,stream)
        stamp=signature(path)
        cached=self.cache.get((sid,stream))
        if cached and cached['signature']==stamp:
            self.cache.move_to_end((sid,stream))
            return cached
        if cached:
            self.audio.memory_cache.invalidate(sid,namespaces={'playback_index','pcm'})
        points=[]
        with self.audio.cursor(sid,stream) as cursor:
            while not cursor.complete:
                points.append((cursor.end_ms,cursor.offset,cursor.sequence,cursor.samples,cursor.aligned_end))
                _,report=cursor.read(max_frames=64)
                if report['caught_up'] and not report['complete']:
                    raise AudioIntegrityError('Recover interrupted audio before playback')
            duration_samples=cursor.aligned_end or 0
        if signature(path)!=stamp:
            raise AudioIntegrityError('Audio changed during indexing')
        result={'signature':stamp,'points':points,'times':[(p[4] or 0)*1000/SAMPLE_RATE for p in points],
                'duration_ms':ceil(duration_samples*1000/SAMPLE_RATE),'duration_samples':duration_samples}
        self.cache[(sid,stream)]=result
        self.cache.move_to_end((sid,stream))
        return result

    def window(self,sid,stream,start_ms,end_ms):
        if not 0<=start_ms<end_ms<=10_805_000 or end_ms-start_ms>305000:
            raise ValueError('Invalid bounded playback window')
        # Serialize key revocation with cache reads/insertions. Deletion then
        # evicts all plaintext entries after any existing reader finishes.
        with self.audio._key_lock:
            self.audio._key(sid)
            return self._window(sid,stream,start_ms,end_ms)

    def _window(self,sid,stream,start_ms,end_ms):
        index=self.index(sid,stream)
        path=self.audio.path(sid,stream)
        duration=index['duration_ms']
        if start_ms>=duration:
            return np.zeros(0,dtype=np.float32)
        end_ms=min(end_ms,duration)
        start_sample=round(start_ms*SAMPLE_RATE/1000)
        end_sample=min(round(end_ms*SAMPLE_RATE/1000),index['duration_samples'])
        pcm=np.empty(max(0,end_sample-start_sample),dtype=np.float32)
        block_samples=60*SAMPLE_RATE
        for start in range(start_sample//block_samples*block_samples,end_sample,block_samples):
            end=min(start+block_samples,index['duration_samples'])
            key=('pcm',(sid,stream,index['signature'],start))
            block=self.audio.memory_cache.get(key)
            if block is None:
                block=self._read_window(sid,stream,index,start,end)
                block.flags.writeable=False
                self.audio.memory_cache.put(key,block,owner=sid)
            lo,hi=max(start_sample,start),min(end_sample,end)
            pcm[lo-start_sample:hi-start_sample]=block[lo-start:hi-start]
        if signature(path)!=index['signature']:
            self.audio.memory_cache.invalidate(sid,namespaces={'playback_index','pcm'})
            raise AudioIntegrityError('Audio changed during playback')
        return pcm

    def _read_window(self,sid,stream,index,start_sample,end_sample):
        pcm=np.zeros(end_sample-start_sample,dtype=np.float32)
        point=index['points'][max(0,bisect_right(index['times'],start_sample*1000/SAMPLE_RATE)-1)]
        with self.audio.cursor(sid,stream) as cursor:
            cursor.end_ms,cursor.offset,cursor.sequence,cursor.samples,cursor.aligned_end=point
            while (cursor.aligned_end or 0)<end_sample and not cursor.complete:
                batch,report=cursor.read(max_frames=8)
                for frame in batch:
                    offset=sample_position(frame)-start_sample
                    lo,hi=max(0,offset),min(len(pcm),offset+len(frame.pcm))
                    if hi>lo:
                        pcm[lo:hi]=frame.pcm[lo-offset:hi-offset]
                if report['caught_up'] and not report['complete']:
                    raise AudioIntegrityError('Audio became incomplete')
        if signature(self.audio.path(sid,stream))!=index['signature']:
            self.audio.memory_cache.invalidate(sid,namespaces={'playback_index','pcm'})
            raise AudioIntegrityError('Audio changed during playback')
        return pcm
