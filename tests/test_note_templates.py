"""F3 contract/storage checks; no providers, credentials or capture."""
import json
import sqlite3

import pytest
from pydantic import ValidationError
from scribeloop_core.llm.note_templates import BUILTINS, NoteTemplate, definition_hash
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.note_templates import NoteTemplateStore
from scribeloop_core.storage.sessions import SessionStore


@pytest.fixture()
def workspace(tmp_path):
    conn = migrate(connect(tmp_path / 'templates.db'))
    yield conn, SessionStore(conn), NoteTemplateStore(conn)
    conn.close()


def custom(name='My planning notes'):
    value = BUILTINS['planning'].model_dump()
    value['name'] = name
    return value


def meeting(sessions):
    sid = sessions.create_recording('Existing meeting', {})['id']
    sessions.append(sid, {'text': 'We agreed to review the launch plan.', 'start_ms': 0, 'end_ms': 1000, 'is_final': True})
    return sid


def generation(templates, sid, **kwargs):
    return templates.begin_generation(sid, 1, provider='OpenAI', model='test-model',
                                      destination='https://api.example.test/v1', **kwargs)


def test_builtins_and_localized_presentation():
    assert set(BUILTINS) == {'general', 'stand-up', 'customer-call', 'interview', 'planning', 'one-to-one'}
    for template in BUILTINS.values():
        assert len(template.presentation()) == len(template.presentation('vi')) == 6
        assert template.presentation('vi')[0]['label'] == 'Tổng quan'
        assert len(definition_hash(template.model_dump())) == 64
    value = custom()
    value['sections'] = [value['sections'][0]]
    assert len(NoteTemplate.model_validate(value).presentation()) == 6, 'Omitted sections cannot hide supported evidence'


@pytest.mark.parametrize('change', [
    {'name': ' '}, {'name': 'x' * 81}, {'focus': 'x' * 2001}, {'unknown': 'schema override'},
    {'sections': []}, {'sections': [{'key': 'invented', 'label': 'Facts'}]},
    {'sections': [{'key': 'overview', 'label': ' '}]},
    {'sections': [{'key': 'overview', 'label': 'A'}, {'key': 'overview', 'label': 'B'}]},
    {'sections': [{'key': 'action_items', 'label': 'Tasks'}]},
])
def test_invalid_definitions_rejected(change):
    with pytest.raises(ValidationError):
        NoteTemplate.model_validate({**custom(), **change})


def test_default_attached_once_and_reopen(workspace):
    conn, sessions, templates = workspace
    first = meeting(sessions)
    templates.set_default('stand-up')
    second = meeting(sessions)
    templates.set_default('planning')
    assert templates.selection(first)['id'] == 'general'
    assert templates.selection(second)['id'] == 'stand-up'
    templates.select(first, 'interview')
    path = conn.execute('PRAGMA database_list').fetchone()['file']
    other = connect(path)
    try:
        reopened = NoteTemplateStore(other)
        assert reopened.selection(first)['id'] == 'interview'
        assert reopened.default_id() == 'planning'
    finally:
        other.close()
    assert conn.execute('SELECT COUNT(*) FROM note_generations').fetchone()[0] == 0
    with pytest.raises(ValueError):
        templates.select('missing-meeting', 'general')


def test_custom_crud_version_and_bounds(workspace):
    conn, _, templates = workspace
    saved = templates.create(custom())
    changed = templates.update(saved['id'], custom('Revised notes'), expected_version=1)
    assert changed['version'] == 2
    assert changed['definition_hash'] != saved['definition_hash']
    with pytest.raises(ValueError, match='changed'):
        templates.update(saved['id'], custom(), expected_version=1)
    with pytest.raises(ValueError, match='built-in'):
        templates.update('general', custom(), expected_version=1)
    with pytest.raises(ValueError):
        templates.delete('general')
    for i in range(49):
        templates.create(custom(str(i)))
    with pytest.raises(ValueError, match='50'):
        templates.create(custom())
    assert len(templates.list()) == 56
    with pytest.raises(sqlite3.IntegrityError, match='limit'):
        conn.execute("INSERT INTO note_templates VALUES ('bypass','Bypass',1,0,'{}',0)")
    conn.rollback()


