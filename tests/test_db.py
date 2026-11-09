"""Schema tests (§3.1): migration, pragmas, FK enforcement."""
import sqlite3
from pathlib import Path

import pytest
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import ROSTER_QUERY, SessionStore


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
    assert db.execute("PRAGMA user_version").fetchone()[0] == 15


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


def test_event_index_upgrades_v13_and_serves_history_queries(db):
    db.execute('DROP INDEX idx_events_session_type_id')
    db.execute('PRAGMA user_version=13')
    db.commit()
    migrate(db)
    assert db.execute('PRAGMA user_version').fetchone()[0] == 15
    for direction in ('ASC', 'DESC'):
        plan = db.execute('EXPLAIN QUERY PLAN SELECT payload_json FROM events '
                          f'WHERE session_id=? AND type=? ORDER BY id {direction} LIMIT 1',
                          ('fixture', 'summary_outcome')).fetchall()
        assert any('SEARCH events USING INDEX idx_events_session_type_id' in row[3]
                   for row in plan)
    migrate(db)  # Reopening is idempotent.


def test_event_index_migration_rolls_back_ddl_and_version(db):
    db.execute('DROP INDEX idx_events_session_type_id')
    db.execute('PRAGMA user_version=13')
    db.commit()
    class FailVersion:
        def __getattr__(self, name): return getattr(db, name)
        def __enter__(self): db.__enter__(); return self
        def __exit__(self, *args): return db.__exit__(*args)
        def execute(self, sql, *args):
            if sql == 'PRAGMA user_version=14':
                raise sqlite3.OperationalError('injected')
            return db.execute(sql, *args)
    with pytest.raises(sqlite3.OperationalError, match='injected'):
        migrate(FailVersion())
    assert db.execute('PRAGMA user_version').fetchone()[0] == 13
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_events_session_type_id'").fetchone() is None


def test_roster_covering_index_migration_and_rollback(db):
    db.execute('DROP INDEX idx_segments_revision_speaker')
    db.execute('PRAGMA user_version=14');db.commit()
    class FailVersion:
        def __getattr__(self,name):return getattr(db,name)
        def __enter__(self):db.__enter__();return self
        def __exit__(self,*args):return db.__exit__(*args)
        def execute(self,sql,*args):
            if sql=='PRAGMA user_version=15':raise sqlite3.OperationalError('injected')
            return db.execute(sql,*args)
    with pytest.raises(sqlite3.OperationalError):migrate(FailVersion())
    assert db.execute('PRAGMA user_version').fetchone()[0]==14
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_segments_revision_speaker'").fetchone() is None
    migrate(db)
    plan=' '.join(str(tuple(r)) for r in db.execute('EXPLAIN QUERY PLAN SELECT id FROM segments WHERE session_id=? AND revision=? AND local_speaker_id=?',('s',1,'speaker')))
    assert 'idx_segments_revision_speaker' in plan and 'SEARCH' in plan


def test_full_roster_query_is_one_pass_and_preserves_samples(db):
    store=SessionStore(db)
    sid=store.create_recording('Roster scale',{})['id']
    for index in range(120):
        store.append(sid,{'text':f'Words {index%2}','start_ms':index*1000,
            'end_ms':index*1000+999,'is_final':True,'speaker_key':f'deepgram:{sid}:mic:{index%2}'},stream='mic')
    plan=' '.join(str(tuple(row)) for row in db.execute('EXPLAIN QUERY PLAN '+ROSTER_QUERY,
        (sid,1,sid,1,sid,1,sid,1)))
    # The utterance CTE is explicitly materialized in the query source; SQLite
    # may render it as either a materialization or a coroutine in EXPLAIN.
    assert 'utterances AS MATERIALIZED' in ROSTER_QUERY
    assert 'idx_segments_revision_speaker' in plan
    roster=store.speaker_roster(sid,1)
    assert [row['person_number'] for row in roster]==[1,2]
    assert roster[0]['sample']=='Words 0' and roster[0]['start_ms']==0
    assert roster[1]['sample']=='Words 1' and roster[1]['start_ms']==1000


def test_cloud_usage_cache_avoids_reparsing_reservation_tail(db):
    store=SessionStore(db)
    sid=store.create_recording('Cloud usage',{})['id']
    reservation={'provider':'deepgram','model':'nova-3','stream':'mic','billed_audio_s':10,
                 'estimated_usd':.001,'reserved_at_ms':1}
    usage=store.reserve_cloud(sid,reservation)
    assert usage['requests']==1
    reads=[]
    db.set_trace_callback(reads.append)
    for index in range(10):
        usage=store.reserve_cloud(sid,{**reservation,'reserved_at_ms':index+2})
    db.set_trace_callback(None)
    assert usage['requests']==11 and usage['estimated_usd']==pytest.approx(.011)
    assert not any('SELECT payload_json' in query for query in reads)
    assert store.cloud_usage(sid)==usage
