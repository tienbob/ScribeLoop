"""Local transcript copy editing preserves source revisions and timing."""
import json
import threading

import pytest
from scribeloop_core.api.desktop_settings import Settings
from scribeloop_core.llm.transcript_cleanup import checked_edits, clean_transcript
from scribeloop_core.pipeline.local_transcription import (
    LocalRecordingRunner,
    LocalTranscriptionConfig,
)
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


class Editor:
    provider = 'Ollama'
    cloud = False
    configured = True
    model = 'local-text'
    def __init__(self,fail_at=None):
        self.calls = []
        self.fail_at = fail_at
    async def generate(self,messages,*,schema):
        self.calls.append((messages,schema))
        if len(self.calls)==self.fail_at:
            raise ValueError('Unusable result')
        rows = json.loads(messages[1]['content'])['untrusted_transcript']
        return json.dumps({'segments':[{'id':row['id'],'text':row['text']+'.'} for row in rows]})


@pytest.fixture()
def saved():
    conn = migrate(connect(':memory:'))
    store = SessionStore(conn)
    sid = store.create_recording('Local meeting',{'scope':'microphone','provider':'local-ollama'})['id']
    row = store.append(sid,{'text':'Nam will send 25 reports tomorrow','start_ms':100,'end_ms':1200,
        'is_final':True,'language':'en','speaker_key':'ollama:'+'a'*32+':mic:1'},stream='mic')
    store.transition(sid,'completed')
    store.correct_speaker(sid,1,row['local_speaker_id'],label='Nam')
    store.transition(sid,'processing')
    yield store,sid,row
    conn.close()


def test_cleanup_is_detached_from_the_notes_provider():
    base = {'transcription_engine':'ollama','local_transcription_model':'audio'}
    # Cloud notes no longer gate cleanup: an explicit local cleanup model works.
    cloud_notes = Settings(**base,summary_provider='OpenAI',summary_model='gpt-5.4-mini',
        summary_key='sk-'+'x'*20,local_cleanup_model='text-model')
    provider = cloud_notes.local_transcription().cleanup_provider
    assert provider is not None and provider.cloud is False and provider.model == 'text-model'
    assert cloud_notes.summary().cloud is True
    # Empty cleanup model with cloud notes fails closed; it never reuses a cloud model.
    assert Settings(**base,summary_provider='OpenAI',
        summary_model='gpt-5.4-mini').local_transcription().cleanup_provider is None
    # Unset cleanup model reuses the local notes model, preserving prior behavior.
    local = Settings(**base,summary_provider='Ollama',summary_endpoint='http://localhost:11434/v1',summary_model='text')
    assert local.local_transcription().cleanup_provider.cloud is False
    assert local.local_transcription().cleanup_provider.model == 'text'
    assert local.model_copy(update={'local_transcript_cleanup':False}).local_transcription().cleanup_provider is None


def test_cleanup_configures_independently_of_the_transcription_engine():
    # Deepgram transcription + local cleanup is a supported combination.
    settings = Settings(transcription_engine='deepgram',local_cleanup_model='text-model')
    provider = settings.cleanup()
    assert provider is not None and provider.cloud is False and provider.model == 'text-model'
    # No cleanup model with cloud notes means no cleanup provider at all.
    assert Settings(transcription_engine='deepgram').cleanup() is None
    assert settings.model_copy(update={'local_transcript_cleanup':False}).cleanup() is None
    assert settings.public()['local_cleanup_configured'] is True
    assert Settings(transcription_engine='deepgram').public()['local_cleanup_configured'] is False


