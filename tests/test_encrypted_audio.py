from __future__ import annotations

import os
import secrets

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.storage.audio import (
    MAGIC,
    RECORD,
    AudioIntegrityError,
    EncryptedAudioWriter,
    SessionAudioStore,
    read_audio,
)
from scribeloop_core.storage.keys import KeyUnavailable

pytest.importorskip('cryptography')
SID = 'a' * 32


def frame(t=0, stream='loopback'):
    return AudioFrame(np.linspace(-.4,.4,1600,dtype=np.float32),stream,t,0)


def write_sample(path, key):
    with EncryptedAudioWriter(path,SID,'loopback',key) as writer:
        writer.write(frame())
        writer.write(frame(1000))  # Recorder gap is preserved, not silently closed.


def test_fractional_mic_packets_survive_integer_timestamp_rounding(tmp_path):
    from scribeloop_core.capture.clock import SessionClock

    key=secrets.token_bytes(32)
    path=tmp_path/'mic.slpa'
    samples=0
    timings=[]
    clock=SessionClock(0)
    with EncryptedAudioWriter(path,SID,'mic',key) as writer:
        for count in [170,171,171]*100:
            t=clock.milliseconds(84_100_000+samples*1_000_000_000//16000)
            writer.write(AudioFrame(np.zeros(count,dtype='float32'),'mic',t,0))
            timings.append(t)
            samples+=count
    assert timings[:3]==[84,95,105], 'Third valid packet used to fail after 341 samples'
    restored=list(read_audio(path,SID,'mic',key))
    assert [f.t_ms for f in restored]==timings
    assert sum(len(f.pcm) for f in restored)==samples


def test_real_timestamp_overlap_still_fails(tmp_path):
    key=secrets.token_bytes(32)
    with EncryptedAudioWriter(tmp_path/'mic.slpa',SID,'mic',key) as writer:
        writer.write(AudioFrame(np.zeros(160,dtype='float32'),'mic',0,0))
        with pytest.raises(ValueError,match='timestamp'):
            writer.write(AudioFrame(np.zeros(160,dtype='float32'),'mic',8,0))


def test_round_trip_preserves_pcm_stream_and_timestamps_without_raw_file(tmp_path):
    key = secrets.token_bytes(32)
    path = tmp_path/'loopback.slpa'
    write_sample(path,key)
    result = list(read_audio(path,SID,'loopback',key))
    assert [f.t_ms for f in result] == [0,1000]
    assert all(np.array_equal(f.pcm,frame().pcm) for f in result)
    assert all(f.stream=='loopback' for f in result)
    assert frame().pcm.tobytes() not in path.read_bytes()
    assert list(tmp_path.iterdir()) == [path]
    if os.name == 'posix':
        assert path.stat().st_mode & 0o777 == 0o600


def test_independent_file_keys_produce_different_ciphertexts(tmp_path):
    key = secrets.token_bytes(32)
    first,second = tmp_path/'first',tmp_path/'second'
    write_sample(first,key)
    write_sample(second,key)
    assert first.read_bytes()!=second.read_bytes()
    assert len(list(read_audio(first,SID,'loopback',key)))==2
    assert len(list(read_audio(second,SID,'loopback',key)))==2


@pytest.mark.parametrize('mutation', ['tag','truncated','footer_removed','trailing','header','reorder'])
def test_tamper_truncation_and_reordering_are_rejected(tmp_path,mutation):
    key=secrets.token_bytes(32)
    path=tmp_path/'audio'
    write_sample(path,key)
    data=bytearray(path.read_bytes())
    header_size=len(MAGIC)+18+int.from_bytes(data[len(MAGIC)+16:len(MAGIC)+18],'little')
    length1=RECORD.unpack(data[header_size:header_size+RECORD.size])[1]+RECORD.size
    start2=header_size+length1
    length2=RECORD.unpack(data[start2:start2+RECORD.size])[1]+RECORD.size
    if mutation=='tag': data[header_size+RECORD.size+20]^=1
    elif mutation=='truncated': data=data[:-1]
    elif mutation=='footer_removed': data=data[:start2+length2]
    elif mutation=='trailing': data+=b'x'
    elif mutation=='header': data[len(MAGIC)]^=1
    elif mutation=='reorder':
        data=data[:header_size]+data[start2:start2+length2]+data[header_size:start2]+data[start2+length2:]
    path.write_bytes(data)
    with pytest.raises(AudioIntegrityError):
        list(read_audio(path,SID,'loopback',key))


def test_wrong_key_or_session_stream_is_rejected(tmp_path):
    path=tmp_path/'audio'
    key=secrets.token_bytes(32)
    write_sample(path,key)
    for sid,stream,otherkey in [(SID,'loopback',secrets.token_bytes(32)),
                                ('b'*32,'loopback',key),(SID,'mic',key)]:
        with pytest.raises(AudioIntegrityError):
            list(read_audio(path,sid,stream,otherkey))


def test_exception_keeps_only_incomplete_ciphertext_not_a_valid_completed_file(tmp_path):
    path=tmp_path/'audio'
    key=secrets.token_bytes(32)
    with pytest.raises(RuntimeError), EncryptedAudioWriter(path,SID,'loopback',key) as writer:
        writer.write(frame())
        raise RuntimeError('recorder failure')
    with pytest.raises(AudioIntegrityError):
        list(read_audio(path,SID,'loopback',key))
    assert frame().pcm.tobytes() not in path.read_bytes()


def test_invalid_frames_and_exclusive_creation(tmp_path):
    path=tmp_path/'audio'
    key=secrets.token_bytes(32)
    with EncryptedAudioWriter(path,SID,'loopback',key) as writer:
        writer.write(frame())
        for invalid in [frame(0),frame(100,stream='mic'),
                AudioFrame(np.array([1e300],dtype=np.float64),'loopback',100,0),
                AudioFrame(np.array([float('nan')],dtype=np.float32),'loopback',100,0)]:
            with pytest.raises(ValueError): writer.write(invalid)
    original=path.read_bytes()
    with pytest.raises(FileExistsError):
        EncryptedAudioWriter(path,SID,'loopback',key)
    assert path.read_bytes()==original


def test_failed_write_poisons_writer_and_rejects_further_use(tmp_path):
    path=tmp_path/'audio'
    key=secrets.token_bytes(32)
    writer=EncryptedAudioWriter(path,SID,'loopback',key)
    original=writer.file

    class BrokenFile:
        def write(self,*args): raise OSError('disk full')
        def close(self): original.close()

    writer.file=BrokenFile()
    with pytest.raises(OSError): writer.write(frame())
    with pytest.raises(ValueError,match='closed'): writer.write(frame())
    with pytest.raises(AudioIntegrityError): list(read_audio(path,SID,'loopback',key))


def test_session_key_crypto_shred_prevents_future_reads(tmp_path):
    class Keys:
        def __init__(self): self.items={}
        def create(self,account):
            if account in self.items: raise KeyUnavailable('Existing key')
            self.items[account]=secrets.token_bytes(32)
        def get(self,account): return self.items.get(account)
        def delete(self,account): del self.items[account]

    keys=Keys()
    store=SessionAudioStore(tmp_path,keys)
    store.create_session(SID)
    with store.writer(SID,'loopback') as writer: writer.write(frame())
    with store.writer(SID,'mic') as writer: writer.write(frame(stream='mic'))
    assert len(list(store.frames(SID,'mic')))==1
    store.forget_session_key(SID)
    assert store.path(SID,'loopback').exists()
    with pytest.raises(KeyUnavailable): store.frames(SID,'loopback')
    with pytest.raises(ValueError): store.path('../escape','mic')
