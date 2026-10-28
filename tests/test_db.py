"""Schema tests (§3.1): migration, pragmas, FK enforcement."""
import sqlite3
from pathlib import Path

import pytest
from scribeloop_core.storage.db import connect, migrate


@pytest.fixture()
def db(tmp_path: Path):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    yield conn
    conn.close()


def test_migrate_creates_schema(db):
    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    expected = {
        "sessions", "session_streams", "speakers", "speaker_embeddings",
        "session_speakers", "segments", "segment_links", "speaker_remaps",
        "words", "summaries", "embeddings_chunks", "events", "transcript_revisions",
    }
    assert expected <= tables
    assert db.execute("PRAGMA user_version").fetchone()[0] == 12


def test_pragmas_on(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_foreign_keys_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO segments (id, session_id, stream, local_speaker_id,"
            " start_ms, end_ms, text, revision, source)"
            " VALUES ('s1', 'no-such-session', 'loopback', NULL, 0, 100, 'x', 1, 'stream')"
        )


def test_insert_session_and_stream(db):
    db.execute(
        "INSERT INTO sessions (id, started_at, source, status, consent_confirmed)"
        " VALUES ('sess1', 1, 'loopback', 'live', 1)"
    )
    db.execute(
        "INSERT INTO session_streams (id, session_id, stream, audio_path, sample_rate)"
        " VALUES ('st1', 'sess1', 'loopback', 'audio/x.wav', 16000)"
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) c FROM session_streams").fetchone()["c"] == 1
