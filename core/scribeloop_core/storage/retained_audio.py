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

    async def playback(self,sid,stream):
        if self.busy or stream not in {'mic','loopback'}:
            raise ValueError('Audio reader unavailable')
        self.require(sid)
        self.busy = True
        try:
            return await self._worker(self._wav,sid,stream)
        finally:
            self.busy = False

    def _wav(self,sid,stream):
        output = io.BytesIO()
        end = 0
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            for frame in self.audio.frames(sid,stream):
                start = round(frame.t_ms*SAMPLE_RATE/1000)
                finish = start+frame.pcm.size
                if start < end or finish > 305*SAMPLE_RATE:
                    raise ValueError('Playback exceeds bounded recording timeline')
                if start > end:
                    wav.writeframesraw(bytes((start-end)*2))
                wav.writeframesraw((np.clip(frame.pcm,-1,1)*32767).astype('<i2').tobytes())
                end = finish
        if not end:
            raise ValueError('Selected track contains no audio')
        # The generator must authenticate the final footer before bytes leave memory.
        return output.getvalue()
