"""Explicit, shape-safe embedding providers for hybrid meeting retrieval."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    endpoint: str
    model: str
    dimensions: int
    api_key: str = field(default='',repr=False)
    normalization: str = 'l2'
    implementation: str = 'v1'

    def __post_init__(self):
        if self.provider not in {'Local','OpenAI','Ollama','LM Studio','Custom'}:
            raise ValueError('Unsupported embedding provider')
        if not 32 <= self.dimensions <= 4096 or not 1 <= len(self.model) <= 160:
            raise ValueError('Invalid embedding model shape')
        if self.normalization != 'l2':
            raise ValueError('Unsupported embedding normalization')
        if self.provider == 'Local':
            if self.endpoint or self.api_key:
                raise ValueError('Local embeddings cannot use a network credential')
            if self.model != 'granite-embedding-97m-multilingual-r2' or self.dimensions != 384:
                raise ValueError('The bundled multilingual model requires 384 dimensions')
            return
        url=urlsplit(self.endpoint.rstrip('/'))
        try:
            loopback=ipaddress.ip_address(url.hostname or '').is_loopback
        except ValueError:
            loopback=url.hostname == 'localhost'
        valid=(url.scheme in {'https','http'} and url.hostname and
               (url.scheme == 'https' or loopback) and not url.username and not url.password and
               not url.query and not url.fragment and (self.api_key or loopback))
        _=url.port
        if not valid:
            raise ValueError('Invalid embedding endpoint or key')
        if self.provider in {'Ollama','LM Studio'} and not loopback:
            raise ValueError('Local embedding providers require a loopback endpoint')
        if any(ord(c)<32 for c in self.endpoint+self.model+self.api_key):
            raise ValueError('Invalid embedding configuration')

    @property
    def generation(self) -> str:
        # Endpoint identity matters, but never persist a credential in the DB.
        identity=json.dumps({'provider':self.provider,'endpoint':self.endpoint.rstrip('/'),
            'model':self.model,'dimensions':self.dimensions,'normalization':self.normalization,
            'implementation':self.implementation},sort_keys=True,separators=(',',':'))
        return hashlib.sha256(identity.encode()).hexdigest()


def pack_embedding(values,dimensions: int) -> bytes:
    if not isinstance(values,list) or len(values) != dimensions:
        raise ValueError('Embedding provider returned a different vector shape')
    if any(type(value) not in {float,int} for value in values):
        raise ValueError('Embedding provider returned invalid vector values')
    try:
        vector=[float(value) for value in values]
    except (TypeError,ValueError,OverflowError):
        raise ValueError('Embedding provider returned invalid vector values') from None
    if any(not math.isfinite(value) for value in vector):
        raise ValueError('Embedding provider returned invalid vector values')
    norm=math.sqrt(sum(value*value for value in vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError('Embedding provider returned an empty vector')
    return struct.pack(f'{dimensions}f',*(value/norm for value in vector))


class CompatibleEmbeddingProvider:
    """One bounded OpenAI-compatible `/embeddings` request; never retries."""

    def __init__(self,config: EmbeddingConfig,*,request=None):
        if config.provider == 'Local':
            raise ValueError('Use the bundled local embedding provider')
        self.config=config
        self._request=request

    async def embed(self,texts: list[str]) -> list[bytes]:
        if (not isinstance(texts,list) or not 1 <= len(texts) <= 64 or
                any(not isinstance(text,str) or not text.strip() or len(text)>8000 for text in texts) or
                len(json.dumps(texts,ensure_ascii=False).encode())>128_000):
            raise ValueError('Invalid embedding batch')
        body={'model':self.config.model,'input':texts,'encoding_format':'float'}
        if self.config.provider == 'OpenAI' and self.config.model.startswith('text-embedding-3'):
            body['dimensions']=self.config.dimensions
        if self._request:
            payload=await self._request(body)
        else:
            headers={'Authorization':'Bearer '+self.config.api_key} if self.config.api_key else {}
            async with httpx.AsyncClient(timeout=httpx.Timeout(60,connect=10),trust_env=False,
                                         follow_redirects=False) as client:
                response=await client.post(self.config.endpoint.rstrip('/')+'/embeddings',json=body,headers=headers)
                response.raise_for_status()
                if len(response.content)>16_000_000:
                    raise ValueError('Embedding response too large')
                payload=response.json()
        rows=payload.get('data') if isinstance(payload,dict) else None
        if not isinstance(rows,list) or len(rows)!=len(texts):
            raise ValueError('Embedding provider returned an invalid batch')
        ordered=sorted(rows,key=lambda row:row.get('index',-1) if isinstance(row,dict) else -1)
        if [row.get('index') for row in ordered] != list(range(len(texts))):
            raise ValueError('Embedding provider returned invalid indexes')
        return [pack_embedding(row.get('embedding'),self.config.dimensions) for row in ordered]


def bundled_embedding_model_dir() -> Path:
    frozen=Path(getattr(sys,'_MEIPASS',''))/'scribeloop_models/granite-embedding-97m-multilingual-r2'
    if getattr(sys,'frozen',False):
        return frozen
    return Path(__file__).resolve().parents[3]/'models/granite-embedding-97m-multilingual-r2'


@lru_cache(maxsize=1)
def _local_runtime(directory: str):
    import onnxruntime as ort
    from tokenizers import Tokenizer
    options=ort.SessionOptions()
    options.intra_op_num_threads=2
    options.inter_op_num_threads=1
    tokenizer=Tokenizer.from_file(str(Path(directory)/'tokenizer.json'))
    session=ort.InferenceSession(str(Path(directory)/'model_quint8_avx2.onnx'),
                                sess_options=options,providers=['CPUExecutionProvider'])
    return tokenizer,session


class LocalEmbeddingProvider:
    """CPU-only official IBM Granite INT8 ONNX embedding runtime."""

    def __init__(self,config: EmbeddingConfig,*,model_dir: Path | None=None):
        if config.provider != 'Local' or config.dimensions != 384 or config.model != 'granite-embedding-97m-multilingual-r2':
            raise ValueError('Unsupported bundled embedding model or shape')
        directory=Path(model_dir) if model_dir else bundled_embedding_model_dir()
        model=directory/'model_quint8_avx2.onnx'; tokenizer=directory/'tokenizer.json'
        if not model.is_file() or not tokenizer.is_file():
            raise FileNotFoundError('Bundled multilingual search model is unavailable')
        self.config=config
        self._tokenizer,self._session=_local_runtime(str(directory))

    async def embed(self,texts: list[str]) -> list[bytes]:
        import asyncio
        return await asyncio.to_thread(self._embed,texts)

    def _embed(self,texts: list[str]) -> list[bytes]:
        import numpy as np
        if not isinstance(texts,list) or not 1 <= len(texts) <= 64:
            raise ValueError('Invalid embedding batch')
        encoded=[self._tokenizer.encode(text[:8000]) for text in texts]
        if any(not text.strip() for text in texts):
            raise ValueError('Invalid embedding text')
        length=min(512,max(len(row.ids) for row in encoded))
        ids=np.zeros((len(encoded),length),dtype=np.int64)
        mask=np.zeros_like(ids)
        for index,row in enumerate(encoded):
            values=row.ids[:length]; attention=row.attention_mask[:length]
            ids[index,:len(values)]=values; mask[index,:len(attention)]=attention
        output=self._session.run(None,{'input_ids':ids,'attention_mask':mask})[0]
        return [pack_embedding(row[0].tolist(),self.config.dimensions) for row in output]