def test_generation_snapshot_survives_edits_and_deletion(workspace):
    conn, sessions, templates = workspace
    sid = meeting(sessions)
    saved = templates.create(custom())
    templates.set_default(saved['id'])
    templates.select(sid, saved['id'])
    original = generation(templates, sid, guidance='Viết bằng tiếng Việt.')
    templates.update(saved['id'], custom('Changed'), expected_version=1)
    newer = generation(templates, sid, guidance='Focus on risks.')
    assert original['id'] != newer['id']
    assert original['snapshot_hash'] != newer['snapshot_hash']
    assert newer['template']['version'] == 2
    assert templates.delete(saved['id']) == {'affected_meetings': 1, 'default_reset': True}
    assert templates.default_id() == templates.selection(sid)['id'] == 'general'
    assert templates.generation(original['id']) == original
    assert templates.generation(newer['id']) == newer
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        conn.execute('UPDATE note_generations SET snapshot_json=\'{}\' WHERE id=?', (original['id'],))
    conn.rollback()
    conn.execute('DELETE FROM sessions WHERE id=?', ('absent',))  # no-op cannot disturb snapshots
    conn.commit()
    assert templates.generation(original['id'])['guidance'] == 'Viết bằng tiếng Việt.'


@pytest.mark.parametrize('destination', ['https://name:secret@example.test/v1', 'https://example.test?key=secret',
                                         'https://example.test/#secret', 'file:///secret', ''])
def test_snapshot_rejects_private_endpoint_fields(workspace, destination):
    conn, sessions, templates = workspace
    sid = meeting(sessions)
    with pytest.raises(ValueError):
        templates.begin_generation(sid, 1, provider='test', model='test', destination=destination)
    assert conn.execute('SELECT COUNT(*) FROM note_generations').fetchone()[0] == 0


def test_populated_v23_migration_preserves_notes_and_does_not_invent_history(workspace):
    conn, sessions, _ = workspace
    sid = meeting(sessions)
    sessions.create_transcript_notes(sid)
    notes = sessions.notes(sid)
    segments = sessions.segments(sid)
    with conn:
        for table in ('note_generations', 'session_note_templates', 'note_template_preferences', 'note_templates'):
            conn.execute(f'DROP TABLE {table}')
        conn.execute('PRAGMA user_version=23')
    migrate(conn)
    migrate(conn)
    templates = NoteTemplateStore(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 24
    assert sessions.notes(sid) == notes and sessions.segments(sid) == segments
    assert templates.selection(sid)['id'] == 'general'
    assert len(templates.list()) == 6
    assert conn.execute('SELECT COUNT(*) FROM note_generations').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_snapshot_requires_existing_source_and_has_no_key_field(workspace):
    _, sessions, templates = workspace
    sid = meeting(sessions)
    with pytest.raises(ValueError):
        templates.begin_generation(sid, 99, provider='test', model='test', destination='https://example.test')
    snapshot = generation(templates, sid)
    assert 'api_key' not in json.dumps(snapshot)


def test_creation_rollback_and_conversation_deletion(workspace):
    conn, sessions, templates = workspace
    with pytest.raises(TypeError):
        sessions.create_recording('Invalid metadata', {'cannot_encode': set()})
    assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM session_note_templates').fetchone()[0] == 0
    sid = meeting(sessions)
    saved = templates.create(custom())
    templates.select(sid, saved['id'])
    generation(templates, sid)
    sessions.finish_delete(sid)
    assert conn.execute('SELECT COUNT(*) FROM note_generations').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM session_note_templates').fetchone()[0] == 0
    assert templates.get(saved['id'])['definition']['name'] == 'My planning notes'


def test_template_policy_survives_real_encrypted_reopen(tmp_path):
    pytest.importorskip('sqlcipher3.dbapi2')
    from scribeloop_core.storage.encrypted import encrypted_database
    from test_encrypted_storage import MemoryKeys

    keys = MemoryKeys()
    path = tmp_path / 'templates.sqlcipher'
    with encrypted_database(path, keys) as conn:
        sessions, templates = SessionStore(conn), NoteTemplateStore(conn)
        sid = meeting(sessions)
        template = templates.create(custom('Private planning format'))
        templates.select(sid, template['id'])
        original = generation(templates, sid, guidance='A private instruction stored in SQLCipher')
    with encrypted_database(path, keys) as conn:
        templates = NoteTemplateStore(conn)
        assert templates.selection(sid)['id'] == template['id']
        assert templates.generation(original['id']) == original
    raw = path.read_bytes()
    assert b'Private planning format' not in raw
    assert b'A private instruction' not in raw
