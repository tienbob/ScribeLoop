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

import json
import sqlite3
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

    def __init__(self, conn: sqlite3.Connection, dim: int = 384) -> None:
        self._conn = conn
        self._dim = dim
        load_vec_extension(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_chunks_vec USING vec0("
            f" id integer primary key, vec float[{dim}])"
        )
        conn.commit()

    def add(self, session_id: str, chunk_start_ms: int, chunk_end_ms: int,
            segment_ids: list[str], chunk_text: str, vector: bytes) -> None:
        """Insert a chunk (metadata + vector) as a new row."""
        # Metadata goes to the plain relational table; the vec0 table holds
        # the vector and a foreign key back by rowid.
        with self._conn:
            self._add(session_id, chunk_start_ms, chunk_end_ms, segment_ids, chunk_text, vector)

    def _add(self, session_id: str, chunk_start_ms: int, chunk_end_ms: int,
             segment_ids: list[str], chunk_text: str, vector: bytes) -> None:
        cur = self._conn.execute(
            "INSERT INTO embeddings_chunks("
            " id, session_id, chunk_start_ms, chunk_end_ms,"
            " segment_ids, chunk_text, model_name, vec)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                session_id,
                chunk_start_ms,
                chunk_end_ms,
                json.dumps(segment_ids),
                chunk_text,
                "sqlite-vec-v0.1.9",  # PINNED version (§10)
                vector,
            ),
        )
        self._conn.execute(
            "INSERT INTO embeddings_chunks_vec(id, vec) VALUES (?, ?)",
            (cur.lastrowid, vector),
        )

    def search(self, vector: bytes, k: int = 8) -> list[dict]:
        """KNN search: returns top-k chunks joined with their metadata.

        Note: vec0 requires a *literal* LIMIT (a bound ``?`` is rejected with
        "A LIMIT or 'k = ?' constraint is required"), so k is inlined after
        clamping to a sane positive integer.
        """
        k = max(1, int(k))
        rows = self._conn.execute(
            "SELECT m.id, m.session_id, m.chunk_start_ms, m.chunk_end_ms,"
            " m.segment_ids, m.chunk_text, v.distance"
            " FROM ("
            "   SELECT id, distance FROM embeddings_chunks_vec"
            f"  WHERE vec MATCH ? ORDER BY distance LIMIT {k}"
            " ) v JOIN embeddings_chunks m ON m.rowid = v.id ORDER BY v.distance",
            (vector,),
        ).fetchall()
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
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> None:
        """Delete all vector+metadata rows for a session (purge path, §3.1)."""
        rowids = [
            r[0]
            for r in self._conn.execute(
                "SELECT rowid FROM embeddings_chunks WHERE session_id = ?", (session_id,)
            )
        ]
        with self._conn:
            self._conn.executemany(
                "DELETE FROM embeddings_chunks_vec WHERE id = ?", ((rowid,) for rowid in rowids)
            )
            self._conn.execute(
                "DELETE FROM embeddings_chunks WHERE session_id = ?", (session_id,)
            )
