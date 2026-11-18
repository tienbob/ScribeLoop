"""Native template flows: draft commits, conflict recovery and saved presentation."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.email_exports import summary_blocks
from scribeloop_core.api.note_templates import library, save_library
from scribeloop_core.api.server import create_app
from scribeloop_core.llm.grounded import escape_md, export_markdown, transcript_hash
from scribeloop_core.llm.note_templates import BUILTINS
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.note_templates import NoteTemplateStore
from scribeloop_core.storage.sessions import SessionStore
from test_desktop import Vault, envelope, receive


@pytest.fixture()
def store(tmp_path):
    conn = migrate(connect(tmp_path / 'templates.db'))
    yield SessionStore(conn)
    conn.close()


def meeting(store, language='en'):
    sid = store.create_recording('Template UI', {})['id']
    store.append(sid, {'text':'We agreed to test the desktop workflow.', 'language':language,
                      'start_ms':0, 'end_ms':1000, 'is_final':True})
    store.transition(sid, 'completed')
    return sid


def draft_command(store):
    return {'action':'save_note_templates', 'templates':[], 'default_id':'general',
            'library_revision':library(store)['library_revision']}


def custom():
    return {'id':'custom-' + uuid4().hex, 'definition':BUILTINS['planning'].model_dump()}


def test_library_draft_commit_conflict_delete_and_no_generation(store):
    templates = NoteTemplateStore(store.conn)
    sid = meeting(store)
    draft = custom()
    command = {**draft_command(store), 'templates':[draft], 'default_id':draft['id']}
    before = library(store)
    assert library(store) == before, 'Opening/abandoning a draft has no side effects'
    saved = save_library(store, command)
    assert saved['default_id'] == draft['id'] and templates.selection(sid)['id'] == 'general'
    templates.select(sid, draft['id'])
    assert templates.selection(meeting(store))['id'] == draft['id']
    with pytest.raises(ValueError, match='changed'):
        save_library(store, command)
    assert templates.get(draft['id'])['version'] == 1
    command = {**draft_command(store), 'templates':[deepcopy(draft)], 'default_id':draft['id']}
    command['templates'][0]['definition']['name'] = 'Revised'
    save_library(store, command)
    assert templates.get(draft['id'])['version'] == 2
    result = save_library(store, draft_command(store))
    assert result['affected_meetings'] == 2 and templates.selection(sid)['id'] == 'general'
    assert templates.default_id() == 'general'
    assert store.conn.execute('SELECT COUNT(*) FROM note_generations').fetchone()[0] == 0


def test_invalid_multi_edit_draft_is_atomic(store):
    draft = custom()
    save_library(store, {**draft_command(store), 'templates':[draft]})
    before = library(store)
    invalid = custom(); invalid['definition']['sections'][0]['label'] = ''
    with pytest.raises(ValueError):
        save_library(store, {**draft_command(store), 'templates':[invalid]})
    assert library(store) == before, 'Validation cannot delete an existing custom template'
    with pytest.raises(ValueError):
        save_library(store, {**draft_command(store), 'default_id':'missing'})
    assert library(store) == before


@pytest.mark.parametrize('language,overview,label', [('en','We agreed to test the workflow.','Proposals and dependencies'),
                                                   ('vi','Nhóm thống nhất kiểm tra quy trình.','Đề xuất và phụ thuộc')])
def test_saved_snapshot_drives_notes_and_exports_after_deletion(store, language, overview, label):
    templates = NoteTemplateStore(store.conn)
    sid = meeting(store, language)
    definition = BUILTINS['planning'].model_dump()
    # Verify the saved order, including an overview placed after key points.
    definition['sections'][0], definition['sections'][1] = definition['sections'][1], definition['sections'][0]
    draft = templates.create(definition)
    templates.select(sid, draft['id'])
    generation = templates.begin_generation(sid, 1, provider='fixture', model='fixture', destination='http://localhost:1234/v1')
    rows = store.segments(sid)
    cited = {'text':overview, 'segment_ids':[rows[0]['id']]}
    summary = {'overview':cited, 'key_points':[cited], 'decisions':[], 'action_items':[], 'open_questions':[], 'uncertainties':[]}
    saved = store.save_grounded_summary(sid, 1, transcript_hash(rows), summary, provider='fixture', model='fixture', generation_id=generation['id'])
    assert saved['presentation'][0] == {'key':'key_points','label':label}
    templates.delete(draft['id'])
    assert store.grounded_summary(sid)['presentation'] == saved['presentation']
    markdown = export_markdown(store.get(sid), rows, store.revisions(sid), saved)
    assert label in markdown and markdown.index(label) < markdown.index(escape_md(overview))
    assert 'Notes template: Planning' in markdown and '(#' in markdown
    assert 'None stated.' not in markdown
    blocks = summary_blocks(saved, rows, 'Test', include_sources=True)
    assert blocks[1] == {'style':'heading','text':label}
    assert any(block['style'] == 'source' for block in blocks)


def test_desktop_template_choices_and_draft_receipts():
    settings = DesktopSettings(Vault())
    desktop = DesktopContract(settings, Path('/fake/helper'))
    app, token = create_app(desktop=desktop, summary_provider=settings.value.summary())
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        snapshot = receive(ws, 'snapshot')
        assert len(snapshot['note_templates']['templates']) == 6
        sid = client.portal.call(meeting, app.state.store)
        ws.send_json(envelope({'action':'select_note_template','session_id':sid,'template_id':'planning'}))
        assert receive(ws, 'note_templates')['default_id'] == 'general'
        assert receive(ws, 'receipt')['status'] == 'accepted'
        ws.send_json(envelope({'action':'meeting_page','session_id':sid}))
        assert receive(ws, 'meeting_page')['note_template']['id'] == 'planning'
        receive(ws, 'receipt')
        command = {**draft_command_from_snapshot(snapshot), 'default_id':'interview'}
        request = envelope(command)
        ws.send_json(request)
        assert receive(ws, 'note_templates')['default_id'] == 'interview'
        assert receive(ws, 'receipt')['status'] == 'accepted'
        ws.send_json(request)  # Receipt replay must not reapply a mutation.
        assert receive(ws, 'receipt')['status'] == 'accepted'
        ws.send_json(envelope(command))
        assert receive(ws, 'receipt')['status'] == 'rejected'
        assert receive(ws, 'error')
        assert settings.value.summary_instructions == ''


def draft_command_from_snapshot(snapshot):
    return {'action':'save_note_templates','templates':[], 'default_id':'general',
            'library_revision':snapshot['note_templates']['library_revision']}


def test_sql_failure_rolls_back_deletion_and_creation(store):
    import sqlite3
    original = custom()
    save_library(store, {**draft_command(store), 'templates':[original]})
    before = library(store)
    store.conn.execute("CREATE TRIGGER reject_template BEFORE INSERT ON note_templates BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='simulated'):
        save_library(store, {**draft_command(store), 'templates':[custom()]})
    assert library(store) == before


def test_large_unicode_draft_uses_bounded_template_protocol():
    settings = DesktopSettings(Vault())
    app, token = create_app(desktop=DesktopContract(settings, Path('/fake/helper')),
                            summary_provider=settings.value.summary())
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        snapshot = receive(ws, 'snapshot')
        draft = custom(); draft['definition']['focus'] = '😀' * 2000
        ws.send_json(envelope({**draft_command_from_snapshot(snapshot), 'templates':[draft]}))
        assert receive(ws, 'note_templates')['templates'][-1]['definition']['focus'] == draft['definition']['focus']
        assert receive(ws, 'receipt')['status'] == 'accepted'
