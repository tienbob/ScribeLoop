"""SQLite storage — schema §3.1 of ARCHITECTURE.md, verbatim.

Delete semantics (§3.1): hard deletes with ``secure_delete=ON``; every purge
finishes with ``wal_checkpoint(TRUNCATE)``; full VACUUM only for bulk
migrations. Phase 0a check (verified 2026-10-21, see ``scripts/check_vec.py``
and ``tests/test_vectors.py``): SQLCipher 4.19 and the sqlite-vec 0.1.9
loadable extension coexist — the fallback (volume encryption + app-layer BLOB
encryption, §6) is not needed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as _sqlcipher
except (ImportError, OSError):
    _sqlcipher = None

# DB-API providers have distinct exception hierarchies; handle both explicitly.
DATABASE_ERRORS = (sqlite3.Error,) + ((_sqlcipher.Error,) if _sqlcipher else ())

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, started_at INTEGER, ended_at INTEGER,
  source TEXT, title TEXT, status TEXT,
  consent_confirmed INTEGER DEFAULT 0);

-- one row per captured stream ('loopback' | 'mic'): two WAVs per session
CREATE TABLE session_streams (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  stream TEXT, audio_path TEXT,
  sample_rate INTEGER, started_at INTEGER, ended_at INTEGER);

-- global registry: canonical people. Learning is OPT-IN per person (§6):
-- embeddings are never persisted for unmapped "Unknown-N" speakers.
CREATE TABLE speakers (
  id TEXT PRIMARY KEY, canonical_name TEXT, color TEXT,
  learn_enabled INTEGER DEFAULT 0, created_at INTEGER);

-- one row per exemplar, taken ONLY from offline-reconciled segments
-- (>= ~30 s, purity-checked); embedding model versioned for re-embedding;
-- source_session_id ties exemplars to origin so "forget this session"
-- can remove the exemplars derived from it
CREATE TABLE speaker_embeddings (
  id TEXT PRIMARY KEY, speaker_id TEXT REFERENCES speakers(id),
  source_session_id TEXT REFERENCES sessions(id),
  model_name TEXT, model_version TEXT,
  embedding BLOB, exemplar_ms INTEGER, created_at INTEGER);

-- per-session local labels ("Speaker 1"), optionally linked to a person
CREATE TABLE session_speakers (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  stream TEXT, local_label TEXT,
  speaker_id TEXT REFERENCES speakers(id),   -- NULL = unmapped
  confidence REAL, renamed_by_user INTEGER DEFAULT 0);

-- transcript. Partials (revision 0) are EPHEMERAL — in-memory only;
-- only confirmed (1) and offline-corrected (2) rows are persisted.
CREATE TABLE segments (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  stream TEXT, local_speaker_id TEXT REFERENCES session_speakers(id),
  start_ms INTEGER, end_ms INTEGER, text TEXT, language TEXT,
  confidence REAL, source TEXT,       -- 'stream' | 'offline'
  revision INTEGER);                  -- 1=confirmed, 2=offline-corrected
CREATE INDEX idx_segments_time ON segments(session_id, start_ms);

-- online->offline segments are N:M (boundaries differ): explicit mapping
-- table; the diff view falls back to time overlap when unmapped
CREATE TABLE segment_links (
  online_segment_id TEXT REFERENCES segments(id),
  offline_segment_id TEXT REFERENCES segments(id),
  overlap_ms INTEGER, method TEXT,
  PRIMARY KEY (online_segment_id, offline_segment_id));
CREATE INDEX idx_links_offline ON segment_links(offline_segment_id);

-- persisted remap table from speaker reconciliation (Flow 2)
CREATE TABLE speaker_remaps (
  session_id TEXT REFERENCES sessions(id),
  stream TEXT, online_label TEXT, offline_label TEXT, method TEXT, confidence REAL,
  PRIMARY KEY (session_id, stream, online_label));

CREATE TABLE words (
  id TEXT PRIMARY KEY, segment_id TEXT REFERENCES segments(id),
  session_id TEXT REFERENCES sessions(id),
  text TEXT, start_ms INTEGER, end_ms INTEGER, confidence REAL, source TEXT);
CREATE INDEX idx_words_time ON words(session_id, start_ms);

CREATE TABLE summaries (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  kind TEXT,                           -- 'rolling' | 'provisional' | 'final' | 'section'
  span_start_ms INTEGER, span_end_ms INTEGER,
  content_md TEXT, actions_json TEXT, model_id TEXT, created_at INTEGER);

-- RAG chunks carry time anchors + segment ids so Flow 4 citations
-- resolve to speaker names and timestamps
CREATE TABLE embeddings_chunks (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  chunk_start_ms INTEGER, chunk_end_ms INTEGER,
  segment_ids TEXT,                    -- JSON array
  chunk_text TEXT, model_name TEXT, vec BLOB);

-- audit log: session/state transitions, errors, agent actions ONLY
CREATE TABLE events (
  id INTEGER PRIMARY KEY, session_id TEXT, ts INTEGER,
  level TEXT, type TEXT, payload_json TEXT);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the DB with the §3.1 pragmas. ``:memory:`` for tests."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    configure(conn)
    return conn


def configure(conn) -> None:
    """Apply the shared safety pragmas after selecting/validating encryption."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA journal_mode=WAL")


def migrate(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Apply pending migrations (currently only v1 — the §3.1 schema)."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(DDL)
    if version < 2:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS recording_checkpoints (
          session_id TEXT REFERENCES sessions(id), stream TEXT,
          recorded_ms INTEGER NOT NULL DEFAULT 0,
          confirmed_ms INTEGER NOT NULL DEFAULT 0,
          diarized_ms INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(session_id, stream));
        CREATE TABLE IF NOT EXISTS speaker_turns (
          id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
          stream TEXT, start_ms INTEGER, end_ms INTEGER, label TEXT,
          confidence REAL);
        CREATE INDEX IF NOT EXISTS idx_turns_time ON speaker_turns(session_id,start_ms);
        """)
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    return conn


def purge_checkpoint(conn: sqlite3.Connection) -> None:
    """Finish a purge: WAL retains deleted content until checkpoint (§3.1)."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
