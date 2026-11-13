"""Revision-aware, encrypted local meeting search.

The canonical documents live inside the SQLCipher workspace.  FTS5 is used when
the linked SQLite build provides it; a bounded LIKE fallback keeps search usable
and explicitly reports its reduced capability.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata

from .db import DATABASE_ERRORS
from .embeddings import CompatibleEmbeddingProvider, GeminiEmbeddingProvider, LocalEmbeddingProvider
from .search_work import SearchAuthorizationError, SearchWork
from .vectors import SqliteVecIndex

INDEX_VERSION = 4
QUERY_STOPWORDS = {'a','an','and','are','about','did','do','does','for','from','how','i','in','is','it',
    'of','on','our','the','to','was','we','were','what','when','where','which','who','why','with','you',
    'ai','cái','có','đã','được','gì','khi','là','mà','nào','những','ở','ta','thế','và','về',
    'thì','để','này','đó','các','một','như','hay','không','cho'}


def retrieval_query(value):
    terms=[term for term in re.findall(r'[\wÀ-ỹ]+',value.casefold())
           if len(term)>1 and term not in QUERY_STOPWORDS]
    return ' '.join(dict.fromkeys(terms)) or value.strip()


def _fold(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(c))


def _summary_text(value) -> str:
    parts: list[str] = []
    def visit(node):
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for key, item in node.items():
                if key not in {'id', 'citation_ids', 'citations', 'source_ids', 'segment_ids'} and not key.endswith('_id'):
                    visit(item)
    visit(value)
    return '\n'.join(dict.fromkeys(part.strip() for part in parts if part.strip()))


class MeetingSearch:
    """Synchronize selected revisions and run bounded local queries."""

    def __init__(self, store, *, embedding_config=None, embedder=None, include_titles=True, work=None):
        self.store = store
        self.conn = store.conn
        self.fts = self._ensure_fts()
        self.embedding_config=embedding_config
        self.embedder=embedder
        self.include_titles=include_titles
        self._owns_work=work is None
        self.work=work or SearchWork()
        if embedding_config is not None and embedder is None:
            try:
                self.embedder=(LocalEmbeddingProvider(embedding_config) if embedding_config.provider == 'Local' else
                    GeminiEmbeddingProvider(embedding_config) if embedding_config.provider == 'Gemini' else
                    CompatibleEmbeddingProvider(embedding_config))
            except (FileNotFoundError,ImportError):
                self.embedder=None

    def _ensure_fts(self) -> bool:
        try:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts USING fts5("
                              "content, title, speaker_name, content='search_documents', content_rowid='rowid', "
                              "tokenize='unicode61 remove_diacritics 2')")
            return True
        except DATABASE_ERRORS:  # SQLite/SQLCipher builds may omit FTS5.
            return False

    def _fingerprint(self, session_id: str) -> str:
        row = self.conn.execute('SELECT title,started_at FROM sessions WHERE id=?',(session_id,)).fetchone()
        revision = self.conn.execute('SELECT MAX(revision) FROM segments WHERE session_id=?',(session_id,)).fetchone()[0]
        versions = list(self.conn.execute('SELECT bucket,generation FROM summary_source_versions '
                                          'WHERE session_id=? ORDER BY bucket',(session_id,)))
        summary = self.conn.execute('SELECT id,revision,input_hash FROM grounded_summaries WHERE session_id=? '
                                    'ORDER BY created_at DESC,rowid DESC LIMIT 1',(session_id,)).fetchone()
        provisional = self.conn.execute('SELECT id,created_at FROM summaries WHERE session_id=? '
                                        'ORDER BY created_at DESC,rowid DESC LIMIT 1',(session_id,)).fetchone()
        raw = json.dumps([INDEX_VERSION,list(row) if row else None,revision,[list(v) for v in versions],
                          list(summary) if summary else None,list(provisional) if provisional else None],
                         ensure_ascii=False,separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def _documents(self, session_id: str):
        session = self.store.get(session_id)
        if not session:
            return []
        revision = session['transcript_revision']
        common = {'session_id':session_id,'started_at':session['started_at'],'title':session['title']}
        docs = [{**common,'id':f'{session_id}:title','revision':revision,'source_type':'title',
                 'source_id':session_id,'start_ms':None,'end_ms':None,'speaker_id':None,
                 'speaker_name':None,'content':session['title']}]
        if revision:
            for row in self.store.segments(session_id,revision=revision):
                docs.append({**common,'id':f"{session_id}:r{revision}:segment:{row['id']}",
                    'revision':revision,'source_type':'transcript','source_id':row['id'],
                    'start_ms':row['start_ms'],'end_ms':row['end_ms'],
                    'speaker_id':row.get('display_speaker_id'),
                    'speaker_name':row.get('speaker_label'),'content':row['text']})
        summary = self.store.grounded_summary(session_id)
        if summary and summary.get('revision') == revision and not summary.get('stale'):
            content = _summary_text(summary.get('summary',{}))
            if content:
                docs.append({**common,'id':f"{session_id}:r{revision}:summary:{summary['id']}",
                    'revision':revision,'source_type':'summary','source_id':summary['id'],
                    'start_ms':None,'end_ms':None,'speaker_id':None,'speaker_name':None,'content':content})
        return docs

    def _changed(self):
        changed=[]
        for row in self.conn.execute('SELECT id FROM sessions ORDER BY started_at DESC,id DESC'):
            fingerprint=self._fingerprint(row['id'])
            known=self.conn.execute('SELECT fingerprint FROM search_index_state WHERE session_id=?',(row['id'],)).fetchone()
            if not known or known['fingerprint'] != fingerprint:
                changed.append((row['id'],fingerprint))
        return changed

    def _sync_session(self, session_id, fingerprint):
        docs=self._documents(session_id)
        with self.conn:
            old_rows=[r[0] for r in self.conn.execute('SELECT rowid FROM search_documents WHERE session_id=?',(session_id,))]
            if self.fts:
                for rowid in old_rows:
                    self.conn.execute("INSERT INTO search_documents_fts(search_documents_fts,rowid,content,title,speaker_name) "
                                      "SELECT 'delete',rowid,content,CASE WHEN source_type='title' THEN title ELSE '' END,"
                                      "COALESCE(speaker_name,'') FROM search_documents WHERE rowid=?",(rowid,))
            self.conn.execute('DELETE FROM search_documents WHERE session_id=?',(session_id,))
            for doc in docs:
                fields=tuple(doc)
                cur=self.conn.execute(f"INSERT INTO search_documents ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                                      tuple(doc.values()))
                if self.fts:
                    self.conn.execute('INSERT INTO search_documents_fts(rowid,content,title,speaker_name) VALUES (?,?,?,?)',
                                      (cur.lastrowid,doc['content'],
                                       doc['title'] if doc['source_type']=='title' else '',
                                       doc['speaker_name'] or ''))
            self.conn.execute('INSERT INTO search_index_state(session_id,fingerprint,indexed_at,document_count) '
                              'VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                              'fingerprint=excluded.fingerprint,indexed_at=excluded.indexed_at,document_count=excluded.document_count',
                              (session_id,fingerprint,int(time.time()*1000),len(docs)))

    def sync(self, *, limit: int | None = None):
        changed=self._changed()
        selected=changed if limit is None else changed[:limit]
        for session_id,fingerprint in selected:
            self._sync_session(session_id,fingerprint)
        return {'complete':len(selected)==len(changed),'pending':len(changed)-len(selected),
                'indexed_meetings':self.conn.execute('SELECT COUNT(*) FROM search_index_state').fetchone()[0],
                'engine':'fts5' if self.fts else 'basic'}

    async def sync_async(self, scope=None):
        """Reconcile on the owning DB thread while yielding between meetings."""
        changed=[]
        where=(' WHERE id IN ('+','.join('?' for _ in scope)+')') if scope is not None else ''
        for index,row in enumerate(self.conn.execute('SELECT id FROM sessions'+where+' ORDER BY started_at DESC,id DESC',scope or ())):
            self.work.check()
            fingerprint=self._fingerprint(row['id'])
            known=self.conn.execute('SELECT fingerprint FROM search_index_state WHERE session_id=?',(row['id'],)).fetchone()
            if not known or known['fingerprint'] != fingerprint:
                changed.append((row['id'],fingerprint))
            if index % 25 == 24:
                await asyncio.sleep(0)
        for session_id,fingerprint in changed:
            self.work.check()
            self._sync_session(session_id,fingerprint)
            await asyncio.sleep(0)
        self.work.check()
        return {'complete':True,'pending':0,
                'indexed_meetings':self.conn.execute('SELECT COUNT(*) FROM search_index_state').fetchone()[0],
                'engine':'fts5' if self.fts else 'basic'}

    def purge_session(self, session_id: str) -> None:
        """Remove index text before the owning encrypted meeting is deleted."""
        rows=list(self.conn.execute("SELECT rowid,content,CASE WHEN source_type='title' THEN title ELSE '' END,"
                                    "COALESCE(speaker_name,'') "
                                    'FROM search_documents WHERE session_id=?',(session_id,)))
        with self.conn:
            if self.fts:
                for row in rows:
                    self.conn.execute("INSERT INTO search_documents_fts(search_documents_fts,rowid,content,title,speaker_name) "
                                      "VALUES ('delete',?,?,?,?)",tuple(row))
            self.conn.execute('DELETE FROM search_documents WHERE session_id=?',(session_id,))
            self.conn.execute('DELETE FROM search_index_state WHERE session_id=?',(session_id,))

    def query(self, query: str, *, limit=50, offset=0, meeting_id=None,
              meeting_ids=None, person_id=None, date_from=None, date_to=None, match_all=True):
        self._validate_query(query,limit,offset)
        return self._query(query,self.sync(),limit=limit,offset=offset,meeting_id=meeting_id,
                           meeting_ids=meeting_ids,person_id=person_id,date_from=date_from,date_to=date_to,
                           match_all=match_all)

    async def query_async(self, query: str, *, limit=50, offset=0, meeting_id=None,
                          meeting_ids=None, person_id=None, date_from=None, date_to=None, match_all=True,
                          semantic_query=None):
        if self._owns_work: self.work=SearchWork()
        self._validate_query(query,limit,offset)
        if semantic_query is not None and (not isinstance(semantic_query,str) or not 1<=len(semantic_query)<=500):
            raise ValueError('Invalid semantic question')
        self.work.check()
        scope=[meeting_id] if meeting_id else meeting_ids
        if scope is not None and (not scope or len(scope)>100 or any(not isinstance(s,str) for s in scope)):
            raise ValueError('Invalid meeting scope')
        started=time.monotonic()
        status=await self.sync_async(scope)
        self.work.indexing_ms += round((time.monotonic()-started)*1000)
        hybrid=self.embedding_config is not None
        lexical=self._query(retrieval_query(query) if hybrid else query,status,limit=100 if hybrid else limit,offset=0 if hybrid else offset,meeting_id=meeting_id,
                           meeting_ids=meeting_ids,person_id=person_id,date_from=date_from,date_to=date_to,
                           match_all=False if hybrid else match_all)
        result=await self._hybrid(semantic_query or query,lexical,limit=limit,offset=offset,meeting_id=meeting_id,
                                  meeting_ids=meeting_ids,person_id=person_id,date_from=date_from,date_to=date_to)
        self.work.check()
        self.work.retrieval_ms=round((time.monotonic()-started)*1000)
        result['usage']=self.work.public()
        return result

    def _semantic_chunks(self,session_id):
        rows=list(self.conn.execute("SELECT * FROM search_documents WHERE session_id=? AND source_type IN ('transcript','summary') "
                                    "ORDER BY CASE source_type WHEN 'summary' THEN 0 ELSE 1 END,start_ms,rowid",(session_id,)))
        chunks=[]
        for row in rows:
            text=row['content']
            # Rank each source independently. Adjacent boilerplate must not inherit
            # another segment's similarity or consume its evidence rank.
            for start in range(0,len(text),1000):
                end=min(len(text),start+1200)
                if not text[start:end].strip(): continue
                chunks.append({'start_ms':row['start_ms'] or 0,'end_ms':row['end_ms'] or 0,
                    'segment_ids':[row['id']], 'text':text[start:end],
                    'source_spans':[{'document_id':row['id'],'start':start,'end':end}]})
                if end==len(text): break
        return chunks

    async def _sync_semantic(self,scope=None):
        config=self.embedding_config
        if config is None:
            return {'enabled':False,'status':'keyword_only'}
        if self.embedder is None:
            return {'enabled':False,'status':'local_model_unavailable','generation':config.generation}
        index=SqliteVecIndex(self.conn,dim=config.dimensions,generation=config.generation)
        changed=[]
        for row in self.conn.execute('SELECT session_id,fingerprint FROM search_index_state ORDER BY session_id'):
            if scope is not None and row['session_id'] not in scope:
                continue
            known=self.conn.execute('SELECT fingerprint FROM embedding_index_state WHERE session_id=? AND generation=?',
                                    (row['session_id'],config.generation)).fetchone()
            if not known or known['fingerprint'] != row['fingerprint']:
                changed.append((row['session_id'],row['fingerprint']))
        for session_id,fingerprint in changed:
            self.work.check()
            chunks=self._semantic_chunks(session_id)
            vectors=[]
            for start in range(0,len(chunks),16):
                if self.work.embedding_requests>=128:
                    raise ValueError('Indexing budget reached; submit again to continue saved indexing')
                vectors.extend(await self.work.embed(self.embedder,[chunk['text'] for chunk in chunks[start:start+16]]))
                await asyncio.sleep(0)
            self.work.check()
            if self._fingerprint(session_id) != fingerprint:
                raise ValueError('Meeting changed while semantic search was indexing; search again')
            if len(vectors)!=len(chunks):
                raise ValueError('Embedding provider returned an incomplete batch')
            if any(not isinstance(vector,bytes) or len(vector)!=config.dimensions*4 for vector in vectors):
                raise ValueError('Embedding provider returned a different vector shape')
            with self.conn:
                self.conn.execute(f'DELETE FROM {index._table} WHERE id IN '
                    '(SELECT rowid FROM embeddings_chunks WHERE session_id=? AND generation=?)',
                    (session_id,config.generation))
                self.conn.execute('DELETE FROM embeddings_chunks WHERE session_id=? AND generation=?',
                                  (session_id,config.generation))
                for chunk,vector in zip(chunks,vectors,strict=True):
                    index._add(session_id,chunk['start_ms'],chunk['end_ms'],chunk['segment_ids'],chunk['text'],vector,
                               source_spans=chunk['source_spans'])
                self.conn.execute('INSERT INTO embedding_index_state(session_id,generation,fingerprint,indexed_at,chunk_count) '
                    'VALUES (?,?,?,?,?) ON CONFLICT(session_id,generation) DO UPDATE SET '
                    'fingerprint=excluded.fingerprint,indexed_at=excluded.indexed_at,chunk_count=excluded.chunk_count',
                    (session_id,config.generation,fingerprint,int(time.time()*1000),len(chunks)))
            await asyncio.sleep(0)
        return {'enabled':True,'status':'ready','generation':config.generation,
                'provider':config.provider,'model':config.model,'dimensions':config.dimensions,
                'indexed_meetings':self.conn.execute('SELECT COUNT(*) FROM embedding_index_state WHERE generation=?',
                                                     (config.generation,)).fetchone()[0]}

    async def _hybrid(self,query,lexical,*,limit,offset,meeting_id,meeting_ids,person_id,date_from,date_to):
        if self.embedding_config is None:
            lexical['index']['semantic']={'enabled':False,'status':'keyword_only'}
            lexical['retrieval_mode']='keyword'
            return lexical
        scope=[meeting_id] if meeting_id else meeting_ids
        try:
            started=time.monotonic()
            semantic=await self._sync_semantic(scope)
        except SearchAuthorizationError:
            raise
        except Exception:  # noqa: BLE001 - sanitize embedding bodies/keys; disclose keyword-only results
            semantic={'enabled':False,'status':'embedding_error','message':'Semantic indexing failed. Check the embedding provider and vector dimensions; submit again to retry.'}
        finally:
            self.work.indexing_ms += round((time.monotonic()-started)*1000)
        lexical['index']['semantic']=semantic
        if not semantic['enabled']:
            lexical['results']=lexical['results'][offset:offset+limit]
            lexical['retrieval_mode']='keyword'
            return lexical
        try:
            query_vector=(await self.work.embed(self.embedder,[query.strip()],query=True))[0]
        except SearchAuthorizationError:
            raise
        except Exception:  # noqa: BLE001 - sanitize embedding bodies/keys; no automatic retry
            semantic.update(enabled=False,status='embedding_error',message='Semantic query failed. Check the embedding provider and submit again to retry.')
            lexical['results']=lexical['results'][offset:offset+limit]
            lexical['retrieval_mode']='keyword'
            return lexical
        for row in self.conn.execute('SELECT session_id,fingerprint FROM embedding_index_state WHERE generation=?',
                                     (self.embedding_config.generation,)):
            if (scope is None or row['session_id'] in scope) and self._fingerprint(row['session_id'])!=row['fingerprint']:
                raise ValueError('Meeting changed during semantic search; submit again')
        index=SqliteVecIndex(self.conn,dim=self.embedding_config.dimensions,
                             generation=self.embedding_config.generation)
        chunks=index.search(query_vector,k=min(100,max(20,limit*4)),session_ids=scope,
                            person_id=person_id,date_from=date_from,date_to=date_to)
        best={}
        for rank,chunk in enumerate(chunks,1):
            for span in chunk['source_spans']:
                if span['document_id'] not in best:
                    best[span['document_id']]={**span,'rank':rank,'distance':chunk['distance']}
        semantic_ids=list(best)
        if semantic_ids:
            placeholders=','.join('?' for _ in semantic_ids)
            constraints=[]; args=list(semantic_ids)
            if person_id:
                constraints.append('(speaker_id=? OR EXISTS (SELECT 1 FROM person_links pl WHERE pl.session_id=search_documents.session_id AND pl.revision=search_documents.revision AND pl.person_id=?))'); args.extend((person_id,person_id))
            if date_from is not None: constraints.append('started_at>=?'); args.append(date_from)
            if date_to is not None: constraints.append('started_at<?'); args.append(date_to)
            rows=self.conn.execute(f'SELECT * FROM search_documents WHERE id IN ({placeholders})'+
                ((' AND '+' AND '.join(constraints)) if constraints else ''),args).fetchall()
            by_id={row['id']:row for row in rows}
        else: by_id={}
        lexical_rank={row['id']:rank for rank,row in enumerate(lexical['results'],1)}
        semantic_rank={identity:best[identity]['rank'] for identity in semantic_ids if identity in by_id}
        identities=set(lexical_rank)|set(semantic_rank)
        scores={identity:(.2/(60+lexical_rank[identity]) if identity in lexical_rank else 0)+
                         (.8/(20+semantic_rank[identity]) if identity in semantic_rank else 0) for identity in identities}
        lexical_rows={row['id']:row for row in lexical['results']}
        results=[]
        for identity in sorted(identities,key=lambda value:(-scores[value],value))[offset:offset+limit]:
            if identity in lexical_rows: item=dict(lexical_rows[identity])
            else:
                item=dict(by_id[identity]); item.pop('content')
            if identity in best:
                span=best[identity]; content=by_id[identity]['content']
                item['excerpt_start']=span['start']; item['excerpt_end']=span['end']
                excerpt=content[span['start']:span['end']]
                item['snippet']=excerpt[:360]+('…' if len(excerpt)>360 else '')
            item['hybrid_score']=scores[identity]; results.append(item)
        lexical['results']=results; lexical['retrieval_mode']='hybrid'
        lexical['next_offset']=offset+limit if len(identities)>offset+limit else None
        return lexical

    @staticmethod
    def _validate_query(query,limit,offset):
        if not isinstance(query,str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError('Enter from 1 to 200 search characters')
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=10_000:
            raise ValueError('Invalid search page')

    def _query(self, query, status, *, limit, offset, meeting_id, meeting_ids,
               person_id, date_from, date_to, match_all):
        where=[]; args=[]
        if not self.include_titles:
            where.append("d.source_type != 'title'")
        if meeting_id and meeting_ids is not None:
            raise ValueError('Choose one meeting scope')
        if meeting_id:
            where.append('d.session_id=?'); args.append(meeting_id)
        if meeting_ids is not None:
            if (not isinstance(meeting_ids,(list,tuple)) or not meeting_ids or
                    len(meeting_ids)>100 or any(not isinstance(value,str) for value in meeting_ids)):
                raise ValueError('Invalid meeting scope')
            values=tuple(dict.fromkeys(meeting_ids))
            where.append('d.session_id IN ('+','.join('?' for _ in values)+')')
            args.extend(values)
        if person_id:
            where.append('(d.speaker_id=? OR EXISTS (SELECT 1 FROM person_links pl WHERE pl.session_id=d.session_id '
                         'AND pl.revision=d.revision AND pl.person_id=?))'); args.extend((person_id,person_id))
        if date_from is not None:
            where.append('d.started_at>=?'); args.append(date_from)
        if date_to is not None:
            where.append('d.started_at<?'); args.append(date_to)
        clause=(' AND '+' AND '.join(where)) if where else ''
        terms=[t for t in re.findall(r"[\wÀ-ỹ]+",query,flags=re.UNICODE) if t]
        if self.fts and terms:
            operator=' AND ' if match_all else ' OR '
            match=operator.join('"'+term.replace('"','""')+'"*' for term in terms[:12])
            sql=('SELECT d.*,bm25(search_documents_fts,1.0,2.5,1.5) AS score '
                 'FROM search_documents_fts JOIN search_documents d ON d.rowid=search_documents_fts.rowid '
                 'WHERE search_documents_fts MATCH ?'+clause+
                 " ORDER BY score,CASE d.source_type WHEN 'title' THEN 0 WHEN 'transcript' THEN 1 ELSE 2 END,d.started_at DESC LIMIT ? OFFSET ?")
            rows=self.conn.execute(sql,(match,*args,limit+1,offset)).fetchall()
        else:
            folded=_fold(query.strip())
            # The fallback is deliberately bounded and reports itself as basic.
            rows=[r for r in self.conn.execute('SELECT d.*,0.0 AS score FROM search_documents d WHERE 1=1'+clause+
                                                ' ORDER BY d.started_at DESC LIMIT 5000',args)
                  if folded in _fold(' '.join((r['title'],r['speaker_name'] or '',r['content'])))]
            rows=rows[offset:offset+limit+1]
        results=[]
        for row in rows[:limit]:
            item=dict(row); content=item.pop('content')
            item['snippet']=content[:360]+('…' if len(content)>360 else '')
            results.append(item)
        return {'query':query.strip(),'results':results,'next_offset':offset+limit if len(rows)>limit else None,
                'index':status}
