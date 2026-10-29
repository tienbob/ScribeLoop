"""Call end contracts: generated pipes and isolated SQLite only."""
import json
import sqlite3
import threading

import pytest
from scribeloop_core.capture.multiplexed import MultiplexedPCMSource
from scribeloop_core.pipeline.recording import RecordingRunner
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_multiplexed_capture import END, PREFIX, helper, packet


@pytest.mark.parametrize('stderr,expected', [
    ('Source ended: selected_app_exited\nother diagnostic\n', 'selected_app_exited'),
    ('Source ended: invented\n', None),
    ('prefix Source ended: selected_app_exited\n', None),
    ('Source ended: selected_app_exited\nSource ended: junk\n', 'selected_app_exited'),
    ('x'*3000+'\nSource ended: selected_app_exited\n'+'y'*3000, 'selected_app_exited'),
])
def test_marker_survives_cleanup_and_junk(tmp_path, stderr, expected):
    source = MultiplexedPCMSource(helper(tmp_path, PREFIX+packet(0,0)+packet(1,0)+END,
                                          f'sys.stderr.write({stderr!r})\n'))
    assert len(list(source.frames())) == 2
    assert source.end_reason == expected


def test_marker_without_completed_drain_is_not_an_end_reason(tmp_path):
    source = MultiplexedPCMSource(helper(tmp_path, PREFIX+packet(0,0)+packet(1,0),
        "sys.stderr.write('Source ended: selected_app_exited\\n')\n"))
    with pytest.raises(RuntimeError): list(source.frames())
    assert source.end_reason is None


@pytest.mark.parametrize('stop,capture,expected', [
    (None,None,'unknown'), ('manual_stop',None,'manual_stop'),
    ('manual_stop','selected_app_exited','selected_app_exited'),
    (None,'selected_app_exited','selected_app_exited'),
    *[(reason,'selected_app_exited',reason) for reason in
      ('duration_limit','auto_app_inactive','auto_microphone_released','app_shutdown')],
])
def test_resolution_table(stop, capture, expected):
    from scribeloop_core.pipeline.recording import resolve_end_reason
    assert resolve_end_reason(stop,capture) == expected


def test_stop_first_writer_and_validation():
    runner = RecordingRunner(None,None,None,None)
    runner.active_id = 's'
    barrier = threading.Barrier(3)
    def stop(reason):
        barrier.wait(); runner.stop('s',reason=reason)
    threads = [threading.Thread(target=stop,args=(reason,)) for reason in
               ('manual_stop','auto_app_inactive')]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    first = runner._stop_reason
    runner.stop('s',reason='auto_microphone_released')
    assert runner._stop_reason == first
    with pytest.raises(ValueError): runner.stop('s',reason='duration_limit')


def test_record_end_is_first_writer_logs_duplicates_and_propagates_constraints(caplog):
    conn = migrate(connect(':memory:')); store = SessionStore(conn)
    sid = store.create_recording('Fixture',{})['id']
    store.record_end(sid,'manual_stop')
    store.record_end(sid,'selected_app_exited')
    assert store.get(sid)['end_reason'] == 'manual_stop'
    assert 'duplicate recording_end' in caplog.text.lower()
    conn.execute("CREATE TRIGGER deny_end BEFORE INSERT ON events WHEN NEW.type='recording_end' "
                 "BEGIN SELECT RAISE(ABORT,'constraint fixture'); END")
    with pytest.raises(sqlite3.IntegrityError): store.record_end('other','manual_stop')
    conn.close()


def legacy():
    conn = migrate(connect(':memory:'))
    conn.execute('DROP INDEX IF EXISTS idx_events_recording_end')
    conn.execute('PRAGMA user_version=12')
    for reason in ('manual_stop','selected_app_exited'):
        conn.execute('INSERT INTO events(session_id,type,payload_json) VALUES (?,?,?)',
                     ('s','recording_end',json.dumps({'end_reason':reason})))
    conn.commit()
    return conn


def test_migration_dedupes_first_row():
    assert sqlite3.sqlite_version_info >= (3,24,0)
    conn = legacy(); migrate(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 13
    rows = conn.execute("SELECT payload_json FROM events WHERE type='recording_end'").fetchall()
    assert len(rows) == 1 and json.loads(rows[0][0])['end_reason'] == 'manual_stop'
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO events(session_id,type) VALUES ('s','recording_end')")
    conn.close()


def test_migration_rollback_includes_dedupe_index_and_version():
    conn = legacy()
    class FailVersion:
        def __getattr__(self,name): return getattr(conn,name)
        def __enter__(self): conn.__enter__(); return self
        def __exit__(self,*args): return conn.__exit__(*args)
        def execute(self,sql,*args):
            if sql == 'PRAGMA user_version=13': raise sqlite3.OperationalError('injected')
            return conn.execute(sql,*args)
    with pytest.raises(sqlite3.OperationalError): migrate(FailVersion())
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 12
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_events_recording_end'").fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='recording_end'").fetchone()[0] == 2
    conn.close()


@pytest.mark.parametrize('duration,count,eligible', [(0,0,False),(29999,0,False),(30000,0,True),(0,1,True)])
def test_short_guard_logs_only_skips(caplog,duration,count,eligible):
    from scribeloop_core.pipeline.recording import automatic_notes_eligible
    assert automatic_notes_eligible('fixture',duration,count) == eligible
    assert ('fixture' in caplog.text and str(duration) in caplog.text) == (not eligible)
