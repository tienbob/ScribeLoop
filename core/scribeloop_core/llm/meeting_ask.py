"""Bounded, revision-aware local evidence retrieval for Ask meetings."""
from __future__ import annotations

from dataclasses import dataclass

from ..storage.search import MeetingSearch

MAX_PASSAGES = 20
MAX_EVIDENCE_BYTES = 48 * 1024
MAX_QUESTION_CHARS = 500
MAX_SELECTED_MEETINGS = 100


@dataclass(frozen=True)
class AskScope:
    """An explicit server-enforced meeting scope."""

    kind: str
    meeting_ids: tuple[str, ...]

    @classmethod
    def resolve(cls, store, kind, *, current_id=None, selected_ids=None):
        if kind not in {'current','selected','all'}:
            raise ValueError('Invalid Ask scope')
        if kind == 'current':
            ids=(current_id,) if isinstance(current_id,str) else ()
        elif kind == 'selected':
            if not isinstance(selected_ids,(list,tuple)):
                raise ValueError('Choose meetings to ask')
            ids=tuple(dict.fromkeys(selected_ids))
        else:
            ids=tuple(row['id'] for row in store.conn.execute(
                'SELECT id FROM sessions ORDER BY started_at DESC,id DESC'))
        if (not ids or (kind == 'selected' and len(ids)>MAX_SELECTED_MEETINGS) or
                any(not isinstance(value,str) for value in ids)):
            raise ValueError('Ask scope must contain meetings; chosen scope is limited to 100')
        if kind != 'all' and any(not store.get(value) for value in ids):
            raise ValueError('Ask scope contains an unavailable meeting')
        return cls(kind,ids)


def _truncate_utf8(value: str, limit: int) -> tuple[str,bool]:
    raw=value.encode('utf-8')
    if len(raw)<=limit:
        return value,False
    return raw[:limit].decode('utf-8','ignore'),True


class MeetingEvidence:
    """Build the only evidence packet that an Ask generation may receive."""

    def __init__(self, store):
        self.store=store

    async def retrieve(self, question: str, scope: AskScope):
        if not isinstance(question,str) or not 1<=len(question.strip())<=MAX_QUESTION_CHARS:
            raise ValueError('Enter a question from 1 to 500 characters')
        if not isinstance(scope,AskScope):
            raise TypeError('Invalid Ask scope')
        meeting_ids=None if scope.kind == 'all' else list(scope.meeting_ids)
        result=await MeetingSearch(self.store).query_async(question.strip(),limit=MAX_PASSAGES,
                                                            meeting_ids=meeting_ids,match_all=False)
        passages=[]; used=0; truncated=result['next_offset'] is not None
        for hit in result['results']:
            # Titles help discovery but cannot support a factual meeting answer.
            if hit['source_type'] == 'title':
                continue
            row=self.store.conn.execute('SELECT content FROM search_documents WHERE id=? AND session_id=? AND revision=?',
                                        (hit['id'],hit['session_id'],hit['revision'])).fetchone()
            if not row:
                continue
            remaining=MAX_EVIDENCE_BYTES-used
            if remaining<=0:
                truncated=True
                break
            content,cut=_truncate_utf8(row['content'],remaining)
            if not content:
                truncated=True
                break
            used+=len(content.encode('utf-8'))
            passages.append({'citation_id':hit['id'],'meeting_id':hit['session_id'],
                'meeting_title':hit['title'],'revision':hit['revision'],
                'source_type':hit['source_type'],'source_id':hit['source_id'],
                'start_ms':hit['start_ms'],'end_ms':hit['end_ms'],
                'speaker_id':hit['speaker_id'],'speaker_name':hit['speaker_name'],
                'text':content})
            truncated=truncated or cut
            if cut:
                break
        return {'question':question.strip(),'scope':{'kind':scope.kind,'meeting_ids':list(scope.meeting_ids)},
                'passages':passages,'passage_count':len(passages),'evidence_bytes':used,
                'truncated':truncated,'sufficient':bool(passages),'index':result['index']}
