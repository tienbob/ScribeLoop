"""Explicit people links. Naming never enables voice learning or automatic matching."""
from __future__ import annotations

import unicodedata
from uuid import uuid4

from ..storage.db import purge_checkpoint


class SpeakerRegistry:
    def __init__(self,store,*,audio=None):
        self.store,self.conn=store,store.conn
        self.audio=audio

    def list(self):
        return [dict(row) for row in self.conn.execute('SELECT id,canonical_name,learn_enabled,created_at FROM speakers ORDER BY canonical_name,id')]

    def create(self,name):
        if not isinstance(name,str):
            raise TypeError('Invalid person name')
        name=name.strip()
        name.encode('utf-8')
        if not 1<=len(name)<=80 or any(unicodedata.category(c).startswith('C') for c in name):
            raise ValueError('Invalid person name')
        identity=uuid4().hex
        with self.conn:
            self.conn.execute('INSERT INTO speakers(id,canonical_name,learn_enabled,created_at) VALUES (?,?,0,?)',
                              (identity,name,self.store._now()))
        return identity

    def link(self,sid,revision,speaker,person):
        session=self.store.get(sid)
        if not session or session['status'] not in {'completed','failed','interrupted'} or revision!=session['transcript_revision']:
            raise ValueError('Select the current saved transcript')
        if person is not None and not self.conn.execute('SELECT 1 FROM speakers WHERE id=?',(person,)).fetchone():
            raise ValueError('Unknown person')
        if speaker not in {r['display_speaker_id'] for r in self.store.segments(sid) if r['display_speaker_id']}:
            raise ValueError('Unknown meeting speaker')
        with self.conn:
            if person is None:
                self.conn.execute('DELETE FROM person_links WHERE session_id=? AND revision=? AND speaker_id=?',(sid,revision,speaker))
            else:
                self.conn.execute('INSERT OR IGNORE INTO person_link_history VALUES (?,?,?,?)',(sid,revision,speaker,person))
                self.conn.execute('INSERT INTO person_links(session_id,revision,speaker_id,person_id) VALUES (?,?,?,?) '
                    'ON CONFLICT(session_id,revision,speaker_id) DO UPDATE SET person_id=excluded.person_id',
                    (sid,revision,speaker,person))
            self.store._write_transcript_notes(sid)

    def name_speaker(self,sid,revision,speaker,*,name=None,person_id=None):
        """Create/reuse a person and link in one transaction; stale reads create nothing."""
        if (name is None)==(person_id is None):
            raise ValueError('Provide a name or existing person')
        session=self.store.get(sid)
        if (not session or session['status'] not in {'completed','failed','interrupted'} or
                speaker not in {r['id'] for r in self.store.speaker_roster(sid,revision)}):
            raise ValueError('Choose a speaker in the current saved transcript')
        # create/link normally commit their own transactions; an explicit outer
        # savepoint keeps both mutations and derived notes atomic here.
        self.conn.execute('SAVEPOINT name_speaker')
        try:
            if name is not None:
                if not isinstance(name,str):
                    raise TypeError('Invalid person name')
                clean=name.strip()
                if not 1<=len(clean)<=80 or any(unicodedata.category(c).startswith('C') for c in clean):
                    raise ValueError('Invalid person name')
                clean.encode('utf-8')
                person_id=uuid4().hex
                self.conn.execute('INSERT INTO speakers(id,canonical_name,learn_enabled,created_at) VALUES (?,?,0,?)',
                                  (person_id,clean,self.store._now()))
            elif not self.conn.execute('SELECT 1 FROM speakers WHERE id=?',(person_id,)).fetchone():
                raise ValueError('Unknown person')
            self.conn.execute('INSERT OR IGNORE INTO person_link_history VALUES (?,?,?,?)',(sid,revision,speaker,person_id))
            self.conn.execute('INSERT INTO person_links(session_id,revision,speaker_id,person_id) VALUES (?,?,?,?) '
                'ON CONFLICT(session_id,revision,speaker_id) DO UPDATE SET person_id=excluded.person_id',
                (sid,revision,speaker,person_id))
            self.store._write_transcript_notes(sid)
            self.conn.execute('RELEASE name_speaker')
        except BaseException:
            self.conn.execute('ROLLBACK TO name_speaker')
            self.conn.execute('RELEASE name_speaker')
            raise
        return person_id

    def learning(self,person,enabled):
        if type(enabled) is not bool or not self.conn.execute('SELECT 1 FROM speakers WHERE id=?',(person,)).fetchone():
            raise ValueError('Unknown person or preference')
        # This stores a preference only. No embedding backend is loaded or dispatched.
        with self.conn:
            self.conn.execute('UPDATE speakers SET learn_enabled=? WHERE id=?',(int(enabled),person))

    def forget(self,person):
        row=self.conn.execute('SELECT canonical_name FROM speakers WHERE id=?',(person,)).fetchone()
        if not row:
            raise ValueError('Unknown person')
        links=[dict(r) for r in self.conn.execute('SELECT * FROM person_links WHERE person_id=? UNION SELECT * FROM person_link_history WHERE person_id=?',(person,person))]
        sessions={r['session_id'] for r in links}
        sessions.update(r[0] for r in self.conn.execute('SELECT source_session_id FROM speaker_embeddings WHERE speaker_id=?',(person,)) if r[0])
        sessions.update(r[0] for r in self.conn.execute('SELECT session_id FROM session_speakers WHERE speaker_id=?',(person,)))
        has_vectors=self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='embeddings_chunks_vec'").fetchone()
        if has_vectors:
            from ..storage.vectors import load_vec_extension
            load_vec_extension(self.conn)
        with self.conn:
            for sid in sessions:
                if has_vectors:
                    self.conn.execute('DELETE FROM embeddings_chunks_vec WHERE id IN (SELECT rowid FROM embeddings_chunks WHERE session_id=?)',(sid,))
                for table in ('embeddings_chunks','grounded_summaries','summary_jobs','summaries'):
                    self.conn.execute(f'DELETE FROM {table} WHERE session_id=?',(sid,))
                if self.store.get(sid).get('capture',{}).get('auto_title'):
                    self.conn.execute("UPDATE sessions SET title='Meeting conversation' WHERE id=?",(sid,))
            for link in links:
                self.conn.execute('UPDATE revision_speaker_edits SET label=NULL WHERE session_id=? AND revision=? AND speaker_id=? AND label=?',
                    (link['session_id'],link['revision'],link['speaker_id'],row[0]))
            self.conn.execute('DELETE FROM person_links WHERE person_id=?',(person,))
            self.conn.execute('DELETE FROM person_link_history WHERE person_id=?',(person,))
            self.conn.execute('DELETE FROM speaker_embeddings WHERE speaker_id=?',(person,))
            self.conn.execute("UPDATE session_speakers SET speaker_id=NULL,local_label=CASE WHEN local_label=? THEN 'Speaker unknown' ELSE local_label END WHERE speaker_id=?",
                              (row[0],person))
            self.conn.execute('DELETE FROM speakers WHERE id=?',(person,))
            for sid in sessions:
                self.store._write_transcript_notes(sid)
                self.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                    (sid,self.store._now(),'info','summary_redaction_review','{"reason":"Person forgotten; derived notes removed. Exports/backups may remain."}'))
        purge_checkpoint(self.conn)
        return sorted(sessions)

    def enroll(self,person,sid,revision,speaker,start_ms,end_ms,embedding,*,model,version,reviewed=False):
        """Store only explicitly reviewed, linked, pure, opt-in exemplars."""
        from .embeddings import vector
        profile=self.conn.execute('SELECT learn_enabled FROM speakers WHERE id=?',(person,)).fetchone()
        link=self.conn.execute('SELECT 1 FROM person_links WHERE session_id=? AND revision=? AND speaker_id=? AND person_id=?',
                               (sid,revision,speaker,person)).fetchone()
        if not profile or not profile[0] or not link or reviewed is not True or not model or not version:
            raise ValueError('Explicit named-person learning and review are required')
        if self.audio is None:
            raise ValueError('Retained audio is required for enrollment')
        self.audio.require(sid)
        session=self.store.get(sid)
        if session['transcript_revision']!=revision or not 30000<=end_ms-start_ms<=120000 or start_ms<0:
            raise ValueError('Invalid exemplar revision or duration')
        rows=self.store.segments(sid)
        selected=[r for r in rows if r['display_speaker_id']==speaker and r['end_ms']>start_ms and r['start_ms']<end_ms]
        if not selected:
            raise ValueError('No speech in exemplar')
        stream=selected[0]['stream']
        report=self.audio.audio.scan(sid,stream)
        if not report['complete'] or report['end_ms']<end_ms:
            raise ValueError('Exemplar audio is incomplete or shorter than its labels')
        if any(r['stream']==stream and r['display_speaker_id']!=speaker and r['end_ms']>start_ms and r['start_ms']<end_ms for r in rows):
            raise ValueError('Exemplar overlaps another speaker')
        intervals=sorted((max(start_ms,r['start_ms']),min(end_ms,r['end_ms'])) for r in selected)
        covered=0
        last=start_ms
        for a,b in intervals:
            covered+=max(0,b-max(a,last))
            last=max(last,b)
        if covered<30000:
            raise ValueError('Exemplar lacks thirty seconds of pure speech')
        count=self.conn.execute('SELECT COUNT(*) FROM speaker_embeddings WHERE speaker_id=?',(person,)).fetchone()[0]
        if count>=10:
            raise ValueError('Review/remove an existing exemplar before adding more')
        data=vector(embedding)
        identity=uuid4().hex
        with self.conn:
            self.conn.execute('INSERT INTO speaker_embeddings(id,speaker_id,source_session_id,model_name,model_version,embedding,exemplar_ms,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (identity,person,sid,model,version,data.astype('<f4').tobytes(),covered,self.store._now()))
            self.conn.execute('INSERT INTO exemplar_provenance VALUES (?,?,?,?,?,?)',
                (identity,speaker,revision,start_ms,end_ms,self.store._now()))
        return identity

    def suggest(self,embedding,calibration,*,model,version):
        import numpy as np

        from .embeddings import vector
        if (not calibration.get('suggestions_ready') or calibration.get('model')!=model or
                calibration.get('version')!=version):
            return []
        query=vector(embedding)
        best={}
        for row in self.conn.execute('SELECT e.speaker_id,e.embedding FROM speaker_embeddings e JOIN speakers s ON s.id=e.speaker_id '
            'WHERE s.learn_enabled=1 AND e.model_name=? AND e.model_version=?',(model,version)):
            value=vector(np.frombuffer(row['embedding'],dtype='<f4'))
            if value.shape!=query.shape:
                continue
            score=float(np.dot(value,query))
            if score>=calibration['threshold']:
                best[row['speaker_id']]=max(score,best.get(row['speaker_id'],-1))
        return [{'person_id':person,'score':score,'automatic':False} for person,score in sorted(best.items(),key=lambda pair:-pair[1])]
