"""Bounded SLPA reader. Consumed bytes are authenticated once, never buffered whole."""
from __future__ import annotations

import json
import os
import struct
import time

import numpy as np

from ..capture.source import SAMPLE_RATE, AudioFrame, aligned_sample
from .audio import (
    FOOTER,
    FRAME,
    MAGIC,
    MAX_CIPHERTEXT,
    MAX_SAMPLES,
    RECORD,
    TIMESTAMP_ROUNDING_MS,
    AudioIntegrityError,
    _cipher,
    _identity,
    _read_exact,
)

MAX_TIMELINE_MS = 10_805_000


class AudioCursor:
    def __init__(self,path,sid,stream,key):
        _identity(sid,stream)
        self.path,self.stream = path,stream
        self.file=path.open('rb')
        try:
            fixed=_read_exact(self.file,len(MAGIC)+18)
            size=struct.unpack('<H',fixed[-2:])[0]
            if not fixed.startswith(MAGIC) or not 0<size<=1024:
                raise AudioIntegrityError('Invalid SLPA header')
            metadata=_read_exact(self.file,size)
            if json.loads(metadata) != {'session_id':sid,'stream':stream,'sample_rate':SAMPLE_RATE,'format':'f32le'}:
                raise AudioIntegrityError('Invalid SLPA identity')
            self.header=fixed+metadata
            self.cipher=_cipher(key,fixed[len(MAGIC):len(MAGIC)+16])
            stat=os.fstat(self.file.fileno())
            self.identity=(stat.st_dev,stat.st_ino)
            self.boundary=stat.st_size
            self.sequence=self.samples=0
            self.end_ms=0.
            self.aligned_end=None
            self.complete=False
            self.offset=len(self.header)
        except BaseException:
            self.file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    def close(self):
        self.file.close()
        self.cipher=None

    def read(self,*,max_frames=8,durable=False,max_samples=None):
        from cryptography.exceptions import InvalidTag
        if not 1<=max_frames<=64 or self.file.closed:
            raise ValueError('Invalid cursor read')
        if max_samples is not None and (type(max_samples) is not int or not 1<=max_samples<=2*SAMPLE_RATE):
            raise ValueError('Invalid cursor sample budget')
        stat=self.path.stat()
        if (stat.st_dev,stat.st_ino)!=self.identity or stat.st_size<self.boundary:
            raise AudioIntegrityError('SLPA replaced or truncated')
        self.file.seek(0)
        if self.file.read(len(self.header))!=self.header:
            raise AudioIntegrityError('SLPA header changed')
        if durable:
            os.fsync(self.file.fileno())
        boundary=self.boundary=stat.st_size
        if self.complete and boundary!=self.offset:
            raise AudioIntegrityError('Data after SLPA footer')
        frames=[]
        caught_up=self.complete
        self.file.seek(self.offset)
        batch_samples=0
        while not self.complete and len(frames)<max_frames and (max_samples is None or batch_samples<max_samples):
            start=self.file.tell()
            if boundary-start<RECORD.size:
                caught_up=True
                break
            prefix=_read_exact(self.file,RECORD.size)
            kind,length=RECORD.unpack(prefix)
            if kind not in (0,1) or not 16<=length<=MAX_CIPHERTEXT:
                raise AudioIntegrityError('Invalid SLPA record')
            if boundary-self.file.tell()<length:
                caught_up=True
                break
            counter=self.sequence.to_bytes(12,'big')
            try:
                plain=self.cipher.decrypt(counter,_read_exact(self.file,length),self.header+counter+prefix)
            except InvalidTag:
                raise AudioIntegrityError('SLPA authentication failed') from None
            if kind==1:
                if len(plain)!=FOOTER.size or FOOTER.unpack(plain)!=(self.samples,self.sequence,self.end_ms):
                    raise AudioIntegrityError('Invalid SLPA footer')
                if self.file.tell()!=boundary:
                    raise AudioIntegrityError('Data after SLPA footer')
                self.complete=caught_up=True
            else:
                if len(plain)<FRAME.size:
                    raise AudioIntegrityError('Invalid SLPA frame')
                t,count=FRAME.unpack(plain[:FRAME.size])
                if not 0<count<=MAX_SAMPLES or len(plain)!=FRAME.size+count*4:
                    raise AudioIntegrityError('Invalid SLPA frame size')
                pcm=np.frombuffer(plain[FRAME.size:],dtype='<f4').astype(np.float32)
                end=t+count*1000/SAMPLE_RATE
                if not np.isfinite(pcm).all() or t<self.end_ms-TIMESTAMP_ROUNDING_MS or end>MAX_TIMELINE_MS or self.samples+count>MAX_TIMELINE_MS*SAMPLE_RATE//1000:
                    raise AudioIntegrityError('Invalid SLPA timeline')
                self.end_ms=end
                self.samples+=count
                batch_samples+=count
                self.sequence+=1
                sample_start=aligned_sample(t,self.aligned_end)
                self.aligned_end=sample_start+count
                frames.append(AudioFrame(pcm,self.stream,t,time.monotonic(),sample_start))
            self.offset=self.file.tell()
        if self.offset==boundary:
            caught_up=True
        return frames,{'complete':self.complete,'samples':self.samples,'end_ms':self.end_ms,
            'timeline_end_samples':self.aligned_end or 0,
            'authenticated_bytes':self.offset,'tail_bytes':boundary-self.offset,'caught_up':caught_up}