def test_cleanup_provider_selection_covers_cloud_and_local():
    # LM Studio cleanup with its own endpoint preset and model.
    lm = Settings(local_cleanup_provider='LM Studio',local_cleanup_model='notes-model')
    provider = lm.cleanup()
    assert provider is not None and provider.provider == 'LM Studio'
    assert provider.cloud is False
    assert provider.base_url == 'http://localhost:1234/v1'
    assert lm.public()['local_cleanup_provider'] == 'LM Studio'
    assert lm.public()['local_cleanup_endpoint'] == 'http://localhost:1234/v1'
    # A cloud cleanup provider works with a saved key.
    cloud = Settings(local_cleanup_provider='OpenAI',local_cleanup_model='gpt-5.4-mini',
        local_cleanup_keys={'OpenAI':'sk-'+'x'*20})
    cloud_provider = cloud.cleanup()
    assert cloud_provider is not None and cloud_provider.cloud is True
    assert cloud_provider.base_url == 'https://api.openai.com/v1'
    assert cloud.public()['local_cleanup_configured'] is True
    # A cloud cleanup provider without any key stays off; no key is invented.
    assert Settings(local_cleanup_provider='OpenAI',
        local_cleanup_model='gpt-5.4-mini').cleanup() is None
    # The notes key of the same provider is the cleanup fallback.
    shared = Settings(local_cleanup_provider='OpenAI',local_cleanup_model='gpt-5.4-mini',
        summary_provider='OpenAI',summary_key='sk-'+'y'*20)
    assert shared.cleanup().configured is True
    # A cloud endpoint without a key stays off; with a key it is a real cloud engine.
    remote = Settings(local_cleanup_provider='Custom',local_cleanup_model='m',
        local_cleanup_endpoint='https://api.example.com/v1')
    assert remote.cleanup() is None
    keyed = remote.model_copy(update={'local_cleanup_keys':{'Custom':'sk-'+'z'*20}})
    assert keyed.cleanup().cloud is True
    # An explicit endpoint is honored while it stays on-device.
    custom = Settings(local_cleanup_provider='Ollama',local_cleanup_model='m',
        local_cleanup_endpoint='http://127.0.0.1:11434/v1')
    assert custom.cleanup().base_url == 'http://127.0.0.1:11434/v1'
    # The model fallback reuses notes only on the same provider.
    same = Settings(local_cleanup_provider='LM Studio',summary_provider='LM Studio',
        summary_endpoint='http://localhost:1234/v1',summary_model='notes-model')
    assert same.cleanup().model == 'notes-model'
    other = Settings(local_cleanup_provider='Ollama',summary_provider='LM Studio',
        summary_endpoint='http://localhost:1234/v1',summary_model='notes-model')
    assert other.cleanup() is None


@pytest.mark.asyncio
async def test_cleanup_accepts_local_and_cloud_providers():
    class LMStudio(Editor):
        provider = 'LM Studio'
    edits,rejects,_ = await clean_transcript([{'id':'a','text':'Hello'}],LMStudio(),threading.Event())
    assert edits == {'a':'Hello.'} and rejects == []
    # A cloud cleanup provider is equally valid; only an unconfigured one is refused.
    cloud = LMStudio()
    cloud.cloud = True
    edits,rejects,_ = await clean_transcript([{'id':'a','text':'Hello'}],cloud,threading.Event())
    assert edits == {'a':'Hello.'} and rejects == []
    unconfigured = LMStudio()
    unconfigured.configured = False
    with pytest.raises(ValueError,match='configured'):
        await clean_transcript([{'id':'a','text':'Hello'}],unconfigured,threading.Event())


@pytest.mark.asyncio
async def test_validation_off_keeps_the_raw_model_result():
    # The Experience toggle bypasses every safety check: changed words, digits
    # and even a dropped segment pass through unfiltered.
    class Raw(Editor):
        async def generate(self,messages,*,schema):
            return json.dumps({'segments':[
                {'id':'a','text':'Buy 15 apples tomorrow'},   # numbers changed
                {'id':'b','text':'We will not attend'},       # negation changed
                {'id':'c','text':'Completely rewritten text'}]})
    rows = [{'id':'a','text':'Buy 25 apples yesterday'},{'id':'b','text':'We will attend'},
            {'id':'c','text':'Original text'},{'id':'d','text':'The model dropped me'}]
    edits,rejects,_ = await clean_transcript(rows,Raw(),threading.Event(),validate=False)
    assert edits == {'a':'Buy 15 apples tomorrow','b':'We will not attend',
        'c':'Completely rewritten text','d':'The model dropped me'}
    assert rejects == []
    # The store's full-coverage requirement still holds: the dropped ID keeps
    # its original text, so the save never fails for missing coverage.
    assert set(edits) == {'a','b','c','d'}
    # Validation on rejects the same batch per segment, keeping originals.
    edits,rejects,_ = await clean_transcript(rows,Editor(),threading.Event())
    assert edits == {'a':'Buy 25 apples yesterday.','b':'We will attend.',
        'c':'Original text.','d':'The model dropped me.'}
    assert rejects == []


def test_cleanup_validate_setting_travels_with_the_provider():
    assert Settings().local_cleanup_validate is True
    assert Settings().cleanup() is None  # no model yet
    on = Settings(local_cleanup_model='text-model').cleanup()
    assert on.cleanup_validate is True
    off_settings = Settings(local_cleanup_model='text-model',local_cleanup_validate=False)
    assert off_settings.cleanup().cleanup_validate is False
    assert off_settings.public()['local_cleanup_validate'] is False
    assert Settings(local_cleanup_model='text-model').public()['local_cleanup_validate'] is True


