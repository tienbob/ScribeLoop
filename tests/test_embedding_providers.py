import math
import struct

import pytest
from scribeloop_core.api.desktop_settings import Settings
from scribeloop_core.storage.embeddings import (
    CompatibleEmbeddingProvider,
    EmbeddingConfig,
    GeminiEmbeddingProvider,
    LocalEmbeddingProvider,
    bundled_embedding_model_dir,
    pack_embedding,
)


def test_embedding_generation_ignores_secret_but_binds_shape_and_destination():
    a=EmbeddingConfig('OpenAI','https://api.openai.com/v1','text-embedding-3-small',384,'secret-a')
    b=EmbeddingConfig('OpenAI','https://api.openai.com/v1','text-embedding-3-small',384,'secret-b')
    c=EmbeddingConfig('OpenAI','https://api.openai.com/v1','text-embedding-3-small',768,'secret-a')
    assert a.generation == b.generation
    assert a.generation != c.generation
    assert 'secret' not in a.generation


def test_local_embedding_contract_forbids_network_or_key():
    config=Settings(search_mode='hybrid',embedding_provider='Local',
        embedding_model='granite-embedding-97m-multilingual-r2',embedding_dimensions=384).embedding()
    assert config.provider == 'Local' and config.dimensions == 384
    with pytest.raises(ValueError):
        EmbeddingConfig('Local','https://example.com','model',384,'key')


def test_embedding_settings_restore_keys_without_leaking_to_changed_destination():
    from scribeloop_core.api.desktop_settings import DesktopSettings
    class Vault:
        value=None
        def get_password(self,*_): return self.value
        def set_password(self,*args): self.value=args[-1]
    settings=DesktopSettings(Vault())
    settings.save({'search_mode':'hybrid','embedding_provider':'OpenAI',
                   'embedding_model':'text-embedding-3-small','embedding_dimensions':384,'embedding_key':'test-openai-secret'})
    public=settings.value.public()
    assert 'test-openai-secret' not in str(public)
    settings.save({'embedding_provider':'Local','embedding_model':'granite-embedding-97m-multilingual-r2'})
    assert settings.value.embedding_key == ''
    settings.save({'embedding_provider':'OpenAI'})
    assert settings.value.embedding_key == 'test-openai-secret'
    with pytest.raises(ValueError):
        settings.prepare({'embedding_provider':'Custom','embedding_endpoint':'https://other.example/v1'})


def test_local_embedding_shape_cannot_be_edited():
    with pytest.raises(ValueError,match='384 dimensions'):
        Settings(search_mode='hybrid',embedding_provider='Local',
            embedding_model='granite-embedding-97m-multilingual-r2',embedding_dimensions=768).embedding()


def test_custom_embedding_key_is_bound_to_its_saved_endpoint():
    from scribeloop_core.api.desktop_settings import DesktopSettings
    class Vault:
        def get_password(self,*_): return None
    settings=DesktopSettings(Vault())
    settings.value=settings.prepare({'search_mode':'hybrid','embedding_provider':'Custom',
        'embedding_endpoint':'https://first.example/v1','embedding_model':'embed','embedding_key':'custom-secret'})
    settings.value=settings.prepare({'embedding_provider':'Local','embedding_model':'granite-embedding-97m-multilingual-r2'})
    with pytest.raises(ValueError,match='endpoint or key'):
        settings.prepare({'embedding_provider':'Custom','embedding_model':'embed',
                          'embedding_endpoint':'https://second.example/v1'})


def test_pack_embedding_normalizes_and_rejects_shape_changes():
    packed=pack_embedding([3,4],2)
    values=struct.unpack('2f',packed)
    assert math.isclose(sum(value*value for value in values),1,rel_tol=1e-6)
    with pytest.raises(ValueError,match='different vector shape'):
        pack_embedding([1],2)


@pytest.mark.asyncio
async def test_compatible_provider_orders_response_and_sends_dimensions_once():
    seen=[]
    config=EmbeddingConfig('OpenAI','https://api.openai.com/v1','text-embedding-3-small',32,'test-key')
    # Use a compact fake response while retaining the real 32-dimensional contract.
    async def shaped(body):
        seen.append(body)
        one=[0.0]*32; one[0]=1
        two=[0.0]*32; two[1]=1
        return {'data':[{'index':1,'embedding':two},{'index':0,'embedding':one}]}
    vectors=await CompatibleEmbeddingProvider(config,request=shaped).embed(['hello','xin chào'])
    assert len(vectors)==2 and all(len(value)==128 for value in vectors)
    assert seen[0]['dimensions']==32


