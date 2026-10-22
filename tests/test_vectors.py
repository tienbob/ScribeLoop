"""Tests for the sqlite-vec storage layer (ARCHITECTURE.md §6 / §3.1 Phase 0a).

- SqliteVecIndex add / search / delete_session round-trip on a plain sqlite3
  connection (runs anywhere; needs only the pinned sqlite-vec wheel).
- vec0 delete-scrub: after DELETE + wal_checkpoint(TRUNCATE), the vector bytes
  must be absent from the raw DB file (secure_delete=ON semantics, §3.1).
- SQLCipher + sqlite-vec coexistence — requires the sqlcipher CLI (skip if not
  installed; on CI use the sqlcipher-provisioned job or local dev machine).
"""
from __future__ import annotations

import shutil
import sqlite3
import struct
import subprocess
from pathlib import Path

import pytest
import sqlite_vec
from scribeloop_core.storage.vectors import SqliteVecIndex, load_vec_extension

VEC = Path(sqlite_vec.loadable_path())
DIM = 4


@pytest.fixture()
def conn():
    from scribeloop_core.storage.db import connect, migrate

    c = migrate(connect(":memory:"))
    c.executemany("INSERT INTO sessions(id) VALUES (?)", [("s1",), ("s2",)])
    c.commit()
    yield c
    c.close()


def vec(*fs: float) -> bytes:
    return struct.pack(f"{len(fs)}f", *fs)


def test_add_and_search_roundtrip(conn):
    idx = SqliteVecIndex(conn, dim=DIM)
    idx.add("s1", 0, 1000, ["seg1"], "hello world", vec(1.0, 0.0, 0.0, 0.0))
    idx.add("s2", 1000, 2000, ["seg2"], "goodbye", vec(0.0, 1.0, 0.0, 0.0))
    hits = idx.search(vec(1.0, 0.0, 0.0, 0.0), k=1)
    assert len(hits) == 1
    assert hits[0]["session_id"] == "s1"
    assert hits[0]["chunk_text"] == "hello world"


def test_delete_session_removes_rows(conn):
    idx = SqliteVecIndex(conn, dim=DIM)
    idx.add("s1", 0, 1000, ["seg1"], "a", vec(1.0, 0.0, 0.0, 0.0))
    idx.add("s2", 0, 1000, ["seg2"], "b", vec(0.0, 1.0, 0.0, 0.0))
    idx.delete_session("s1")
    hits = idx.search(vec(1.0, 0.0, 0.0, 0.0), k=8)
    assert all(h["session_id"] != "s1" for h in hits)
    n = conn.execute("SELECT COUNT(*) FROM embeddings_chunks WHERE session_id='s1'").fetchone()[0]
    assert n == 0


def test_vec0_delete_scrub(tmp_path: Path):
    """§3.1: deleted vector bytes must not survive DELETE + checkpoint."""
    db = tmp_path / "scrub.db"
    c = sqlite3.connect(db)
    c.execute("PRAGMA secure_delete=ON")
    c.execute("PRAGMA journal_mode=WAL")
    load_vec_extension(c)
    c.execute("CREATE VIRTUAL TABLE v USING vec0(id integer primary key, vec float[4])")
    pattern = vec(3.14159, 2.71828, 1.41421, 1.61803)
    c.execute("INSERT INTO v(id, vec) VALUES (1, ?)", (pattern,))
    c.commit()

    c.execute("DELETE FROM v WHERE id = 1")
    c.commit()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()

    raw = db.read_bytes()
    wal = tmp_path / "scrub.db-wal"
    if wal.exists():
        raw += wal.read_bytes()
    assert pattern not in raw, "deleted vector bytes still present in raw file"


@pytest.mark.skipif(shutil.which("sqlcipher") is None,
                    reason="sqlcipher CLI not installed")
def test_sqlcipher_vec_coexistence(tmp_path: Path):
    """§6: sqlite-vec loads and works inside a SQLCipher-encrypted DB."""
    db, script = tmp_path / "t.db", tmp_path / "t.sql"
    script.write_text(
        f".load {VEC}\n"
        "PRAGMA key = 'x';\n"
        "CREATE VIRTUAL TABLE v USING vec0(id integer primary key, vec float[4]);\n"
        "INSERT INTO v(id, vec) VALUES (1, x'0000803f000000400000404000004040');\n"
        "SELECT id, distance FROM v WHERE vec MATCH x'0000803f000000400000404000004040'"
        " ORDER BY distance LIMIT 1;\n",
    )
    r = subprocess.run(
        ["sqlcipher", str(db)], input=script.read_text(), capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, r.stderr
    assert "1|" in r.stdout, r.stdout


def test_failed_vector_insert_rolls_back_metadata(conn):
    idx = SqliteVecIndex(conn, dim=DIM)
    with pytest.raises(sqlite3.OperationalError):
        idx.add("s1", 0, 1000, ["seg1"], "bad dimension", vec(1.0))
    assert conn.execute("SELECT COUNT(*) FROM embeddings_chunks").fetchone()[0] == 0


def test_multiple_chunks_keep_distinct_ids_and_metadata(conn):
    idx = SqliteVecIndex(conn, dim=DIM)
    for n in range(3):
        idx.add("s1", n, n + 1, [f"seg{n}"], str(n), vec(float(n), 0, 0, 0))
    hits = idx.search(vec(0, 0, 0, 0))
    assert len({hit["id"] for hit in hits}) == 3
    assert hits[0]["segment_ids"] == ["seg0"]
    assert [hit["distance"] for hit in hits] == sorted(hit["distance"] for hit in hits)
