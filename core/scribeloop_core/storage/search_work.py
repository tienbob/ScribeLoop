"""Request-scoped retrieval authorization and complete provider accounting."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class SearchAuthorizationError(ValueError):
    pass


@dataclass
class SearchWork:
    authorize: object = None
    started: float = field(default_factory=time.monotonic)
    embedding_requests: int = 0
    embedding_input_bytes: int = 0
    embedding_ms: int = 0
    answer_requests: int = 0
    answer_input_bytes: int = 0
    answer_ms: int = 0
    retrieval_ms: int = 0
    indexing_ms: int = 0

    def check(self):
        if self.authorize is not None:
            self.authorize()

    async def embed(self, provider, texts, *, query=False):
        self.check()
        self.embedding_requests += 1
        self.embedding_input_bytes += sum(len(text.encode('utf-8')) for text in texts)
        started=time.monotonic()
        try:
            call=getattr(provider,'embed_query',provider.embed) if query else provider.embed
            result=await call(texts)
        finally:
            self.embedding_ms += round((time.monotonic()-started)*1000)
        self.check()
        return result

    def public(self):
        return {'total_duration_ms':round((time.monotonic()-self.started)*1000),
                'retrieval_ms':self.retrieval_ms,'indexing_ms':self.indexing_ms,
                'embedding_ms':self.embedding_ms,'answer_ms':self.answer_ms,
                'embedding_requests':self.embedding_requests,'answer_requests':self.answer_requests,
                'total_requests':self.embedding_requests+self.answer_requests,
                'embedding_input_bytes':self.embedding_input_bytes,
                'answer_input_bytes':self.answer_input_bytes}