@pytest.mark.asyncio
async def test_compatible_provider_rejects_provider_shape_drift():
    config=EmbeddingConfig('OpenAI','https://api.openai.com/v1','model',32,'test-key')
    async def request(_):
        return {'data':[{'index':0,'embedding':[1,0]}]}
    with pytest.raises(ValueError,match='different vector shape'):
        await CompatibleEmbeddingProvider(config,request=request).embed(['hello'])


@pytest.mark.asyncio
async def test_real_bundled_model_cross_language_retrieval_when_assets_present():
    if not (bundled_embedding_model_dir()/'model_quint8_avx2.onnx').is_file():
        pytest.skip('Pinned local model assets are installed only for desktop builds')
    from scribeloop_core.storage.db import connect, migrate
    from scribeloop_core.storage.search import MeetingSearch
    from scribeloop_core.storage.sessions import SessionStore
    from test_meeting_search import meeting
    store=SessionStore(migrate(connect(':memory:')))
    relevant=meeting(store,'Thiết kế',['Ứng dụng hoạt động như một trợ lý ghi chú cuộc họp, dùng plugin để gửi email và đặt lịch.'])
    meeting(store,'Thời tiết',['Ngày mai trời sẽ mưa và đường phố bị ngập.'])
    config=Settings(search_mode='hybrid',embedding_provider='Local',
        embedding_model='granite-embedding-97m-multilingual-r2',embedding_dimensions=384).embedding()
    provider=LocalEmbeddingProvider(config)
    result=await MeetingSearch(store,embedding_config=config,embedder=provider).query_async(
        'How does the meeting-notes application work?',match_all=False)
    assert result['retrieval_mode']=='hybrid'
    assert result['results'][0]['session_id']==relevant
    assert result['index']['semantic']['dimensions']==384


@pytest.mark.asyncio
async def test_gemini_default_requests_correct_dimension_and_retrieval_task():
    settings=Settings(search_mode='hybrid',summary_provider='Gemini',
        summary_endpoint='https://generativelanguage.googleapis.com/v1beta/openai',summary_key='saved-gemini-key')
    config=settings.embedding()
    assert config.provider=='Gemini' and config.model=='gemini-embedding-001' and config.dimensions==768
    assert config.api_key=='saved-gemini-key'
    assert 'saved-gemini-key' not in str(settings.public())
    seen=[]
    async def request(body):
        seen.append(body)
        return {'embeddings':[{'values':[1.0]+[0.0]*767} for _ in body['requests']]}
    provider=GeminiEmbeddingProvider(config,request=request)
    assert len((await provider.embed(['Nội dung cuộc họp']))[0])==768*4
    await provider.embed_query(['What did we agree?'])
    assert seen[0]['requests'][0]['taskType']=='RETRIEVAL_DOCUMENT'
    assert seen[1]['requests'][0]['taskType']=='RETRIEVAL_QUERY'
    assert seen[1]['requests'][0]['outputDimensionality']==768


def test_default_embedding_provider_does_not_enable_network_search():
    settings=Settings()
    assert settings.embedding_provider=='Gemini' and settings.search_mode=='keyword'
    assert settings.embedding() is None


def test_gemini_notes_key_cannot_be_inherited_by_a_custom_embedding_destination():
    with pytest.raises(ValueError,match='endpoint or key'):
        Settings(search_mode='hybrid',embedding_provider='Custom',
            embedding_endpoint='https://other.example/v1',summary_provider='Gemini',
            summary_key='saved-gemini-key').embedding()


def test_enabled_gemini_search_keeps_key_when_notes_provider_changes():
    from scribeloop_core.api.desktop_settings import DesktopSettings
    class Vault:
        def get_password(self,*_): return None
    settings=DesktopSettings(Vault())
    settings.value=settings.prepare({'summary_provider':'Gemini','summary_model':'gemini-test',
        'summary_key':'saved-gemini-key','search_mode':'hybrid'})
    assert settings.value.embedding_key=='saved-gemini-key'
    changed=settings.prepare({'summary_provider':'OpenAI'})
    assert changed.embedding().api_key=='saved-gemini-key'
