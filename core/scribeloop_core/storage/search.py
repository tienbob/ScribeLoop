"""Revision-aware, encrypted local meeting search.

The canonical documents live inside the SQLCipher workspace.  FTS5 is used when
the linked SQLite build provides it; a bounded LIKE fallback keeps search usable
and explicitly reports its reduced capability.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata

from .db import DATABASE_ERRORS

INDEX_VERSION = 1


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
                if key not in {'id', 'citation_ids', 'citations', 'source_ids'} and not key.endswith('_id'):
                    visit(item)
    visit(value)
    return '\n'.join(dict.fromkeys(part.strip() for part in parts if part.strip()))


class MeetingSearch:
    """Synchronize selected revisions and run bounded local queries."""

    def __init__(self, store):
        self.store = store
        self.conn = store.conn
        self.fts = self._ensure_fts()

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
        elif self.store.notes(session_id):
            content=self.store.notes(session_id)
            docs.append({**common,'id':f'{session_id}:notes','revision':revision,
                'source_type':'summary','source_id':'notes','start_ms':None,'end_ms':None,
                'speaker_id':None,'speaker_name':None,'content':content})
        return docs

    def sync(self, *, limit: int | None = None):
        rows=list(self.conn.execute('SELECT id FROM sessions ORDER BY started_at DESC,id DESC'))
        changed=[]
        for row in rows:
            fingerprint=self._fingerprint(row['id'])
            known=self.conn.execute('SELECT fingerprint FROM search_index_state WHERE session_id=?',(row['id'],)).fetchone()
            if not known or known['fingerprint'] != fingerprint:
                changed.append((row['id'],fingerprint))
        selected=changed if limit is None else changed[:limit]
        for session_id,fingerprint in selected:
            docs=self._documents(session_id)
            with self.conn:
                old_rows=[r[0] for r in self.conn.execute('SELECT rowid FROM search_documents WHERE session_id=?',(session_id,))]
                if self.fts:
                    for rowid in old_rows:
                        self.conn.execute("INSERT INTO search_documents_fts(search_documents_fts,rowid,content,title,speaker_name) "
                                          "SELECT 'delete',rowid,content,title,COALESCE(speaker_name,'') FROM search_documents WHERE rowid=?",(rowid,))
                self.conn.execute('DELETE FROM search_documents WHERE session_id=?',(session_id,))
                for doc in docs:
                    fields=tuple(doc)
                    cur=self.conn.execute(f"INSERT INTO search_documents ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                                          tuple(doc.values()))
                    if self.fts:
                        self.conn.execute('INSERT INTO search_documents_fts(rowid,content,title,speaker_name) VALUES (?,?,?,?)',
                                          (cur.lastrowid,doc['content'],doc['title'],doc['speaker_name'] or ''))
                self.conn.execute('INSERT INTO search_index_state(session_id,fingerprint,indexed_at,document_count) '
                                  'VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                                  'fingerprint=excluded.fingerprint,indexed_at=excluded.indexed_at,document_count=excluded.document_count',
                                  (session_id,fingerprint,int(time.time()*1000),len(docs)))
        return {'complete':len(selected)==len(changed),'pending':len(changed)-len(selected),
                'indexed_meetings':self.conn.execute('SELECT COUNT(*) FROM search_index_state').fetchone()[0],
                'engine':'fts5' if self.fts else 'basic'}

    def purge_session(self, session_id: str) -> None:
        """Remove index text before the owning encrypted meeting is deleted."""
        rows=list(self.conn.execute('SELECT rowid,content,title,COALESCE(speaker_name,\'\') '
                                    'FROM search_documents WHERE session_id=?',(session_id,)))
        with self.conn:
            if self.fts:
                for row in rows:
                    self.conn.execute("INSERT INTO search_documents_fts(search_documents_fts,rowid,content,title,speaker_name) "
                                      "VALUES ('delete',?,?,?,?)",tuple(row))
            self.conn.execute('DELETE FROM search_documents WHERE session_id=?',(session_id,))
            self.conn.execute('DELETE FROM search_index_state WHERE session_id=?',(session_id,))

    def query(self, query: str, *, limit=50, offset=0, meeting_id=None,
              person_id=None, date_from=None, date_to=None):
        if not isinstance(query,str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError('Enter from 1 to 200 search characters')
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=10_000:
            raise ValueError('Invalid search page')
        status=self.sync()
        where=[]; args=[]
        if meeting_id:
            where.append('d.session_id=?'); args.append(meeting_id)
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
            match=' AND '.join('"'+term.replace('"','""')+'"*' for term in terms[:12])
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
