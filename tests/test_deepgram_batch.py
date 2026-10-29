"""Saved audio uses one HTTP file upload, with no realtime socket or pacing."""
import io
import threading
import wave

import httpx
import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline.deepgram_batch import run_batch
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig, DeepgramError, StreamingBudget
from test_retained_audio import retained as retained_fixture


@pytest.fixture()
def retained(tmp_path):
    yield from retained_fixture.__wrapped__(tmp_path)


def saved(service):
    sid = service.store.create_recording('Batch test',{'provider':'deepgram','model':'nova-3'})['id']
    service.audio.create_session(sid)
    service.store.set_audio_paths(sid,service.audio)
    for stream,value in [('loopback',.6),('mic',.2)]:
        with service.audio.writer(sid,stream) as writer:
            writer.write(AudioFrame(np.full(1600,value,dtype=np.float32),stream,0,0))
            writer.write(AudioFrame(np.full(1600,value,dtype=np.float32),stream,1200,0))
    service.store.transition(sid,'completed')
    return sid


def response():
    return {'results':{'channels':[{'alternatives':[{'words':[
        {'word':'hello','start':0,'end':.1,'speaker':0},
        {'word':'again','start':1.2,'end':1.3,'speaker':1}]}]} for _ in range(2)]}}


def execute(service,sid,handler,*,language='auto',budget_usd=.15):
    events=[]
    def emit(event):
        events.append(event)
        if event.get('type')=='cloud_request':
            event['ack'].set()
    config=DeepgramConfig(api_key='test-only-key',batch_transport=httpx.MockTransport(handler),
                          connector=lambda *a,**k:pytest.fail('Saved rerun opened a websocket'),
                          budget_usd=budget_usd)
    run_batch(service.audio,sid,emit,threading.Event(),config=config,
              budget=StreamingBudget(config,{}),language=language)
    return events


@pytest.mark.parametrize('language,expected',[('auto','multi'),('en','en'),('vi','vi')])
def test_one_whole_multichannel_wav_preserves_gaps_sources_and_speakers(retained,language,expected):
    sid=saved(retained)
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.method=='POST' and request.url.path=='/v1/listen'
        assert request.url.params['language']==expected
        assert request.url.params.get_list('language_hint')==(['en','vi'] if language=='auto' else [])
        assert request.url.params['multichannel']=='true'
        assert request.url.params['diarize_model']=='v1'
        assert int(request.headers['content-length'])==len(request.content)
        with wave.open(io.BytesIO(request.content),'rb') as wav:
            assert wav.getnchannels()==2 and wav.getframerate()==16000
            assert wav.getnframes()==20800
            pcm=np.frombuffer(wav.readframes(20800),dtype='<i2').reshape(-1,2)/32767
        assert np.allclose(pcm[:1600],[.6,.2],atol=.0001)
        assert not pcm[1600:19200].any()
        assert np.allclose(pcm[19200:],[.6,.2],atol=.0001)
        return httpx.Response(200,json=response())
    events=execute(retained,sid,handler,language=language)
    assert len(calls)==1
    reservations=[e for e in events if e.get('type')=='cloud_request']
    assert len(reservations)==1
    assert reservations[0]['reservation']['billed_audio_s']==2.6
    rows=[e for e in events if 'text' in e]
    assert len(rows)==4 and {r['stream'] for r in rows}=={'mic','loopback'}
    assert len({r['speaker_key'] for r in rows})==4
    assert [e['mode'] for e in events if e.get('type')=='transcription_progress']==['batch_upload','batch_wait']


@pytest.mark.parametrize('fault',['http','channels','timestamps'])
def test_failed_batch_has_no_partial_replacement_or_automatic_retry(retained,fault):
    sid=saved(retained)
    calls=[]
    def handler(request):
        calls.append(1)
        value=response()
        if fault=='channels':
            value['results']['channels'].pop()
        if fault=='timestamps':
            value['results']['channels'][1]['alternatives'][0]['words'][0]['end']=100
        return httpx.Response(429 if fault=='http' else 200,json=value)
    with pytest.raises((DeepgramError,httpx.HTTPStatusError)):
        execute(retained,sid,handler)
    assert len(calls)==1


def test_corrupt_audio_is_rejected_before_dispatch(retained):
    sid=saved(retained)
    path=retained.audio.path(sid,'mic')
    data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
    with pytest.raises(ValueError):
        execute(retained,sid,lambda request:pytest.fail('Uploaded corrupt audio'))


