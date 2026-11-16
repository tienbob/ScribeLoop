"""Embedding bodies are bounded while reading, not after full buffering."""
import asyncio
import gzip

import httpx
import pytest
from scribeloop_core.storage import embeddings


@pytest.mark.parametrize('provider',['Custom','Gemini'])
@pytest.mark.parametrize('compressed',[False,True])
async def test_response_limit_stops_download_and_closes(monkeypatch,provider,compressed):
    monkeypatch.setattr(embeddings,'MAX_EMBEDDING_RESPONSE_BYTES',1024)
    observed={'blocks':0,'closed':False}
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            if compressed:
                observed['blocks']+=1;yield gzip.compress(b'x'*10000)
            else:
                for _ in range(100):observed['blocks']+=1;yield b'x'*512
        async def aclose(self):observed['closed']=True
    original=httpx.AsyncClient
    def client(**kwargs):
        headers={'Content-Encoding':'gzip'} if compressed else {}
        return original(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers=headers,stream=Body())),**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',client)
    config=embeddings.EmbeddingConfig(provider,
        'https://generativelanguage.googleapis.com/v1beta' if provider=='Gemini' else 'https://fake.example/v1',
        'gemini-embedding-001' if provider=='Gemini' else 'test',128,'fake-key')
    cls=embeddings.GeminiEmbeddingProvider if provider=='Gemini' else embeddings.CompatibleEmbeddingProvider
    with pytest.raises(ValueError,match='response too large'):await cls(config).embed(['test'])
    assert observed['closed'] and observed['blocks']<=3


async def test_whole_response_deadline_and_cancellation_close_stream(monkeypatch):
    monkeypatch.setattr(embeddings,'EMBEDDING_DEADLINE_S',.02)
    observed=[]
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b' '
            await asyncio.Event().wait()
        async def aclose(self):observed.append('closed')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Body()))) as client:
        with pytest.raises(TimeoutError):await embeddings._embedding_response(client,'https://fake.example')
        monkeypatch.setattr(embeddings,'EMBEDDING_DEADLINE_S',60)
        task=asyncio.create_task(embeddings._embedding_response(client,'https://fake.example'))
        await asyncio.sleep(.01);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert observed==['closed','closed']


async def test_normal_streamed_json_and_http_error_preserved():
    async def request(r):
        return httpx.Response(200,json={'data':[]}) if r.url.path=='/ok' else httpx.Response(429)
    async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
        assert await embeddings._embedding_response(client,'https://fake.example/ok')=={'data':[]}
        with pytest.raises(httpx.HTTPStatusError):await embeddings._embedding_response(client,'https://fake.example/error')
    assert 'quota' in embeddings.embedding_failure(httpx.HTTPStatusError('private-key',request=httpx.Request('POST','https://fake.example'),response=httpx.Response(429)))
