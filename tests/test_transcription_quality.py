"""Recognition controls with real encrypted PCM and fake provider boundaries."""
import json
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.llm.grounded import summary_messages
from scribeloop_core.pipeline.deepgram_batch import run_batch
from scribeloop_core.pipeline.deepgram_meeting import (
    DeepgramConfig,
    DeepgramError,
    DeepgramRecordingRunner,
    StreamingBudget,
)
from scribeloop_core.pipeline.model_selection import CloudRecordingRouter
from scribeloop_core.pipeline.transcription_quality import TranscriptionQuality, recognition_audio
from test_cloud_meeting import ControlledSource, wait_for
from test_cloud_meeting import state as cloud_state
from test_deepgram_meeting import Socket


@pytest.fixture()
def state(tmp_path):
    yield from cloud_state.__wrapped__(tmp_path)


def test_options_validate_and_deduplicate():
    assert TranscriptionQuality(vocabulary=[' alo ', 'alo', "what's up"]).vocabulary == ['alo', "what's up"]
    for values in ([''], ['a\nb'], ['x'*81], ['名'*80,'字'*80,'語'*80], [str(i) for i in range(51)]):
        with pytest.raises(ValueError):
            TranscriptionQuality(vocabulary=values)
    with pytest.raises(ValueError):
        TranscriptionQuality(asr_microphone_source='unknown')


def test_raw_view_keeps_playback_audio_and_requires_original(state):
    store,audio,_,_ = state
    sid=store.create_recording('Fixture',{})['id']; audio.create_session(sid)
    with audio.writer(sid,'mic') as writer:
        writer.write(AudioFrame(np.full(320,.5,dtype=np.float32),'mic',0,0))
    raw=recognition_audio(audio,TranscriptionQuality(asr_microphone_source='raw'))
    with pytest.raises(FileNotFoundError): raw.scan(sid,'mic')
    with audio.raw_microphone_writer(sid) as writer:
        writer.write(AudioFrame(np.full(320,.1,dtype=np.float32),'mic',0,0))
    with raw.cursor(sid,'mic') as cursor:
        frames,report=cursor.read(max_frames=8)
    assert report['complete'] and np.allclose(frames[0].pcm,.1)
    playback_frames=list(audio.frames(sid,'mic'))
    assert np.allclose(playback_frames[0].pcm,.5)
    assert raw.path(sid,'loopback') == audio.path(sid,'loopback')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_explicit_final_pass_preserves_live_revision_on_failure(state,failure):
    store,audio,events,publish=state
    source=ControlledSource(); urls=[]; uploads=[]
    def connector(url,**kwargs): urls.append(url); return Socket()
    def batch(request):
        uploads.append(request)
        if failure: return httpx.Response(503)
        return httpx.Response(200,json={'results':{'channels':[{'alternatives':[{'words':[
            {'word':'refined','start':0,'end':.1,'speaker':0}]}]} for _ in range(2)]}})
    config=DeepgramConfig(api_key='test-secret',connector=connector,batch_transport=httpx.MockTransport(batch),
        quality={'vocabulary':['exception','Tiếng Việt'],'asr_microphone_source':'raw','final_transcription':True})
    runner=DeepgramRecordingRunner(store,audio,lambda *args,**kwargs:source,publish,config=config)
    try:
        sid=runner.start(None,{'cloud_consent':True,'language_hints':['en','vi'],
            'microphone_processing':{'noise_filter':'off','gain_db':0}},'Fixture',consent_confirmed=True)['id']
        await wait_for(lambda:len(store.segments(sid))>=2)
        runner.stop(sid); await runner.task
        assert store.get(sid)['status']=='completed'
        assert len(uploads)==1
        assert audio.original_path(sid,'mic').with_name('mic.raw.slpa').is_file()
        assert all(parse_qs(urlsplit(url).query)['keyterm']==['exception','Tiếng Việt'] for url in urls)
        assert uploads[0].url.params.get_list('keyterm')==['exception','Tiếng Việt']
        assert len(store.revisions(sid)) == (1 if failure else 2)
        assert all(row['text']=='refined' for row in store.segments(sid)) == (not failure)
        assert any(e['type']=='transcription_warning' for e in events) == failure
        assert bool(store.get(sid)['transcription_warning']) == failure
        assert store.get(sid)['capture']['transcription_quality']['asr_microphone_source']=='raw'
    finally:
        await runner.close()