def test_budget_rejects_whole_upload_before_dispatch(retained):
    sid=saved(retained)
    with pytest.raises(DeepgramError,match='budget'):
        execute(retained,sid,lambda request:pytest.fail('Exceeded budget'),budget_usd=.00001)


@pytest.mark.asyncio
async def test_runner_batch_is_atomic_and_keeps_prior_revision_on_error(retained):
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramRecordingRunner
    sid=saved(retained)
    retained.store.append(sid,{'stream':'mic','text':'Original transcript','start_ms':0,
                             'end_ms':100,'is_final':True})
    prior=retained.store.segments(sid)
    events=[]
    calls=[]
    def handler(request):
        calls.append(1)
        assert retained.store.segments(sid)==prior
        return httpx.Response(429,json={})
    async def publish(event):
        events.append(event)
    runner=DeepgramRecordingRunner(retained.store,retained.audio,None,publish,
        config=DeepgramConfig(api_key='test-key',batch_transport=httpx.MockTransport(handler)))
    runner.reprocess(sid,language='en',cloud_consent=True)
    assert runner.reprocess_id==sid
    await runner.task
    assert runner.reprocess_id is None and retained.store.segments(sid)==prior
    assert retained.store.get(sid)['status']=='failed' and len(calls)==1
    runner.cloud.batch_transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response()))
    runner.reprocess(sid,language='en',cloud_consent=True)
    await runner.task
    rows=retained.store.segments(sid)
    assert retained.store.get(sid)['status']=='completed' and len(rows)==4
    assert all(r['revision']==2 for r in rows)
    assert retained.store.segments(sid,revision=1)==prior
    assert any(e['type']=='transcript_replaced' for e in events)
    assert retained.store.cloud_usage(sid)['requests']==2


@pytest.mark.asyncio
async def test_native_playback_reads_are_allowed_during_immutable_saved_rerun(retained):
    from pathlib import Path
    from types import SimpleNamespace

    from scribeloop_core.api.desktop_contract import DesktopContract
    from scribeloop_core.api.desktop_settings import DesktopSettings
    from test_desktop import Vault
    sid=saved(retained)
    retained.store.accept_agreement(retained.store.AGREEMENT_VERSION)
    with retained.store.conn:
        retained.store.conn.execute("UPDATE sessions SET status='processing' WHERE id=?",(sid,))
    recorder=SimpleNamespace(reprocess_id=sid,task=SimpleNamespace(done=lambda:False))
    app=SimpleNamespace(state=SimpleNamespace(store=retained.store,audio=retained,recording=recorder))
    settings=DesktopSettings(Vault())
    contract=DesktopContract(settings,Path('/unused'))
    command={'action':'audio_info','session_id':sid}
    result=await contract.read(app,command,settings.value.summary(),None,None)
    assert result['duration_ms']==1300
    window=await contract.read(app,{'action':'audio_window','session_id':sid,'stream':'conversation',
                                  'start_ms':0},settings.value.summary(),None,None)
    assert window['wav_base64']
    recorder.reprocess_id=None  # A recovery/other writer still excludes playback.
    with pytest.raises(ValueError):
        await contract.read(app,command,settings.value.summary(),None,None)
    recorder.reprocess_id=sid
    with pytest.raises(ValueError):
        await contract.read(app,command,settings.value.summary(),sid,None)


@pytest.mark.parametrize('end,valid',[(1.385,True),(1.4,True),(1.401,False)])
def test_batch_clips_only_small_word_end_overrun(retained,end,valid):
    sid=saved(retained)
    value=response()
    for channel in value['results']['channels']:
        channel['alternatives'][0]['words'][-1]['end']=end
    handler=lambda request:httpx.Response(200,json=value)
    if valid:
        events=execute(retained,sid,handler)
        assert max(row['end_ms'] for row in events if 'text' in row)==1300
    else:
        with pytest.raises(DeepgramError,match='exceeds sent audio'):
            execute(retained,sid,handler)


def test_batch_never_allows_word_start_at_or_after_file_end(retained):
    sid=saved(retained)
    value=response()
    value['results']['channels'][0]['alternatives'][0]['words'][-1].update(start=1.3,end=1.38)
    with pytest.raises(DeepgramError,match='exceeds sent audio'):
        execute(retained,sid,lambda request:httpx.Response(200,json=value))
