"""Replay and encrypted meeting session metadata; connections stay on the API thread."""
from __future__ import annotations

import json
import sqlite3
import time
from uuid import uuid4


class SessionStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def recover(self):
        with self.conn:
            self.conn.execute("UPDATE sessions SET status='interrupted', ended_at=? "
                              "WHERE status IN ('loading','replaying','preparing','recording','stopping','processing')", (self._now(),))
            self.conn.execute('UPDATE session_streams SET ended_at=(' 
                'SELECT ended_at FROM sessions WHERE id=session_streams.session_id) '
                "WHERE ended_at IS NULL AND session_id IN (SELECT id FROM sessions WHERE status='interrupted')")

    @staticmethod
    def _now():
        return int(time.time() * 1000)

    def create(self, fixture: str, title: str, path: str):
        session_id = uuid4().hex
        with self.conn:
            self.conn.execute('INSERT INTO sessions '
                '(id,started_at,source,title,status,consent_confirmed) VALUES (?,?,?,?,?,1)',
                (session_id, self._now(), fixture, title, 'loading'))
            self.conn.execute('INSERT INTO session_streams '
                '(id,session_id,stream,audio_path,sample_rate,started_at) VALUES (?,?,?,?,?,?)',
                (uuid4().hex, session_id, 'loopback', path, 16000, self._now()))
        return self.get(session_id)

    def transition(self, session_id: str, status: str):
        terminal = status in {'completed', 'stopped', 'failed', 'interrupted', 'processing'}
        now = self._now()
        with self.conn:
            self.conn.execute('UPDATE sessions SET status=?, ended_at=CASE '
                "WHEN source='meeting' AND ended_at IS NOT NULL THEN ended_at ELSE ? END WHERE id=?",
                (status, now if terminal else None, session_id))
            if terminal:
                self.conn.execute('UPDATE session_streams SET ended_at=COALESCE(ended_at,?) WHERE session_id=?',
                                  (now, session_id))
        return self.get(session_id)

    def _speaker(self, session_id, stream, key):
        if key is None:
            return None
        import re
        if not re.fullmatch(r'(deepgram|moss):[0-9a-f]{32}:'+stream+r':[0-9]{1,4}',key):
            raise ValueError('Invalid speaker identity scope')
        identity = session_id+':'+key
        existing = self.conn.execute('SELECT id FROM session_speakers WHERE id=?',(identity,)).fetchone()
        if not existing:
            number = self.conn.execute('SELECT COUNT(*)+1 FROM session_speakers WHERE session_id=?',
                                       (session_id,)).fetchone()[0]
            self.conn.execute('INSERT INTO session_speakers(id,session_id,stream,local_label) VALUES (?,?,?,?)',
                              (identity,session_id,stream,f'Speaker {number}'))
        return identity

    def _speaker_label(self, identity):
        row = self.conn.execute('SELECT local_label FROM session_speakers WHERE id=?',(identity,)).fetchone()
        return row[0] if row else None

    def append(self, session_id: str, chunk: dict, *, stream: str = 'loopback', revision: int | None = None):
        if stream not in {'loopback', 'mic'}:
            raise ValueError('Unknown transcript stream')
        if not chunk['is_final']:
            return None
        if revision is None:
            revision = self.conn.execute(
                'SELECT COALESCE(MAX(revision),1) FROM segments WHERE session_id=?',
                (session_id,)).fetchone()[0]
        if type(revision) is not int or revision < 1:
            raise ValueError('Invalid transcript revision')
        row = {'id': uuid4().hex, 'session_id': session_id, 'stream': stream,
               'start_ms': int(chunk['start_ms']), 'end_ms': int(chunk['end_ms']),
               'text': chunk['text'], 'language': chunk.get('language'),
               'confidence': chunk.get('confidence'), 'source': 'stream', 'revision': revision}
        if row['start_ms'] < 0 or row['end_ms'] < row['start_ms']:
            raise ValueError('Invalid transcript timestamps')
        with self.conn:
            metadata = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? "
                "AND type='recording_selection' ORDER BY id LIMIT 1", (session_id,)).fetchone()
            provenance = json.loads(metadata[0]) if metadata else {}
            self.conn.execute('INSERT OR IGNORE INTO transcript_revisions '
                '(session_id,revision,origin,provider,model,created_at) VALUES (?,?,?,?,?,?)',
                (session_id,revision,'confirmed',provenance.get('provider'),
                 provenance.get('model'),self._now()))
            row['local_speaker_id'] = self._speaker(session_id,stream,chunk.get('speaker_key'))
            self.conn.execute('INSERT INTO segments (' + ','.join(row) + ') VALUES ('
                              + ','.join('?' for _ in row) + ')', tuple(row.values()))
        row['speaker_label'] = self._speaker_label(row.get('local_speaker_id'))
        row['display_speaker_id'] = row.get('local_speaker_id')
        return row

    def get(self, session_id: str):
        row = self.conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        return self._session(row) if row else None

    def _session(self, row):
        session = dict(row)
        session['transcript_revision'] = self.conn.execute(
            'SELECT MAX(revision) FROM segments WHERE session_id=?', (session['id'],)).fetchone()[0]
        if session['source'] == 'meeting':
            metadata = self.conn.execute(
                "SELECT payload_json FROM events WHERE session_id=? AND type='recording_selection' "
                'ORDER BY id LIMIT 1', (session['id'],)).fetchone()
            capture = json.loads(metadata['payload_json']) if metadata else {}
            session['capture'] = {key:capture.get(key) for key in (
                'scope','name','kind','app_id','microphone_id','microphone_name',
                'provider','model','engine','cloud_consent','cloud_budget_usd','language')}
            provider = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? "
                "AND type='transcript_provider' ORDER BY id DESC LIMIT 1",(session['id'],)).fetchone()
            session['transcript_provider'] = json.loads(provider[0])['provider'] if provider else capture.get('provider')
            failure = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? "
                "AND type='recording_failure' ORDER BY id DESC LIMIT 1",(session['id'],)).fetchone()
            session['failure'] = json.loads(failure[0]) if failure else None
            audio = self.conn.execute(
                "SELECT payload_json FROM events WHERE session_id=? AND type='recording_complete' "
                'ORDER BY id DESC LIMIT 1', (session['id'],)).fetchone()
            session['audio_duration_ms'] = json.loads(audio['payload_json'])['audio_duration_ms'] if audio else None
            recovered = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? "
                "AND type='audio_recovered' ORDER BY id DESC LIMIT 1", (session['id'],)).fetchone()
            session['recovery'] = json.loads(recovered[0]) if recovered else None
            session['has_checkpoints'] = self.conn.execute(
                'SELECT 1 FROM recording_checkpoints WHERE session_id=? LIMIT 1',(session['id'],)).fetchone() is not None
        return session

    def list(self):
        return [self._session(row) for row in self.conn.execute(
            'SELECT * FROM sessions ORDER BY started_at DESC, id DESC LIMIT 100')]

    def revisions(self, session_id: str):
        """Read-only history; a replacement becomes visible only after commit."""
        return [dict(row) for row in self.conn.execute(
            'SELECT r.*, COUNT(s.id) AS segment_count, '
            '(r.revision=(SELECT MAX(revision) FROM segments WHERE session_id=r.session_id)) AS selected '
            'FROM transcript_revisions r LEFT JOIN segments s '
            'ON s.session_id=r.session_id AND s.revision=r.revision '
            'WHERE r.session_id=? GROUP BY r.session_id,r.revision ORDER BY r.revision DESC',
            (session_id,))]

    def segments(self, session_id: str, *, revision: int | None = None):
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError('Invalid transcript revision')
        rows = [dict(row) for row in self.conn.execute(
            'SELECT segments.*, session_speakers.local_label AS speaker_label FROM segments '
            'LEFT JOIN session_speakers ON session_speakers.id=segments.local_speaker_id '
            'WHERE segments.session_id=? AND revision=COALESCE(?,('
            'SELECT MAX(revision) FROM segments WHERE session_id=?)) ORDER BY start_ms, segments.rowid',
            (session_id, revision, session_id))]
        if not rows:
            return rows
        edits = {row['speaker_id']:dict(row) for row in self.conn.execute(
            'SELECT * FROM revision_speaker_edits WHERE session_id=? AND revision=?',
            (session_id,rows[0]['revision']))}
        for row in rows:
            identity = row['local_speaker_id']
            target = edits.get(identity,{}).get('merge_target') or identity
            row['display_speaker_id'] = target
            if target:
                row['speaker_label'] = edits.get(target,{}).get('label') or self._speaker_label(target)
        return rows

    def correct_speaker(self, session_id, revision, speaker_id, *, label=None, merge_target=None):
        """Manual presentation edits; original speaker identities remain untouched."""
        import unicodedata
        session = self.get(session_id)
        if (not session or session['source'] != 'meeting' or
                session['status'] not in {'completed','failed','interrupted'} or
                type(revision) is not int or revision != session['transcript_revision']):
            raise ValueError('Speaker correction requires the current saved revision')
        if (label is None) == (merge_target is None):
            raise ValueError('Choose rename or merge')
        if label is not None:
            if not isinstance(label,str):
                raise ValueError('Invalid speaker name')
            label = label.strip()
            label.encode('utf-8')
            if not 1 <= len(label) <= 80 or any(unicodedata.category(c).startswith('C') for c in label):
                raise ValueError('Invalid speaker name')
        rows = self.segments(session_id)
        identities = {r['display_speaker_id'] for r in rows if r['display_speaker_id']}
        if speaker_id not in identities:
            raise ValueError('Unknown speaker in selected revision')
        if merge_target is not None and (merge_target not in identities or merge_target == speaker_id or
                speaker_id.rsplit(':',1)[0] != merge_target.rsplit(':',1)[0]):
            raise ValueError('Merge requires distinct speakers from the same source and generation')
        with self.conn:
            self.conn.execute('INSERT INTO revision_speaker_edits '
                '(session_id,revision,speaker_id,label,merge_target) VALUES (?,?,?,?,?) '
                'ON CONFLICT(session_id,revision,speaker_id) DO UPDATE SET '
                'label=excluded.label,merge_target=excluded.merge_target',
                (session_id,revision,speaker_id,label,merge_target))
            if merge_target is not None:
                self.conn.execute('UPDATE revision_speaker_edits SET merge_target=? '
                    'WHERE session_id=? AND revision=? AND merge_target=?',
                    (merge_target,session_id,revision,speaker_id))
            self._write_transcript_notes(session_id)

    def create_recording(self, title, metadata):
        session_id = uuid4().hex
        now = self._now()
        with self.conn:
            self.conn.execute('INSERT INTO sessions '
                '(id,started_at,source,title,status,consent_confirmed) VALUES (?,?,?,?,?,1)',
                (session_id, now, 'meeting', title, 'preparing'))
            for stream in ('mic', 'loopback'):
                self.conn.execute('INSERT INTO session_streams '
                    '(id,session_id,stream,sample_rate,started_at) VALUES (?,?,?,?,?)',
                    (uuid4().hex, session_id, stream, 16000, now))
            self.conn.execute('INSERT INTO events (session_id,ts,level,type,payload_json) '
                'VALUES (?,?,?,?,?)', (session_id, now, 'info', 'recording_selection', json.dumps(metadata)))
        return self.get(session_id)

    def set_audio_paths(self, session_id, audio):
        with self.conn:
            for stream in ('mic', 'loopback'):
                self.conn.execute('UPDATE session_streams SET audio_path=? WHERE session_id=? AND stream=?',
                    (str(audio.path(session_id, stream)), session_id, stream))

    def create_transcript_notes(self, session_id):
        with self.conn:
            return self._write_transcript_notes(session_id)

    def _write_transcript_notes(self, session_id):
        rows = self.segments(session_id)
        content = 'Conversation notes · transcript excerpts\n\n'
        content += '\n'.join(f"[{row['start_ms']//60000}:{row['start_ms']//1000%60:02d}] "
            f"{'Microphone' if row['stream'] == 'mic' else 'Meeting audio'} · {row.get('speaker_label') or 'speaker unknown'}: {row['text']}" for row in rows)
        if not rows:
            content += 'No speech was transcribed. Check capture routing and microphone permission.'
        self.conn.execute('INSERT INTO summaries '
                '(id,session_id,kind,span_start_ms,span_end_ms,content_md,actions_json,model_id,created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?)', (uuid4().hex, session_id, 'provisional', 0,
                    max((row['end_ms'] for row in rows), default=0), content, '[]',
                    'transcript-excerpts-v1', self._now()))
        return content

    def notes(self, session_id):
        row = self.conn.execute('SELECT content_md FROM summaries WHERE session_id=? '
            'ORDER BY created_at DESC,rowid DESC LIMIT 1', (session_id,)).fetchone()
        return row['content_md'] if row else ''

    def summary_input(self, session_id, revision, *, build_messages=True):
        from ..llm.grounded import summary_messages, transcript_hash
        session = self.get(session_id)
        if (not session or session['source'] != 'meeting' or
                session['status'] not in {'completed','failed','interrupted'} or
                type(revision) is not int or revision != session['transcript_revision']):
            raise ValueError('Select the current saved transcript')
        rows = self.segments(session_id)
        return rows,transcript_hash(rows),summary_messages(rows) if build_messages else None

    def save_grounded_summary(self, session_id, revision, input_hash, value, *, provider, model):
        from ..llm.grounded import validate_summary
        rows,current_hash,_ = self.summary_input(session_id,revision)
        if current_hash != input_hash:
            raise ValueError('Transcript changed during summary generation')
        summary = validate_summary(value,rows)
        with self.conn:
            self.conn.execute('INSERT INTO grounded_summaries '
                '(id,session_id,revision,input_hash,provider,model,summary_json,created_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (uuid4().hex,session_id,revision,input_hash,provider,model,json.dumps(summary),self._now()))
        return self.grounded_summary(session_id)

    def grounded_summary(self, session_id):
        from ..llm.grounded import transcript_hash
        row = self.conn.execute('SELECT * FROM grounded_summaries WHERE session_id=? '
            'ORDER BY created_at DESC,rowid DESC LIMIT 1',(session_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['summary'] = json.loads(result.pop('summary_json'))
        result['stale'] = result['input_hash'] != transcript_hash(self.segments(session_id))
        return result

    def revise_transcript(self, session_id, chunks, *, provider=None, model=None):
        from ..pipeline.replay import ReplayChunk
        if not chunks:
            raise ValueError('Empty reprocessing result; retain the previous transcript')
        rows = []
        for chunk in chunks:
            if chunk.get('stream') not in {'mic','loopback'}:
                raise ValueError('Invalid transcript stream')
            row = ReplayChunk.model_validate(chunk).model_dump()
            if not row['is_final']:
                raise ValueError('Replacement transcript must contain only confirmed text')
            rows.append({**row, 'stream':chunk['stream']})
        revision = self.conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM segments WHERE session_id=?',
                                     (session_id,)).fetchone()[0]
        # append() uses transaction contexts; an outer savepoint makes all rows atomic.
        self.conn.execute('SAVEPOINT transcript_revision')
        try:
            self.conn.execute('INSERT INTO transcript_revisions '
                '(session_id,revision,origin,provider,model,created_at) VALUES (?,?,?,?,?,?)',
                (session_id,revision,'replacement',provider,model,self._now()))
            for chunk in rows:
                row = {'id':uuid4().hex, 'session_id':session_id, 'stream':chunk['stream'],
                       'start_ms':chunk['start_ms'], 'end_ms':chunk['end_ms'], 'text':chunk['text'],
                       'language':chunk['language'], 'confidence':chunk['confidence'],
                       'source':'offline', 'revision':revision,
                       'local_speaker_id':self._speaker(session_id,chunk['stream'],chunk.get('speaker_key'))}
                self.conn.execute('INSERT INTO segments (' + ','.join(row) + ') VALUES ('
                                  + ','.join('?' for _ in row) + ')', tuple(row.values()))
            self.conn.execute('UPDATE recording_checkpoints SET confirmed_ms=recorded_ms WHERE session_id=?',
                              (session_id,))
            if provider is not None:
                self.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                    (session_id,self._now(),'info','transcript_provider',json.dumps({'provider':provider})))
            self.conn.execute('RELEASE SAVEPOINT transcript_revision')
        except BaseException:
            self.conn.execute('ROLLBACK TO SAVEPOINT transcript_revision')
            self.conn.execute('RELEASE SAVEPOINT transcript_revision')
            raise
        return revision

    def set_language_lock(self, session_id, lock):
        from ..pipeline.languages import LANGUAGE_NAMES
        if lock.get('language') not in {*LANGUAGE_NAMES,'multi'} or lock.get('mode') not in {'auto','manual','multilingual'}:
            raise ValueError('Invalid language lock')
        if (lock['language'] == 'multi') != (lock['mode'] == 'multilingual'):
            raise ValueError('Invalid multilingual mode')
        metadata = {key:lock.get(key) for key in ('language','mode','confidence','sample_s')}
        with self.conn:
            self.conn.execute('INSERT INTO events (session_id,ts,level,type,payload_json) '
                'VALUES (?,?,?,?,?)', (session_id,self._now(),'info','language_locked',json.dumps(metadata)))

    def language_lock(self, session_id):
        row = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? AND type='language_locked' "
            'ORDER BY id DESC LIMIT 1', (session_id,)).fetchone()
        return json.loads(row['payload_json']) if row else None

    def record_failure(self,session_id,diagnostic):
        with self.conn:
            self.conn.execute('INSERT INTO events(ts,session_id,level,type,payload_json) VALUES(?,?,?,?,?)',
                (self._now(),session_id,'warning','recording_failure',json.dumps(diagnostic)))

    def record_audio_duration(self, session_id, samples):
        duration = round(max(samples.values(), default=0) * 1000 / 16000)
        with self.conn:
            self.conn.execute('INSERT INTO events (session_id,ts,level,type,payload_json) '
                'VALUES (?,?,?,?,?)', (session_id,self._now(),'info','recording_complete',
                json.dumps({'audio_duration_ms':duration})))

    def checkpoints(self, session_id):
        return {row['stream']:dict(row) for row in self.conn.execute(
            'SELECT * FROM recording_checkpoints WHERE session_id=?', (session_id,))}

    def checkpoint(self, session_id, stream, *, recorded_ms=None):
        if stream not in {'mic','loopback'}:
            raise ValueError('Invalid checkpoint stream')
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO recording_checkpoints(session_id,stream) VALUES (?,?)',
                              (session_id, stream))
            if recorded_ms is not None:
                self.conn.execute('UPDATE recording_checkpoints SET recorded_ms=MAX(recorded_ms,?) '
                    'WHERE session_id=? AND stream=?', (int(recorded_ms),session_id,stream))

    def commit_window(self, session_id, stream, chunks, end_ms):
        """Transcript rows and their replay boundary become durable together."""
        from ..pipeline.replay import ReplayChunk
        rows = [ReplayChunk.model_validate(chunk).model_dump() for chunk in chunks]
        if stream not in {'mic','loopback'} or not 0 <= end_ms <= 305000:
            raise ValueError('Invalid confirmed checkpoint')
        self.conn.execute('SAVEPOINT confirmed_window')
        try:
            self.conn.execute('INSERT OR IGNORE INTO recording_checkpoints(session_id,stream) VALUES (?,?)',
                              (session_id,stream))
            current = self.checkpoints(session_id)[stream]
            if end_ms > current['recorded_ms']:
                raise ValueError('Confirmation exceeds durable recording')
            revision = self.conn.execute('SELECT COALESCE(MAX(revision),1) FROM segments WHERE session_id=?',
                                         (session_id,)).fetchone()[0]
            saved = []
            for chunk in rows:
                if not chunk['is_final'] or not current['confirmed_ms'] <= chunk['start_ms'] < chunk['end_ms'] <= end_ms:
                    raise ValueError('Confirmation overlaps checkpoint or exceeds window')
                row = {'id':uuid4().hex,'session_id':session_id,'stream':stream,
                    'start_ms':chunk['start_ms'],'end_ms':chunk['end_ms'],'text':chunk['text'],
                    'language':chunk['language'],'confidence':chunk['confidence'],
                    'source':'stream','revision':revision,
                    'local_speaker_id':self._speaker(session_id,stream,chunk.get('speaker_key'))}
                self.conn.execute('INSERT INTO segments (' + ','.join(row) + ') VALUES ('
                    + ','.join('?' for _ in row) + ')', tuple(row.values()))
                row['speaker_label'] = self._speaker_label(row.get('local_speaker_id'))
                saved.append(row)
            self.conn.execute('UPDATE recording_checkpoints SET confirmed_ms=MAX(confirmed_ms,?) '
                'WHERE session_id=? AND stream=?', (end_ms,session_id,stream))
            self.conn.execute('RELEASE SAVEPOINT confirmed_window')
            return saved
        except BaseException:
            self.conn.execute('ROLLBACK TO SAVEPOINT confirmed_window')
            self.conn.execute('RELEASE SAVEPOINT confirmed_window')
            raise

    def recovery_report(self, session_id, reports):
        with self.conn:
            self.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                (session_id,self._now(),'warning','audio_recovered',json.dumps(reports)))
            for stream, report in reports.items():
                # Reconcile checkpoints to authenticated audio, never trust a stale
                # checkpoint beyond a recovered prefix. Existing confirmed text stays.
                self.conn.execute('INSERT OR IGNORE INTO recording_checkpoints(session_id,stream) VALUES (?,?)',
                                  (session_id,stream))
                self.conn.execute('UPDATE recording_checkpoints SET recorded_ms=? WHERE session_id=? AND stream=?',
                                  (int(report['end_ms']),session_id,stream))

    def begin_delete(self, session_id):
        self.transition(session_id, 'deleting')

    def finish_delete(self, session_id):
        """Delete dependent data in FK order; callers have already retired audio keys."""
        with self.conn:
            self.conn.execute('DELETE FROM segment_links WHERE online_segment_id IN '
                '(SELECT id FROM segments WHERE session_id=?) OR offline_segment_id IN '
                '(SELECT id FROM segments WHERE session_id=?)', (session_id,session_id))
            for table in ('grounded_summaries','revision_speaker_edits','words','segments','session_speakers','speaker_remaps','speaker_embeddings',
                          'summaries','embeddings_chunks','speaker_turns','recording_checkpoints',
                          'session_streams','events','transcript_revisions'):
                column = 'source_session_id' if table == 'speaker_embeddings' else 'session_id'
                self.conn.execute(f'DELETE FROM {table} WHERE {column}=?', (session_id,))
            self.conn.execute('DELETE FROM sessions WHERE id=?', (session_id,))
        from .db import purge_checkpoint
        purge_checkpoint(self.conn)

    def retention_candidates(self, days=30):
        if not isinstance(days,int) or days < 1:
            raise ValueError('Retention must be at least one day')
        return [row[0] for row in self.conn.execute(
            "SELECT id FROM sessions WHERE source='meeting' AND status IN "
            "('completed','failed','interrupted','deleting') AND started_at < ?",
            (self._now()-days*86400000,))]

    def turns(self, session_id):
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM speaker_turns WHERE session_id=? ORDER BY start_ms', (session_id,))]

    def save_turns(self, session_id, stream, turns, end_ms):
        with self.conn:
            for turn in turns:
                self.conn.execute('INSERT INTO speaker_turns(id,session_id,stream,start_ms,end_ms,label,confidence) '
                    'VALUES (?,?,?,?,?,?,?)', (uuid4().hex,session_id,stream,turn['start_ms'],turn['end_ms'],
                    turn.get('label'),turn.get('confidence')))
            self.conn.execute('UPDATE recording_checkpoints SET diarized_ms=MAX(diarized_ms,?) '
                'WHERE session_id=? AND stream=?', (end_ms,session_id,stream))


    def transcript_provider(self, session_id):
        row = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? AND type='transcript_provider' ORDER BY id DESC LIMIT 1",(session_id,)).fetchone()
        if row:
            return json.loads(row[0])['provider']
        return (self.get(session_id) or {}).get('capture',{}).get('provider')

    def grant_cloud(self, session_id, model, *, provider='groq'):
        with self.conn:
            self.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                (session_id,self._now(),'info','cloud_consent',json.dumps({'provider':provider,'model':model,'consent':True})))

    def reserve_cloud(self, session_id, reservation):
        with self.conn:
            self.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) VALUES (?,?,?,?,?)',
                (session_id,self._now(),'info','cloud_request',json.dumps(reservation)))
        return self.cloud_usage(session_id)

    def cloud_usage(self, session_id):
        rows = [json.loads(row[0]) for row in self.conn.execute(
            "SELECT payload_json FROM events WHERE session_id=? AND type='cloud_request'", (session_id,))]
        return {'providers':sorted({r.get('provider','groq') for r in rows}),
                'requests':len(rows),'estimated_usd':sum(r['estimated_usd'] for r in rows),
                'billed_audio_s':sum(r['billed_audio_s'] for r in rows)}