def test_missing_raw_cannot_drop_mic_from_batch_upload(state):
    store,audio,_,_=state
    sid=store.create_recording('Fixture',{})['id']; audio.create_session(sid)
    for stream in ('mic','loopback'):
        with audio.writer(sid,stream): pass
    config=DeepgramConfig(api_key='test',quality={'asr_microphone_source':'raw'})
    def forbidden(*args,**kwargs): raise AssertionError('Unexpected upload')
    with pytest.raises(DeepgramError,match='Raw microphone'):
        run_batch(recognition_audio(audio,config.quality),sid,forbidden,threading.Event(),
            config=config,budget=StreamingBudget(config,{}),language='auto')


def test_duration_profiles_preserve_quality_and_deadline(state):
    store,audio,_,publish=state
    runner=CloudRecordingRouter(store,audio,None,publish,cloud_config=DeepgramConfig(
        api_key='test',write_timeout_s=7,quality={'vocabulary':['exception']}))
    runner._duration(3600)
    assert runner.cloud.quality.vocabulary==['exception'] and runner.cloud.write_timeout_s==7


def test_summary_keeps_unicode_and_explicit_uncertainty_rules():
    messages=summary_messages([{'id':'s','text':'Không rõ câu này','stream':'mic'}])
    assert 'Không rõ câu này' in messages[1]['content']
    assert '\\u' not in messages[1]['content']
    assert 'never reconstruct missing words' in messages[0]['content']
    assert json.loads(messages[1]['content'])['untrusted_transcript'][0]['text']=='Không rõ câu này'


@pytest.mark.asyncio
async def test_gemini_uses_structured_summary_output_without_retry(monkeypatch):
    from scribeloop_core.llm.summary_provider import SummaryProvider
    client=httpx.AsyncClient; calls=[]
    def handler(request):
        calls.append(request)
        body=json.loads(request.content)
        assert body['response_format']['type']=='json_schema'
        assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    provider=SummaryProvider(settings={'SUMMARY_BASE_URL':'https://generativelanguage.googleapis.com/v1beta/openai',
        'SUMMARY_PROVIDER':'Gemini','SUMMARY_MODEL':'synthetic','SUMMARY_API_KEY':'synthetic-key'})
    assert await provider.generate(summary_messages([{'id':'s','text':'Chưa rõ'}]))=='{}'
    assert len(calls)==1


def test_settings_persist_quality_and_build_session_config():
    from scribeloop_core.api.desktop_settings import DesktopSettings
    class Vault:
        value=None
        def get_password(self,*args): return self.value
        def set_password(self,_service,_account,value): self.value=value
    vault=Vault(); settings=DesktopSettings(vault,transcription_key='synthetic-key')
    settings.save({'vocabulary':[' exception ','Tiếng Việt'],'asr_microphone_source':'raw',
                   'final_transcription':True})
    restored=DesktopSettings(vault,transcription_key='synthetic-key')
    assert restored.cloud().quality.model_dump()=={
        'vocabulary':['exception','Tiếng Việt'],'asr_microphone_source':'raw','final_transcription':True}
    assert DesktopSettings(Vault()).value.asr_microphone_source=='processed'
    assert DesktopSettings(Vault()).value.final_transcription is False


@pytest.mark.asyncio
async def test_optional_pass_does_not_hide_storage_commit_failure(state,monkeypatch):
    import sqlite3
    store,audio,events,publish=state
    sid=store.create_recording('Fixture',{})['id']
    store.append(sid,{'text':'live text','start_ms':0,'end_ms':100,'is_final':True})
    runner=DeepgramRecordingRunner(store,audio,None,publish,
        config=DeepgramConfig(api_key='test',quality={'final_transcription':True}))
    async def replacement(*args,**kwargs):
        return [{'text':'refined','stream':'mic','start_ms':0,'end_ms':100,'is_final':True}]
    def fail(*args,**kwargs): raise sqlite3.OperationalError('injected')
    monkeypatch.setattr(runner,'_process',replacement)
    monkeypatch.setattr(store,'revise_transcript',fail)
    with pytest.raises(sqlite3.OperationalError): await runner._complete_live(sid)
    assert not any(e['type']=='transcription_warning' for e in events)
    assert store.segments(sid)[0]['text']=='live text'
    await runner.close()
