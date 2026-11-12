"""Vector index for session retrieval (Flow 4).

sqlite-vec behind a ``VectorIndex`` interface (§10): version PINNED, swappable
for Qdrant if the index outgrows ~10^7 chunks. Phase 0a checks — both verified
(2026-10-21) in ``scripts/check_vec.py`` and ``tests/test_vectors.py``:
- SQLCipher + sqlite-vec coexistence: OK — vec0 loads into a SQLCipher 4.19
  encrypted DB and KNN queries work (build-flag dependent, §6; fallback
  volume encryption + app-layer BLOB encryption NOT needed).
- vec0 delete-scrub: OK — with ``secure_delete=ON`` + ``wal_checkpoint``
  ``(TRUNCATE)``, deleted vector bytes are absent from the raw DB file
  (§3.1); the rebuild-into-fresh-table fallback is unnecessary.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Protocol
from uuid import uuid4

import sqlite_vec

# ---------------------------------------------------------------------------
# Contract (unchanged)
# ---------------------------------------------------------------------------

class VectorIndex(Protocol):
    def add(self, session_id: str, chunk_start_ms: int, chunk_end_ms: int,
            segment_ids: list[str], chunk_text: str, vector: bytes) -> None: ...

    def search(self, vector: bytes, k: int = 8) -> list[dict]: ...

    def delete_session(self, session_id: str) -> None: ...


# ---------------------------------------------------------------------------
# sqlite-vec implementation
# ---------------------------------------------------------------------------

def load_vec_extension(conn: sqlite3.Connection) -> None:
    """Enable and load the sqlite-vec extension into a plain sqlite3 connection."""
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


class SqliteVecIndex:
    """sqlite-vec ``vec0`` implementation of ``VectorIndex`` (§10).

    Wraps the caller's connection (which ``db.connect``/``db.migrate`` opens
    and applies pragmas to). Creates the ``embeddings_chunks`` vec0 virtual
    table on demand if it does not already exist.
    """

    def __init__(self, conn: sqlite3.Connection, dim: int = 384, *, generation: str = 'legacy') -> None:
        self._conn = conn
        self._dim = dim
        if not isinstance(generation,str) or not generation or len(generation)>500:
            raise ValueError('Invalid vector generation')
        self.generation = generation
        # Never interpolate provider/model text into SQL. A generation-specific
        # vec0 table makes a dimensionality mismatch structurally impossible.
        self._table = ('embeddings_chunks_vec' if generation == 'legacy' else
                       'embeddings_chunks_vec_' + hashlib.sha256(generation.encode()).hexdigest()[:16])
        load_vec_extension(conn)
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._table} USING vec0("
            f" id integer primary key, vec float[{dim}])"
        )
        existing=conn.execute('SELECT table_name,dimensions FROM embedding_index_generations WHERE generation=?',
                              (generation,)).fetchone()
        if existing and (existing[0] != self._table or existing[1] != dim):
            raise ValueError('Vector generation shape mismatch')
        conn.execute('INSERT OR IGNORE INTO embedding_index_generations '
                     '(generation,table_name,dimensions,created_at) VALUES (?,?,?,?)',
                     (generation,self._table,dim,int(time.time()*1000)))
        conn.commit()

    def add(self, session_id: str, chunk_start_ms: int, chunk_end_ms: int,
            segment_ids: list[str], chunk_text: str, vector: bytes) -> None:
        """Insert a chunk (metadata + vector) as a new row."""
        # Metadata goes to the plain relational table; the vec0 table holds
        # the vector and a foreign key back by rowid.
        with self._conn:
            self._add(session_id, chunk_start_ms, chunk_end_ms, segment_ids, chunk_text, vector)

    def _add(self, session_id: str, chunk_start_ms: int, chunk_end_ms: int,
             segment_ids: list[str], chunk_text: str, vector: bytes, *, source_spans=None) -> None:
        cur = self._conn.execute(
            "INSERT INTO embeddings_chunks("
            " id, session_id, chunk_start_ms, chunk_end_ms,"
            " segment_ids, chunk_text, model_name, vec, generation, source_spans)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                session_id,
                chunk_start_ms,
                chunk_end_ms,
                json.dumps(segment_ids),
                chunk_text,
                "sqlite-vec-v0.1.9",  # PINNED version (§10)
                vector,
                self.generation,
                json.dumps(source_spans or []),
            ),
        )
        self._conn.execute(
            f"INSERT INTO {self._table}(id, vec) VALUES (?, ?)",
            (cur.lastrowid, vector),
        )

    def search(self, vector: bytes, k: int = 8, *, session_ids: list[str] | tuple[str,...] | None = None,
               person_id=None, date_from=None, date_to=None) -> list[dict]:
        """KNN search: returns top-k chunks joined with their metadata.

        Use vec0's explicit k constraint: Ubuntu 22.04's SQLite cannot pass
        LIMIT constraints to virtual tables. The same query works on newer SQLite.
        """
        k = max(1, int(k))
        if session_ids is not None and (not session_ids or len(session_ids)>100 or
                any(not isinstance(value,str) for value in session_ids)):
            raise ValueError('Invalid vector meeting scope')
        if session_ids is not None or person_id or date_from is not None or date_to is not None:
            # Exact distance over scoped metadata avoids global top-k starvation.
            # Vectors remain inside the encrypted database; no outside rows can
            # consume the selected meetings' candidate budget.
            constraints=['m.generation=?']; args=[vector,self.generation]
            if session_ids is not None:
                constraints.append('m.session_id IN ('+','.join('?' for _ in session_ids)+')')
                args.extend(session_ids)
            if date_from is not None:
                constraints.append('EXISTS (SELECT 1 FROM sessions s WHERE s.id=m.session_id AND s.started_at>=?)')
                args.append(date_from)
            if date_to is not None:
                constraints.append('EXISTS (SELECT 1 FROM sessions s WHERE s.id=m.session_id AND s.started_at<?)')
                args.append(date_to)
            if person_id:
                constraints.append('EXISTS (SELECT 1 FROM search_documents d WHERE d.id IN '
                    '(SELECT value FROM json_each(m.segment_ids)) AND (d.speaker_id=? OR EXISTS '
                    '(SELECT 1 FROM person_links pl WHERE pl.session_id=d.session_id AND pl.revision=d.revision AND pl.person_id=?)))')
                args.extend((person_id,person_id))
            args.append(k)
            rows=self._conn.execute(
                'SELECT m.id,m.session_id,m.chunk_start_ms,m.chunk_end_ms,m.segment_ids,m.chunk_text,'
                'vec_distance_L2(m.vec,?) AS distance,m.source_spans FROM embeddings_chunks m WHERE '+
                ' AND '.join(constraints)+' ORDER BY distance,m.session_id,m.chunk_start_ms,m.rowid LIMIT ?',args).fetchall()
            return self._hits(rows)
        fetch_k=min(k,2000)
        scope=''
        args: list[object]=[vector,fetch_k,self.generation]
        if session_ids is not None:
            scope=' AND m.session_id IN ('+','.join('?' for _ in session_ids)+')'
            args.extend(session_ids)
        args.append(k)
        rows = self._conn.execute(
            "SELECT m.id, m.session_id, m.chunk_start_ms, m.chunk_end_ms,"
            " m.segment_ids, m.chunk_text, v.distance,m.source_spans"
            " FROM ("
            f"   SELECT id, distance FROM {self._table}"
            "  WHERE vec MATCH ? AND k = ? ORDER BY distance"
            " ) v JOIN embeddings_chunks m ON m.rowid = v.id WHERE m.generation=?"+scope+
            " ORDER BY v.distance LIMIT ?",
            args,
        ).fetchall()
        return self._hits(rows)

    @staticmethod
    def _hits(rows):
        # Positional access supports both tuples and sqlite3.Row connections.
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "chunk_start_ms": r[2],
                "chunk_end_ms": r[3],
                "segment_ids": json.loads(r[4]),
                "chunk_text": r[5],
                "distance": r[6],
                "source_spans": json.loads(r[7]),
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> None:
        """Delete all vector+metadata rows for a session (purge path, §3.1)."""
        rowids = [
            r[0]
            for r in self._conn.execute(
                "SELECT rowid FROM embeddings_chunks WHERE session_id = ? AND generation = ?", (session_id,self.generation)
            )
        ]
        with self._conn:
            self._conn.executemany(
                f"DELETE FROM {self._table} WHERE id = ?", ((rowid,) for rowid in rowids)
            )
            self._conn.execute(
                "DELETE FROM embeddings_chunks WHERE session_id = ? AND generation = ?", (session_id,self.generation)
            )


def purge_session_vectors(conn,session_id):
    """Purge every generation, including legacy tables, in the caller transaction."""
    import re
    tables=[row[0] for row in conn.execute('SELECT table_name FROM embedding_index_generations')]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='embeddings_chunks_vec'").fetchone():
        tables.append('embeddings_chunks_vec')
    if tables:
        load_vec_extension(conn)
    for table in set(tables):
        if table != 'embeddings_chunks_vec' and not re.fullmatch(r'embeddings_chunks_vec_[0-9a-f]{16}',table):
            raise ValueError('Invalid vector table registry')
        conn.execute(f'DELETE FROM {table} WHERE id IN (SELECT rowid FROM embeddings_chunks WHERE session_id=?)',
                     (session_id,))
    conn.execute('DELETE FROM embeddings_chunks WHERE session_id=?',(session_id,))
    conn.execute('DELETE FROM embedding_index_state WHERE session_id=?',(session_id,))