@pytest.mark.asyncio
async def test_lm_studio_cleanup_uses_json_schema_response_format(monkeypatch):
    import httpx
    seen = {}
    def handler(request):
        seen['url'] = str(request.url)
        seen['body'] = json.loads(request.content)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"segments":[]}'},
            'finish_reason':'stop'}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',
        lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    provider = Settings(local_cleanup_provider='LM Studio',local_cleanup_model='notes-model').cleanup()
    raw = await provider.generate([{'role':'user','content':'x'}],schema={'type':'object'})
    assert seen['url'].startswith('http://localhost:1234/v1/chat/completions')
    assert seen['body']['response_format']['json_schema']['schema'] == {'type':'object'}
    assert raw == '{"segments":[]}'


@pytest.mark.asyncio
async def test_openai_cleanup_sends_grammar_in_responses_text_format(monkeypatch):
    import httpx
    seen = {}
    def handler(request):
        seen['url'] = str(request.url)
        seen['body'] = json.loads(request.content)
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message',
            'content':[{'type':'output_text','text':'{\"segments\":[]}'}]}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',
        lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    provider = Settings(local_cleanup_provider='OpenAI',local_cleanup_model='gpt-5.4-mini',
        local_cleanup_keys={'OpenAI':'sk-'+'x'*20}).cleanup()
    raw = await provider.generate([{'role':'user','content':'x'}],schema={'type':'object'})
    assert seen['url'].startswith('https://api.openai.com/v1/responses')
    assert seen['body']['text']['format']['type'] == 'json_schema'
    assert seen['body']['text']['format']['name'] == 'transcript_cleanup'
    assert seen['body']['text']['format']['schema'] == {'type':'object'}
    assert raw == '{"segments":[]}'


