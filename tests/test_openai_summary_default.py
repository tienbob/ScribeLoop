"""OpenAI defaults, strict Responses parsing, and custom-endpoint key isolation."""
import json

import httpx
import pytest
from scribeloop_core.llm.summary_provider import SummaryProvider


def test_defaults_only_use_openai_key_for_the_exact_official_endpoint():
    provider=SummaryProvider(settings={'OPENAI_API_KEY':'test-openai-key'})
    assert provider.model=='gpt-5.4-mini' and provider.provider=='OpenAI'
    assert provider.public()['endpoint']=='https://api.openai.com/v1'
    assert provider.configured
    assert 'test-openai-key' not in json.dumps(provider.public())
    custom=SummaryProvider(settings={'OPENAI_API_KEY':'must-not-leave-openai','SUMMARY_BASE_URL':'https://custom.invalid/v1','SUMMARY_MODEL':'custom'})
    assert not custom.configured and not custom._api_key
    assert not SummaryProvider(settings={}).configured


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['completed','incomplete'])
async def test_openai_uses_bounded_responses_without_legacy_parameters(monkeypatch,status):
    original=httpx.AsyncClient
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url=='https://api.openai.com/v1/responses'
        body=json.loads(request.content)
        assert body['store'] is False and body['max_output_tokens']==4000
        assert 'temperature' not in body and 'max_tokens' not in body
        assert body['model']=='gpt-5.4-mini'
        return httpx.Response(200,json={'status':status,'output':[{'type':'message','content':[
            {'type':'output_text','text':'{"overview":"test"}'}]}]})
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx,'AsyncClient',client)
    provider=SummaryProvider(settings={'OPENAI_API_KEY':'test-key'})
    if status=='completed':
        assert await provider.generate([{'role':'user','content':'test'}])=='{"overview":"test"}'
    else:
        with pytest.raises(ValueError,match='incomplete'):
            await provider.generate([{'role':'user','content':'test'}])
    assert len(calls)==1
