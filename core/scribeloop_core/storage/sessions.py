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

    def append(self, session_id: str, chunk: dict, *, stream: str = 'loopback', revision: int = 1):
        if stream not in {'loopback', 'mic'}:
            raise ValueError('Unknown transcript stream')
        if not chunk['is_final']:
            return None
        row = {'id': uuid4().hex, 'session_id': session_id, 'stream': stream,
               'start_ms': int(chunk['start_ms']), 'end_ms': int(chunk['end_ms']),
               'text': chunk['text'], 'language': chunk.get('language'),
               'confidence': chunk.get('confidence'), 'source': 'offline' if revision > 1 else 'stream', 'revision': revision}
        if row['start_ms'] < 0 or row['end_ms'] < row['start_ms']:
            raise ValueError('Invalid transcript timestamps')
        with self.conn:
            self.conn.execute('INSERT INTO segments (' + ','.join(row) + ') VALUES ('
                              + ','.join('?' for _ in row) + ')', tuple(row.values()))
        return row

    def get(self, session_id: str):
        row = self.conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        return dict(row) if row else None

    def list(self):
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM sessions ORDER BY started_at DESC, id DESC LIMIT 100')]

    def segments(self, session_id: str):
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM segments WHERE session_id=? AND revision=('
            'SELECT MAX(revision) FROM segments WHERE session_id=?) ORDER BY start_ms, rowid',
            (session_id, session_id))]

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
        rows = self.segments(session_id)
        content = 'Conversation notes · transcript excerpts\n\n'
        content += '\n'.join(f"[{row['start_ms']//60000}:{row['start_ms']//1000%60:02d}] "
            f"{'Microphone' if row['stream'] == 'mic' else 'Meeting audio'}: {row['text']}" for row in rows)
        if not rows:
            content += 'No speech was transcribed. Check capture routing and microphone permission.'
        with self.conn:
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

    def revise_transcript(self, session_id, chunks):
        from ..pipeline.replay import ReplayChunk
        if not chunks:
            raise ValueError('Empty reprocessing result; retain the previous transcript')
        rows = []
        for chunk in chunks:
            if chunk.get('stream') not in {'mic','loopback'}:
                raise ValueError('Invalid transcript stream')
            row = ReplayChunk.model_validate(chunk).model_dump()
            rows.append({**row, 'stream':chunk['stream']})
        revision = self.conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM segments WHERE session_id=?',
                                     (session_id,)).fetchone()[0]
        # append() uses transaction contexts; an outer savepoint makes all rows atomic.
        self.conn.execute('SAVEPOINT transcript_revision')
        try:
            for chunk in rows:
                row = {'id':uuid4().hex, 'session_id':session_id, 'stream':chunk['stream'],
                       'start_ms':chunk['start_ms'], 'end_ms':chunk['end_ms'], 'text':chunk['text'],
                       'language':chunk['language'], 'confidence':chunk['confidence'],
                       'source':'offline', 'revision':revision}
                self.conn.execute('INSERT INTO segments (' + ','.join(row) + ') VALUES ('
                                  + ','.join('?' for _ in row) + ')', tuple(row.values()))
            self.conn.execute('RELEASE SAVEPOINT transcript_revision')
        except BaseException:
            self.conn.execute('ROLLBACK TO SAVEPOINT transcript_revision')
            self.conn.execute('RELEASE SAVEPOINT transcript_revision')
            raise

    def set_language_lock(self, session_id, lock):
        if lock.get('language') not in {'vi','en'} or lock.get('mode') not in {'auto','manual'}:
            raise ValueError('Invalid language lock')
        metadata = {key:lock.get(key) for key in ('language','mode','confidence','sample_s')}
        with self.conn:
            self.conn.execute('INSERT INTO events (session_id,ts,level,type,payload_json) '
                'VALUES (?,?,?,?,?)', (session_id,self._now(),'info','language_locked',json.dumps(metadata)))

    def language_lock(self, session_id):
        row = self.conn.execute("SELECT payload_json FROM events WHERE session_id=? AND type='language_locked' "
            'ORDER BY id DESC LIMIT 1', (session_id,)).fetchone()
        return json.loads(row['payload_json']) if row else None
