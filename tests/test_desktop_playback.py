"""Generated SLPA seeking/mixing and atomic numbered-person naming."""
import io
import wave

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.speakers.registry import SpeakerRegistry
from test_retained_audio import retained as retained_fixture


@pytest.fixture()
def retained(tmp_path):
    yield from retained_fixture.__wrapped__(tmp_path)


def audio_session(service):
    sid=service.store.create_recording('Seek test',{'scope':'application'})['id']
    service.audio.create_session(sid)
    service.store.set_audio_paths(sid,service.audio)
    for stream,value in [('mic',.2),('loopback',.6)]:
        with service.audio.writer(sid,stream) as writer:
            for second in range(140):
                writer.write(AudioFrame(np.full(16000,value,dtype=np.float32),stream,second*1000,0))
    service.store.transition(sid,'completed')
    return sid


def decode(data):
    with wave.open(io.BytesIO(data)) as reader:
        assert reader.getframerate()==16000 and reader.getnchannels()==1
        return np.frombuffer(reader.readframes(reader.getnframes()),dtype='<i2')/32767


@pytest.mark.asyncio
async def test_full_duration_seek_boundaries_mix_and_key_revocation(retained):
    sid=audio_session(retained)
    info=await retained.describe(sid)
    assert info['duration_ms']==140000 and info['track_durations']['mic']==140000
    first=decode(await retained.seek(sid,'conversation',0))
    assert len(first)==60000*16 and np.allclose(first,.4,atol=.0001)
    last=decode(await retained.seek(sid,'mic',139500))
    assert len(last)==500*16 and np.allclose(last,.2,atol=.0001)
    assert len(retained.playback_index.cache)==2
    assert all(len(index['points'])<=4 for index in retained.playback_index.cache.values())
    with pytest.raises(ValueError):
        await retained.seek(sid,'mic',140000)
    with pytest.raises(ValueError):
        await retained.seek(sid,'mic',True)
    retained.audio.forget_session_key(sid)
    with pytest.raises(ValueError):
        await retained.seek(sid,'mic',500)


@pytest.mark.asyncio
async def test_seek_authenticates_footer_and_invalidates_changed_file(retained):
    sid=audio_session(retained)
    await retained.describe(sid)
    path=retained.audio.path(sid,'mic')
    with path.open('r+b') as file:
        file.seek(-1,2)
        byte=file.read(1)
        file.seek(-1,2)
        file.write(bytes([byte[0]^1]))
    with pytest.raises(ValueError):
        await retained.seek(sid,'mic',0)
    assert not retained.busy


def speakers(store):
    sid=store.create_recording('People test',{})['id']
    rows=[]
    for index in range(230):
        rows.append(store.append(sid,{'text':f'Person sample {index}', 'start_ms':index*100,
            'end_ms':index*100+99,'is_final':True,
            'speaker_key':f'deepgram:{"a"*32}:mic:{0 if index<100 else 1 if index<200 else 2}'},stream='mic'))
    store.transition(sid,'completed')
    return sid,rows


def test_numbered_roster_includes_other_pages_and_name_links_atomically(retained,monkeypatch):
    store=retained.store
    sid,_rows=speakers(store)
    roster=store.speaker_roster(sid,1)
    assert [r['person_number'] for r in roster]==[1,2,3]
    assert roster[2]['sample']=='Person sample 200'
    registry=SpeakerRegistry(store)
    person=registry.name_speaker(sid,1,roster[0]['id'],name='Alice')
    assert store.segments(sid,limit=1)[0]['speaker_label']=='Alice'
    assert store.speaker_roster(sid,1)[0]['person_id']==person
    assert registry.list()[0]['learn_enabled']==0
    before=registry.list()
    with pytest.raises(ValueError):
        registry.name_speaker(sid,2,roster[1]['id'],name='Stale')
    assert registry.list()==before
    original=store._write_transcript_notes
    def fail(_sid):
        raise ValueError('injected derived-notes failure')
    monkeypatch.setattr(store,'_write_transcript_notes',fail)
    with pytest.raises(ValueError):
        registry.name_speaker(sid,1,roster[1]['id'],name='Rollback')
    assert registry.list()==before
    assert store.speaker_roster(sid,1)[1]['person_id'] is None
    monkeypatch.setattr(store,'_write_transcript_notes',original)
    registry.name_speaker(sid,1,roster[2]['id'],person_id=person)
    assert store.segments(sid,offset=200,limit=1)[0]['speaker_label']=='Alice'
    store.correct_speaker(sid,1,roster[1]['id'],merge_target=roster[0]['id'])
    assert [r['person_number'] for r in store.speaker_roster(sid,1)]==[1,3]
