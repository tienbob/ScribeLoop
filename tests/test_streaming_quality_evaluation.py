"""Comparison protocol and verified-reference gating, without network access."""
import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
from scribeloop_core.evaluation.streaming_quality import score_verified, stream_trial


def test_scores_require_human_verified_reference_and_success():
    result={'status':'completed','text':'xin chào bạn'}
    assert score_verified({'text':'xin chào','human_verified':False},result) is None
    assert score_verified({'text':'','human_verified':True},result) is None
    assert score_verified({'text':'xin chào','human_verified':True},{**result,'status':'failed'}) is None
    assert score_verified({'text':'xin chào','human_verified':True},result)=={
        'word_errors':1,'reference_words':2,'wer':.5}


@pytest.mark.asyncio
@pytest.mark.parametrize('provider',['deepgram','soniox'])
async def test_trial_sends_correct_configuration_and_counts_only_final_text(provider):
    seen={}
    class Socket:
        sent=0
        def __init__(self): self.done=asyncio.Event()
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def send(self,value):
            if isinstance(value,bytes): self.sent+=len(value)
            elif value=='': self.done.set()
            else:
                config=json.loads(value)
                if config.get('type')=='CloseStream': self.done.set()
                else: seen['config']=config
        async def __aiter__(self):
            await self.done.wait()
            if provider=='deepgram':
                yield json.dumps({'type':'Results','is_final':False,'channel':{'alternatives':[{'transcript':'wrong'}]}})
                yield json.dumps({'type':'Results','is_final':True,'channel':{'alternatives':[
                    {'transcript':'xin chào','words':[{'word':'xin','language':'vi'}]}]}})
                yield json.dumps({'type':'Metadata','duration':self.sent/32000})
            else:
                yield json.dumps({'tokens':[{'text':'wrong','is_final':False}]})
                yield json.dumps({'tokens':[{'text':'xin chào','is_final':True,'language':'vi'}],
                                  'finished':True,'total_audio_proc_ms':self.sent/32})
    def connector(url,**kwargs):
        seen['url']=url; seen['headers']=kwargs['additional_headers']; return Socket()
    result=await stream_trial(provider,np.zeros(320,dtype=np.float32),api_key='synthetic',
                              languages=['en','vi'],vocabulary=['exception'],connector=connector)
    assert result['status']=='completed' and result['text']=='xin chào'
    assert result['sent_audio_s']==.02
    assert 'synthetic' not in json.dumps(result)
    if provider=='deepgram':
        params=parse_qs(urlsplit(seen['url']).query)
        assert params['keyterm']==['exception'] and params['language_hint']==['en','vi']
    else:
        assert seen['config']['language_hints_strict'] is True
        assert seen['config']['context']['terms']==['exception']


@pytest.mark.asyncio
async def test_trial_rejects_oversized_input_before_connection():
    def forbidden(*args,**kwargs): raise AssertionError('Unexpected connection')
    with pytest.raises(ValueError):
        await stream_trial('deepgram',np.zeros(60*16000+1),api_key='test',languages=['en'],connector=forbidden)


def test_phrase_score_excludes_unverified_rest_of_call():
    result={'provider':'deepgram','status':'completed','sent_audio_s':60,'text':'unverified red green blue yellow okay',
        'words':[{'word':'unverified','start':1,'end':2},
                 *[{'word':word,'start':48+i*.3,'end':48.2+i*.3} for i,word in enumerate(['red','green','blue','yellow'])],
                 {'word':'okay','start':55,'end':56}]}
    reference={'human_verified':True,'text':'alpha beta gamma delta','start_s':78,'end_s':81}
    score=score_verified(reference,result,clip_start_s=30)
    assert score['word_errors']==4 and score['reference_words']==4 and score['wer']==1
    assert score['scope']=='verified_phrase'
    assert score_verified(reference,{**result,'words':[]},clip_start_s=30)['word_errors']==4
    with pytest.raises(ValueError):
        score_verified({**reference,'end_s':91},result,clip_start_s=30)


@pytest.mark.asyncio
async def test_provider_error_is_redacted_and_never_retried():
    calls=[]
    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def send(self,data): pass
        async def __aiter__(self):
            yield json.dumps({'error_code':402,'error_type':'organization_balance_exhausted',
                              'error_message':'must-not-leak-private-response'})
    def connector(*args,**kwargs): calls.append(1); return Socket()
    result=await stream_trial('soniox',np.zeros(320),api_key='synthetic-key',languages=['en'],connector=connector)
    assert len(calls)==1 and result['status']=='failed'
    assert result['provider_error_type']=='organization_balance_exhausted'
    assert result['provider_error_code']==402 and 'must-not-leak' not in json.dumps(result)