@pytest.mark.asyncio
async def test_gemini_cleanup_sends_the_cleanup_schema_not_the_summary_grammar(monkeypatch):
    import httpx
    seen = {}
    def handler(request):
        seen['url'] = str(request.url)
        seen['body'] = json.loads(request.content)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{\"segments\":[]}'},
            'finish_reason':'stop'}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',
        lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    provider = Settings(local_cleanup_provider='Gemini',local_cleanup_model='gemini-2.5-flash',
        local_cleanup_keys={'Gemini':'g'*20}).cleanup()
    raw = await provider.generate([{'role':'user','content':'x'}],
        schema={'type':'object','properties':{'segments':{'type':'array'}}})
    assert seen['url'].startswith('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
    grammar = seen['body']['response_format']['json_schema']
    assert grammar['name'] == 'transcript_cleanup'
    assert grammar['schema'] == {'type':'object','required':['segments'],
        'properties':{'segments':{'type':'array'}}}
    assert raw == '{"segments":[]}'


@pytest.mark.asyncio
async def test_deepgram_runner_cleans_transcript_locally_after_stop(saved):
    from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig, DeepgramRecordingRunner
    store,sid,raw = saved
    events = []
    async def publish(event):
        events.append(event)
    runner = DeepgramRecordingRunner(store,None,None,publish,
        config=DeepgramConfig(),cleanup_provider=Editor())
    await runner._finish(sid)
    assert store.segments(sid)[0]['text'] == raw['text']+'.'
    assert store.segments(sid)[0]['revision'] == 2
    # Provenance stays Deepgram so the session can still be re-transcribed by it.
    assert store.transcript_provider(sid) == 'deepgram'
    types = [event['type'] for event in events]
    assert types.index('transcript_replaced') < types.index('notes')


@pytest.mark.parametrize('text', ['Nam will send 26 reports tomorrow','Everyone approved a launch next Tuesday',
    'Nam will send 25 reports tomorrow and promised to complete all development work by Monday'])
def test_cleanup_rejects_changed_numbers_or_large_rewrites(text):
    rows = [{'id':'source','text':'Nam will send 25 reports tomorrow'}]
    edits,rejects = checked_edits(json.dumps({'segments':[{'id':'source','text':text}]}),rows)
    assert edits == {'source':'Nam will send 25 reports tomorrow'}
    assert [item['id'] for item in rejects] == ['source'] and rejects[0]['reason']


def test_cleanup_rejects_added_or_reordered_ids():
    rows = [{'id':'a','text':'Hello'},{'id':'b','text':'Goodbye'}]
    with pytest.raises(ValueError):
        checked_edits(json.dumps({'segments':[{'id':'a','text':'Hello'},{'id':'b','text':'Goodbye'},{'id':'c','text':'Extra'}]}),rows)
    with pytest.raises(ValueError):
        checked_edits(json.dumps({'segments':[{'id':'b','text':'Goodbye'},{'id':'a','text':'Hello'}]}),rows)


def test_vietnamese_diacritic_restoration_passes():
    rows = [{'id':'a','language':'vi','text':'Ke hoach chung toi la trien khai phan mem vao ngay 15 thang 12 nam nay voi ngan sach 250 trieu dong'},
            {'id':'b','language':'vi','text':'Nhung em van chua chac la doi ky thuat se hoan thanh dung han'}]
    edits,rejects = checked_edits(json.dumps({'segments':[
        {'id':'a','text':'Kế hoạch chung tôi là triển khai phần mềm vào ngày 15 tháng 12 năm nay với ngân sách 250 triệu đồng.'},
        {'id':'b','text':'Nhưng em vẫn chưa chắc là đội kỹ thuật sẽ hoàn thành đúng hạn.'}]}),rows)
    assert edits['a'] == 'Kế hoạch chung tôi là triển khai phần mềm vào ngày 15 tháng 12 năm nay với ngân sách 250 triệu đồng.'
    assert edits['b'] == 'Nhưng em vẫn chưa chắc là đội kỹ thuật sẽ hoàn thành đúng hạn.'
    assert rejects == []


def test_vietnamese_d_fold_is_required_for_bare_asr():
    rows = [{'id':'a','language':'vi','text':'Toi se de nghi phuong an nay vao dau gio va dung den cuoi tuan'}]
    edits,rejects = checked_edits(json.dumps({'segments':[
        {'id':'a','text':'Tôi sẽ đề nghị phương án này vào đầu giờ và dùng đến cuối tuần'}]}),rows)
    assert rejects == []
    assert edits['a'].startswith('Tôi sẽ đề nghị')


def test_vietnamese_word_substitutions_are_rejected_even_when_similar():
    rows = [{'id':'a','language':'vi','text':'Vay thi chang le anh de nghi em moi chung minh mot bua trua nay voi muc dich la trao doi ve viec hop tac du an nha'}]
    edits,rejects = checked_edits(json.dumps({'segments':[
        {'id':'a','text':'Và thì chắc là anh đề nghị em mới chứng minh một bữa trưa nay với mục đích là trao đổi về việc hợp tác dự án nhà'}]}),rows)
    assert edits == {'a':rows[0]['text']}
    assert rejects == [{'id':'a','reason':'Cleanup changed a Vietnamese word, not only its spelling'}]


def test_vietnamese_bare_diacritic_negation_spelling_passes():
    rows = [{'id':'a','language':'vi','text':'Chung ta chua quyet dinh ngay ra mat'}]
    edits,rejects = checked_edits(json.dumps({'segments':[
        {'id':'a','text':'Chúng ta chưa quyết định ngày ra mắt'}]}),rows)
    assert edits['a']=='Chúng ta chưa quyết định ngày ra mắt'
    assert rejects == []


@pytest.mark.parametrize('after', ['Chúng ta đã quyết định ngày ra mắt',
    'Chúng ta chưa quyết định được ngày ra mắt'])
def test_cleanup_preserves_negation_and_numeric_sign_vi(after):
    rows = [{'id':'a','language':'vi','text':'Chúng ta chua quyet dinh ngay ra mat'}]
    edits,rejects = checked_edits(json.dumps({'segments':[{'id':'a','text':after}]}),rows)
    assert edits == {'a':rows[0]['text']}
    assert [item['id'] for item in rejects] == ['a'] and rejects[0]['reason']


def test_cleanup_preserves_negation_and_numeric_sign_english():
    rows = [{'id':'a','text':'We have not approved this release'}]
    for after in ('We have approved this release','The balance is 25 dollars'):
        edits,rejects = checked_edits(json.dumps({'segments':[{'id':'a','text':after}]}),rows)
        assert edits == {'a':rows[0]['text']}
        assert [item['id'] for item in rejects] == ['a'] and rejects[0]['reason']


def test_english_small_word_fixes_pass_character_similarity():
    rows = [{'id':'a','text':'we has said tomorow that we will send the reports and the plan'}]
    edits,rejects = checked_edits(json.dumps({'segments':[
        {'id':'a','text':'we have said tomorrow that we will send the reports and the plan'}]}),rows)
    assert edits['a']=='we have said tomorrow that we will send the reports and the plan'
    assert rejects == []


def test_one_bad_segment_keeps_good_neighbors():
    rows = [{'id':'good','language':'vi','text':'Ke hoach chung toi la trien khai phan mem vao ngay 15 thang 12'},
            {'id':'bad','language':'vi','text':'Vay thi chang le anh de nghi em moi chung minh mot bua trua nay'}]
    edits,rejects = checked_edits(json.dumps({'segments':[
        {'id':'good','text':'Kế hoạch chung tôi là triển khai phần mềm vào ngày 15 tháng 12.'},
        {'id':'bad','text':'Và thì chắc là anh đề nghị em mới chứng minh một bữa trưa nay'}]}),rows)
    assert edits['good'] == 'Kế hoạch chung tôi là triển khai phần mềm vào ngày 15 tháng 12.'
    assert edits['bad'] == rows[1]['text']
    assert [item['id'] for item in rejects] == ['bad']


@pytest.mark.asyncio
async def test_cleanup_runs_before_completion_and_preserves_source_and_speaker(saved):
    store,sid,raw = saved
    events = []
    async def publish(event):
        events.append(event)
    editor = Editor()
    runner = LocalRecordingRunner(store,None,None,publish,
        config=LocalTranscriptionConfig(model='audio',cleanup_provider=editor))
    await runner._finish(sid)
    current = store.segments(sid)
    assert current[0]['text']==raw['text']+'.'
    assert current[0]['revision']==2
    assert current[0]['speaker_label']=='Nam'
    assert current[0]['local_speaker_id']==raw['local_speaker_id']
    assert (current[0]['start_ms'],current[0]['end_ms'],current[0]['stream'])==(100,1200,'mic')
    assert store.segments(sid,revision=1)[0]['text']==raw['text']
    assert store.get(sid)['status']=='completed'
    assert [event['type'] for event in events].index('transcript_replaced') < [event['type'] for event in events].index('notes')
    assert editor.calls[0][1]['$defs']['EditedSegment']['properties']['id']['enum']==[raw['id']]


@pytest.mark.asyncio
async def test_failed_part_keeps_its_original_text_and_the_rest_is_still_saved(saved):
    store,sid,raw = saved
    for i in range(4):
        store.append(sid,{'text':'Keep this original sentence','start_ms':2000+i*1000,
            'end_ms':2500+i*1000,'is_final':True},stream='mic')
    events = []
    async def publish(event):
        events.append(event)
    runner = LocalRecordingRunner(store,None,None,publish,
        config=LocalTranscriptionConfig(model='audio',cleanup_provider=Editor(fail_at=2)))
    await runner._finish(sid)
    assert store.get(sid)['status']=='completed'
    current = store.segments(sid)
    # The failed second part keeps its original wording; the first part is saved.
    assert len(store.revisions(sid))==2
    assert current[0]['text']==raw['text']+'.'
    assert current[-1]['text']=='Keep this original sentence'
    assert any(event['type']=='transcription_warning'
               and '1 of 2 part(s)' in event.get('message','') for event in events)
    recorded = store.conn.execute("SELECT payload_json FROM events WHERE session_id=? AND "
        "type='local_transcript_cleanup_failed'",(sid,)).fetchall()
    assert len(recorded)==1
    assert json.loads(recorded[0][0])['failed'][0]['batch']==2


@pytest.mark.asyncio
async def test_partial_batch_503_saves_cleaned_parts_and_persists_status(saved):
    import httpx
    from scribeloop_core.llm.transcript_cleanup import apply_cleanup
    store,sid,raw = saved
    for i in range(4):
        store.append(sid,{'text':f'Part two sentence {i+1}','start_ms':2000+i*1000,
            'end_ms':2500+i*1000,'is_final':True},stream='mic')
    class Flaky(Editor):
        async def generate(self,messages,*,schema):
            self.calls.append((messages,schema))
            if len(self.calls)==2:
                request=httpx.Request('POST','https://provider.example/v1/chat/completions')
                raise httpx.HTTPStatusError('Unavailable',request=request,
                    response=httpx.Response(503,request=request))
            rows = json.loads(messages[1]['content'])['untrusted_transcript']
            return json.dumps({'segments':[{'id':row['id'],'text':row['text']+'.'} for row in rows]})
    events = []
    async def publish(event): events.append(event)
    await apply_cleanup(store,sid,Flaky(),asr_model='test',publish=publish,cancelled=threading.Event())
    current = store.segments(sid)
    assert current[0]['text']==raw['text']+'.' and current[0]['revision']==2
    assert current[-1]['text']=='Part two sentence 4'
    assert any('1 of 2 part(s)' in e.get('message','') and 'HTTP 503' in e.get('message','') for e in events)
    recorded = store.conn.execute("SELECT payload_json FROM events WHERE session_id=? AND "
        "type='local_transcript_cleanup_failed'",(sid,)).fetchall()
    payload = json.loads(recorded[0][0])
    assert payload['batches_total']==2 and payload['failed'][0]['http_status']==503
    assert payload['revision']==2 and payload['model']=='local-text'


@pytest.mark.asyncio
async def test_total_batch_failure_records_event_and_keeps_transcript(saved):
    import httpx
    from scribeloop_core.llm.transcript_cleanup import apply_cleanup
    store,sid,raw = saved
    for i in range(4):
        store.append(sid,{'text':f'Part two sentence {i+1}','start_ms':2000+i*1000,
            'end_ms':2500+i*1000,'is_final':True},stream='mic')
    class Unavailable(Editor):
        async def generate(self,messages,*,schema):
            request=httpx.Request('POST','https://provider.example/v1/chat/completions')
            raise httpx.HTTPStatusError('Unavailable',request=request,
                response=httpx.Response(503,request=request))
    events = []
    async def publish(event): events.append(event)
    await apply_cleanup(store,sid,Unavailable(),asr_model='test',publish=publish,cancelled=threading.Event())
    assert store.segments(sid)[0]['text']==raw['text']
    assert len(store.revisions(sid))==1
    assert any('temporarily unavailable (HTTP 503)' in e.get('message','') for e in events)
    recorded = store.conn.execute("SELECT payload_json FROM events WHERE session_id=? AND "
        "type='local_transcript_cleanup_failed'",(sid,)).fetchall()
    assert len(recorded)==1
    payload = json.loads(recorded[0][0])
    assert payload['outcome']=='failed' and payload['error']=='HTTPStatusError'
    assert payload['http_status']==503


def test_cleanup_commit_is_atomic(saved):
    store,sid,raw = saved
    store.conn.execute("CREATE TRIGGER reject_cleanup BEFORE INSERT ON events WHEN NEW.type='local_transcript_cleanup' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    with pytest.raises(Exception,match='injected failure'):
        store.save_transcript_cleanup(sid,1,{raw['id']:raw['text']+'.'},asr_model='audio',cleanup_model='text')
    assert len(store.revisions(sid))==1
    assert store.segments(sid)[0]['text']==raw['text']
    assert store.segments(sid)[0]['speaker_label']=='Nam'


@pytest.mark.asyncio
async def test_cancelled_cleanup_makes_no_request():
    editor = Editor()
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        await clean_transcript([{'id':'a','text':'Hello'}],editor,cancelled)
    assert not editor.calls


@pytest.mark.asyncio
async def test_unconfigured_editor_is_rejected_before_dispatch():
    editor = Editor()
    editor.configured = False
    with pytest.raises(ValueError,match='configured'):
        await clean_transcript([{'id':'a','text':'Hello'}],editor,threading.Event())
    assert not editor.calls


@pytest.mark.asyncio
async def test_skip_validation_accepts_schema_deviations_without_content_checks():
    class Raw(Editor):
        async def generate(self,messages,*,schema):
            return '```json\n'+json.dumps({'explanation':'extra metadata','segments':[
                {'id':'unknown','text':'Ignore this unrelated ID'},
                {'id':'b','text':'No, we now need 900 reports','confidence':.8},
                {'id':'a','text':'Completely different wording','extra':True},
                {'id':'c','text':'Additional segment beyond the strict batch limit'},
                {'id':'d','text':'More text'},
                {'id':'e','text':'Even more text'}]})+'\n```'
    rows=[{'id':'a','text':'Send 25 reports'},{'id':'b','text':'We will attend'}]
    edits,rejects,_=await clean_transcript(rows,Raw(),threading.Event(),validate=False)
    assert edits=={'a':'Completely different wording','b':'No, we now need 900 reports'}
    assert rejects==[]


@pytest.mark.asyncio
async def test_skip_validation_saves_raw_revision_and_does_not_report_validation_failure(saved):
    from scribeloop_core.llm.transcript_cleanup import apply_cleanup
    store,sid,row=saved
    class Raw(Editor):
        cleanup_validate=False
        async def generate(self,messages,*,schema):
            return json.dumps({'extra':True,'segments':[
                {'id':row['id'],'text':'Nam will not send 900 reports','extra':True}]})
    events=[]
    async def publish(event): events.append(event)
    await apply_cleanup(store,sid,Raw(),asr_model='test-asr',publish=publish,cancelled=threading.Event())
    assert store.segments(sid)[0]['text']=='Nam will not send 900 reports'
    assert store.segments(sid)[0]['revision']==2
    assert any(e['type']=='transcript_replaced' for e in events)
    assert any('validation skipped' in e.get('message','') for e in events)
    assert not any('could not' in e.get('message','') for e in events)


@pytest.mark.asyncio
async def test_skip_validation_decode_failure_is_not_called_content_validation(saved):
    from scribeloop_core.llm.transcript_cleanup import apply_cleanup
    store,sid,row=saved
    class Broken(Editor):
        cleanup_validate=False
        async def generate(self,messages,*,schema): return 'not JSON'
    events=[]
    async def publish(event): events.append(event)
    await apply_cleanup(store,sid,Broken(),asr_model='test',publish=publish,cancelled=threading.Event())
    assert store.segments(sid)[0]['text']==row['text']
    assert any('content validation is disabled' in e.get('message','') for e in events)


@pytest.mark.asyncio
async def test_cleanup_503_reports_provider_unavailable_and_retains_transcript(saved):
    import httpx
    from scribeloop_core.llm.transcript_cleanup import apply_cleanup
    store,sid,row=saved
    class Unavailable(Editor):
        cleanup_validate=False
        async def generate(self,messages,*,schema):
            request=httpx.Request('POST','https://provider.example/v1/chat/completions')
            raise httpx.HTTPStatusError('Unavailable',request=request,response=httpx.Response(503,request=request))
    events=[]
    async def publish(event): events.append(event)
    await apply_cleanup(store,sid,Unavailable(),asr_model='test',publish=publish,cancelled=threading.Event())
    assert store.segments(sid)[0]['text']==row['text']
    assert any('temporarily unavailable (HTTP 503)' in e.get('message','') for e in events)
    assert not any('could not be validated' in e.get('message','') for e in events)


def test_contextual_vietnamese_word_repair_keeps_protected_facts():
    rows=[{'id':'a','language':'vi','text':'Chúng ta sẽ sửa con bọ trong phần mềm trước ngày 15.'}]
    raw=json.dumps({'segments':[{'id':'a','text':'Chúng ta sẽ sửa lỗi trong phần mềm trước ngày 15.',
        'context_segment_ids':['context']}]})
    edits,rejects=checked_edits(raw,rows,context_ids={'context'})
    assert edits['a']=='Chúng ta sẽ sửa lỗi trong phần mềm trước ngày 15.' and not rejects
    edits,rejects=checked_edits(raw,rows)
    assert edits['a']==rows[0]['text'] and rejects
    changed=json.loads(raw)
    changed['segments'][0]['text']=changed['segments'][0]['text'].replace('15','20')
    assert checked_edits(json.dumps(changed),rows,context_ids={'context'})[1]
    changed['segments'][0]['text']='Chúng ta không sửa lỗi trong phần mềm trước ngày 15.'
    assert checked_edits(json.dumps(changed),rows,context_ids={'context'})[1]


def test_unclear_cleanup_keeps_original_for_review():
    rows=[{'id':'a','text':'Discuss the unclear launch phrase.'}]
    edits,rejects=checked_edits(json.dumps({'segments':[{'id':'a','text':'Launch is approved.',
        'needs_review':True}]}),rows)
    assert edits['a']==rows[0]['text']
    assert 'review' in rejects[0]['reason']


def test_self_citation_does_not_unlock_contextual_repairs():
    rows=[{'id':'a','language':'vi','text':'Chúng ta sẽ sửa con bọ trong phần mềm trước ngày 15.'},
          {'id':'b','language':'vi','text':'Nhóm đang bàn về các lỗi phần mềm này.'}]
    # Citing only the row being edited is not evidence: the strict checks apply.
    raw=json.dumps({'segments':[
        {'id':'a','text':'Chúng ta sẽ sửa lỗi trong phần mềm trước ngày 15.','context_segment_ids':['a']},
        {'id':'b','text':'Nhóm đang bàn về các lỗi phần mềm này.'}]})
    edits,rejects=checked_edits(raw,rows)
    assert edits['a']==rows[0]['text']
    assert edits['b']=='Nhóm đang bàn về các lỗi phần mềm này.'
    assert rejects==[{'id':'a','reason':'Cleanup changed a Vietnamese word, not only its spelling'}]
    # A sibling citation still unlocks the contextual checks.
    raw=json.dumps({'segments':[
        {'id':'a','text':'Chúng ta sẽ sửa lỗi trong phần mềm trước ngày 15.','context_segment_ids':['b']},
        {'id':'b','text':'Nhóm đang bàn về các lỗi phần mềm này.'}]})
    edits,rejects=checked_edits(raw,rows)
    assert edits['a']=='Chúng ta sẽ sửa lỗi trong phần mềm trước ngày 15.'
    assert edits['b']=='Nhóm đang bàn về các lỗi phần mềm này.' and rejects == []


def test_word_count_slack_allows_merged_words_but_still_bounds_growth():
    rows=[{'id':'a','language':'vi','text':'Ke hoach chung toi gom nhieu ngânhàng khac nhau trong nam'}]
    merged='Kế hoạch chung tôi gồm nhiều ngân hàng khác nhau trong năm'
    edits,rejects=checked_edits(json.dumps({'segments':[
        {'id':'a','text':merged,'context_segment_ids':['ctx']}]}),rows,context_ids={'ctx'})
    assert edits['a']==merged and rejects == []
    grown='Kế hoạch chung của tôi gồm nhiều ngân hàng và nhiều mảng khác nhau trong năm nay'
    edits,rejects=checked_edits(json.dumps({'segments':[
        {'id':'a','text':grown,'context_segment_ids':['ctx']}]}),rows,context_ids={'ctx'})
    assert edits['a']==rows[0]['text']
    assert rejects==[{'id':'a','reason':'Cleanup changed the segment length too much'}]


def test_whitespace_only_edit_is_rejected():
    rows=[{'id':'a','text':'Send reports'}]
    edits,rejects=checked_edits(json.dumps({'segments':[{'id':'a','text':'   '}]}),rows)
    assert edits=={'a':'Send reports'}
    assert rejects==[{'id':'a','reason':'Cleanup changed the segment length too much'}]


def test_decomposed_vietnamese_input_compares_equal_to_the_repair():
    from scribeloop_core.llm.transcript_cleanup import _negation_forms
    assert _negation_forms('chu\u031ba') == _negation_forms('chưa') == ['chua']
    decomposed='Chung ta chu\u031ba quyet dinh ngay ra mat'
    rows=[{'id':'a','language':'vi','text':decomposed}]
    edits,rejects=checked_edits(json.dumps({'segments':[
        {'id':'a','text':'Chúng ta chưa quyết định ngày ra mắt'}]}),rows)
    assert edits['a']=='Chúng ta chưa quyết định ngày ra mắt' and rejects == []



@pytest.mark.asyncio
async def test_cleanup_receives_global_topic_vocabulary_and_real_neighbors():
    class ContextEditor(Editor):
        cleanup_vocabulary=('Northstar','deployment')
    provider=ContextEditor()
    rows=[{'id':str(i),'text':f'Discussion turn {i} about deployment.','start_ms':i*1000} for i in range(12)]
    await clean_transcript(rows,provider,threading.Event(),title='Northstar launch')
    payload=json.loads(provider.calls[1][0][1]['content'])
    assert [r['id'] for r in payload['context_before']]==['2','3']
    assert [r['id'] for r in payload['context_after']]==['8','9']
    assert payload['call_context']['meeting_title']=='Northstar launch'
    assert payload['call_context']['vocabulary']==['Northstar','deployment']
    assert payload['call_context']['source_excerpts'][0]['id']=='0'
    assert payload['call_context']['source_excerpts'][-1]['id']=='11'
    assert len(payload['call_context']['source_excerpts'])<=8


def test_original_asr_is_available_for_cleanup_review(saved):
    store,sid,row=saved
    store.save_transcript_cleanup(sid,1,{row['id']:'Nam will send 25 reports tomorrow.'},
        asr_model='test',cleanup_model='text')
    originals=store.cleanup_originals(sid,2)
    assert originals[0]['text']==row['text']
    assert store.cleanup_originals(sid,1)==[]


def test_uncertainty_review_flag_survives_noop_cleanup(saved):
    store,sid,row=saved
    assert store.save_transcript_cleanup(sid,1,{row['id']:row['text']},asr_model='test',
        cleanup_model='text',review_ids=[row['id']]) is None
    assert store.cleanup_originals(sid,1)[0]['cleanup_needs_review'] is True
