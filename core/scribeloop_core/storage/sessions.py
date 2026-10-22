"""Phase 1 public-fixture session persistence. Real-recording encryption is pending."""
from __future__ import annotations

import sqlite3
import time
from uuid import uuid4


class SessionStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def recover(self):
        with self.conn:
            self.conn.execute("UPDATE sessions SET status='interrupted', ended_at=? "
                              "WHERE status IN ('loading','replaying')", (self._now(),))
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
        terminal = status in {'completed', 'stopped', 'failed', 'interrupted'}
        now = self._now()
        with self.conn:
            self.conn.execute('UPDATE sessions SET status=?, ended_at=? WHERE id=?',
                              (status, now if terminal else None, session_id))
            if terminal:
                self.conn.execute('UPDATE session_streams SET ended_at=? WHERE session_id=?',
                                  (now, session_id))
        return self.get(session_id)

    def append(self, session_id: str, chunk: dict):
        if not chunk['is_final']:
            return None
        row = {'id': uuid4().hex, 'session_id': session_id, 'stream': 'loopback',
               'start_ms': int(chunk['start_ms']), 'end_ms': int(chunk['end_ms']),
               'text': chunk['text'], 'language': chunk.get('language'),
               'confidence': chunk.get('confidence'), 'source': 'stream', 'revision': 1}
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
            'SELECT * FROM segments WHERE session_id=? ORDER BY start_ms, rowid', (session_id,))]
