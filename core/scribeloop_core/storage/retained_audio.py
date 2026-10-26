"""Audio-only expiry and bounded, authenticated playback; no provider requests."""
from __future__ import annotations

import asyncio
import io
import wave

import numpy as np

from ..capture.source import SAMPLE_RATE
from .keys import KeyUnavailable


class RetainedAudio:
    def __init__(self,store,audio):
        self.store,self.audio = store,audio
        self.busy = False
        from .playback import PlaybackIndex
        self.playback_index = PlaybackIndex(audio)

    def status(self,sid):
        policy = self.store.audio_policy(sid)
        try:
            unlocked = policy['expired_at'] is None and self.audio.keys.get('audio:'+sid) is not None
        except KeyUnavailable:
            unlocked = False
        if (self.audio.root/sid).is_symlink():
            unlocked = False
        streams = ('mic',) if self.store.get(sid).get('capture',{}).get('scope') == 'microphone' else ('mic','loopback')
        tracks = [stream for stream in streams
                  if unlocked and self.audio.path(sid,stream).is_file()]
        return {**policy,'available':bool(tracks),'tracks':tracks}

    def require(self,sid):
        if not self.status(sid)['available']:
            raise ValueError('Saved audio expired or unavailable; transcript and notes are preserved')

    async def _worker(self,call,*args):
        # A cancelled client must not release the key lease while a worker reads it.
        task = asyncio.create_task(asyncio.to_thread(call,*args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Even repeated disconnect/shutdown cancellation cannot release a live
            # worker's key lease. Shield every wait until that thread has finished.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:  # noqa: BLE001 - preserve original cancellation
                    break
            if not task.cancelled():
                task.exception()
            raise

    async def cleanup(self,*,authorized=None,on_expired=None):
        if self.busy:
            return []
        self.busy = True
        removed = []
        try:
            for sid in self.store.audio_retention_candidates():
                if authorized is not None and not authorized():
                    break
                await self._worker(self.audio.delete_session,sid)
                # Retry key/file removal if either operation or metadata persistence fails.
                self.store.mark_audio_expired(sid)
                removed.append(sid)
                if on_expired is not None:
                    await on_expired(sid)
            return removed
        finally:
            self.busy = False

    async def playback(self,sid,stream,*,start_ms=0):
        if self.busy or stream not in {'mic','loopback'} or type(start_ms) is not int or not 0<=start_ms<10_805_000:
            raise ValueError('Audio reader unavailable')
        self.require(sid)
        self.busy = True
        try:
            return await self._worker(self._wav,sid,stream,start_ms)
        finally:
            self.busy = False

    def _wav(self,sid,stream,start_ms=0):
        pcm=self.audio.window(sid,stream,start_ms,min(start_ms+305000,10_805_000))
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes((np.clip(pcm,-1,1)*32767).astype('<i2').tobytes())
        return output.getvalue()

    async def describe(self,sid):
        if self.busy:
            raise ValueError('Audio reader unavailable')
        self.require(sid)
        tracks=self.status(sid)['tracks']
        self.busy=True
        try:
            durations=await self._worker(self._durations,sid,tracks)
            return {'duration_ms':max(durations.values(),default=0),'track_durations':durations,
                    'tracks':tracks,'window_ms':60000}
        finally:
            self.busy=False

    def _durations(self,sid,tracks):
        return {stream:self.playback_index.index(sid,stream)['duration_ms'] for stream in tracks}

    async def seek(self,sid,stream,start_ms):
        if (self.busy or stream not in {'conversation','mic','loopback'} or
                type(start_ms) is not int or not 0<=start_ms<10_805_000):
            raise ValueError('Invalid playback request')
        self.require(sid)
        tracks=self.status(sid)['tracks']
        if stream!='conversation' and stream not in tracks:
            raise ValueError('Audio track unavailable')
        selected=tracks if stream=='conversation' else [stream]
        self.busy=True
        try:
            return await self._worker(self._seek,sid,selected,start_ms)
        finally:
            self.busy=False

    def _seek(self,sid,tracks,start_ms):
        windows=[self.playback_index.window(sid,track,start_ms,start_ms+min(60000,10_805_000-start_ms))
                 for track in tracks]
        count=max((len(pcm) for pcm in windows),default=0)
        if not count:
            raise ValueError('No audio at this position')
        mixed=np.zeros(count,dtype=np.float32)
        for pcm in windows:
            mixed[:len(pcm)]+=pcm/len(windows)
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes((np.clip(mixed,-1,1)*32767).astype('<i2').tobytes())
        return output.getvalue()
