"""F4 desktop protocol: bounded task reads, mutations, receipts and invalidation."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.tasks import TaskStore
from test_desktop import Vault, envelope, receive
from test_tasks import action, saved_meeting


@pytest.fixture()
def workspace(tmp_path):
    settings = DesktopSettings(Vault())
    desktop = DesktopContract(settings, Path('/fake/helper'))
    app, token = create_app(desktop=desktop, summary_provider=settings.value.summary())
    client = TestClient(app)
    with client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        global _CLIENT
        _CLIENT = client
        ws.send_text(token)
        receive(ws, 'snapshot')
        yield app, client, ws


_CLIENT = None


def run(app, fn, *args, **kwargs):
    """Store access lives on the app thread; route every direct call through it."""
    import functools
    return _CLIENT.portal.call(functools.partial(fn, *args, **kwargs))


def make_meeting(app, title='Standup', items=None):
    return run(app, saved_meeting, app.state.store, title, items=items)


def tasks_of(app):
    return run(app, TaskStore, app.state.store.conn)


def call_tasks(app, name, *args, **kwargs):
    return run(app, getattr(tasks_of(app), name), *args, **kwargs)


def command(ws, payload):
    """Send a mutation; return the invalidate events seen before its receipt."""
    ws.send_json(envelope(payload))
    events = []
    for _ in range(100):
        value = ws.receive_json()
        if value['type'] == 'error':
            raise AssertionError(f'unexpected error: {value}')
        if value['type'] == 'receipt':
            if value['status'] != 'accepted':
                raise AssertionError(f'command rejected: {ws.receive_json()}')
            return events
        events.append(value)
    raise AssertionError('Receipt missing')


def rejected(ws, payload):
    ws.send_json(envelope(payload))
    errors = []
    for _ in range(100):
        value = ws.receive_json()
        if value['type'] == 'error':
            errors.append(value['message'])
            continue
        if value['type'] == 'receipt':
            assert value['status'] == 'rejected', (value, errors)
            return errors
        assert value['type'] != 'invalidate', 'rejected commands must not invalidate'
    raise AssertionError(f'Rejected receipt missing: {errors}')


def test_tasks_page_read_filters_and_carryover_read(workspace):
    app, _client, ws = workspace
    earlier = make_meeting(app, items=[])
    source = call_tasks(app, 'create', earlier, 'Earlier task', due_date='2026-12-20')
    run(app, lambda: app.state.store.conn.execute(
        'UPDATE sessions SET started_at=1 WHERE id=?', (earlier,)))
    later = make_meeting(app, items=[])
    page = expect(ws, 'tasks_page', payload={'action': 'tasks_page'})
    assert page['status'] == 'open'
    assert [task['title'] for task in page['tasks']] == ['Earlier task']
    assert page['session_id'] is None and page['counts']['open'] == 1
    scoped = expect(ws, 'tasks_page',
                    payload={'action': 'tasks_page', 'session_id': later, 'due': 'next7'})
    assert scoped['tasks'] == [] and scoped['session_id'] == later
    carryover = expect(ws, 'task_carryover',
                       payload={'action': 'task_carryover', 'session_id': later})
    assert [task['id'] for task in carryover['candidates']] == [source['id']]
    command(ws, {'action': 'accept_task_carryover', 'session_id': later,
                 'source_task_id': source['id']})
    again = expect(ws, 'tasks_page', payload={'action': 'tasks_page', 'session_id': later})
    assert again['tasks'][0]['origin'] == 'carryover'
    rejected(ws, {'action': 'task_carryover', 'session_id': 'nope'})


def test_large_unicode_batch_uses_the_bounded_task_allowance(workspace):
    app, _client, ws = workspace
    sid = make_meeting(app, items=[action(task='😀' * 2000, owner=None, due=None),
                                   action(task='😀' * 2000, owner=None, due=None)])
    suggestions = call_tasks(app, 'suggestions', sid)
    items = [{'suggestion_id': row['id']} for row in suggestions]
    assert len(items) == 2
    command(ws, {'action': 'accept_task_suggestions', 'session_id': sid, 'items': items})
    created = call_tasks(app, 'page', session_id=sid)['tasks']
    assert {task['title'] for task in created} == {'😀' * 2000}
    assert call_tasks(app, 'counts', sid)['all'] == 1, \
        'duplicate fingerprints inside one transport batch share one identity'


def test_suggestion_accept_and_dismiss_via_desktop(workspace):
    app, _client, ws = workspace
    sid = make_meeting(app, items=[action()])
    suggestion_id = expect(ws, 'meeting_page',
                           payload={'action': 'meeting_page', 'session_id': sid})['suggestions'][0]['id']
    command(ws, {'action': 'accept_task_suggestions', 'session_id': sid,
                 'items': [{'suggestion_id': suggestion_id, 'title': 'Ship it'}]})
    task = call_tasks(app, 'page', session_id=sid)['tasks'][0]
    assert task['title'] == 'Ship it' and task['origin'] == 'suggestion'

    def regenerate(store, task_text):
        from test_tasks import save_summary
        rows, _fingerprint, _ = store.summary_input(sid, 1, build_messages=False)
        save_summary(store, sid, [{'task': task_text, 'owner': 'Alice',
                                   'due': '2026-12-01', 'segment_ids': [rows[0]['id']]}])
    run(app, regenerate, app.state.store, 'Ship the report')
    assert call_tasks(app, 'suggestions', sid)[0]['state'] == 'linked', \
        'regeneration links without duplication'
    run(app, regenerate, app.state.store, 'Draft the follow-up email')
    fresh = call_tasks(app, 'suggestions', sid)[0]
    assert fresh['state'] == 'proposed'
    command(ws, {'action': 'dismiss_task_suggestions', 'session_id': sid,
                 'suggestion_ids': [fresh['id']]})
    assert call_tasks(app, 'suggestions', sid)[0]['state'] == 'dismissed'
    command(ws, {'action': 'restore_task_suggestions', 'session_id': sid,
                 'suggestion_ids': [fresh['id']]})
    assert call_tasks(app, 'suggestions', sid)[0]['state'] == 'proposed'
    rejected(ws, {'action': 'accept_task_suggestions', 'session_id': sid, 'items': []})
    rejected(ws, {'action': 'accept_task_suggestions', 'session_id': sid,
                 'items': [{'suggestion_id': fresh['id'], 'unknown': 1}]})



def expect(ws, kind, payload):
    ws.send_json(envelope(payload))
    value = None
    for _ in range(100):
        value = ws.receive_json()
        if value['type'] == 'error':
            raise AssertionError(f'{kind} failed: {value}')
        if value['type'] == kind:
            break
    if value is None:
        raise AssertionError(f'{kind} missing')
    # Reads are followed by their receipt; drain it so later steps stay in sync.
    for _ in range(100):
        if ws.receive_json()['type'] == 'receipt':
            break
    return value


def test_meeting_page_includes_tasks_suggestions_and_counts(workspace):
    app, _client, ws = workspace
    sid = make_meeting(app, items=[action()])
    call_tasks(app, 'accept_suggestions', sid,
               [{'suggestion_id': call_tasks(app, 'suggestions', sid)[0]['id']}])
    saved = run(app, app.state.store.grounded_summary, sid)
    page = expect(ws, 'meeting_page', payload={'action': 'meeting_page', 'session_id': sid})
    assert [task['title'] for task in page['tasks']] == ['Ship the report']
    assert page['task_counts'] == {'open': 1, 'done': 0, 'dismissed': 0, 'all': 1, 'deleted': 0}
    assert page['tasks'][0]['owner_label'] == 'Alice'
    assert [row['state'] for row in page['suggestions']] == ['accepted'], \
        'accepted suggestions stay visible as linked to their task'
    assert page['tasks_next_offset'] is None
    assert page['summary']['id'] == saved['id']


def test_task_mutations_publish_invalidation_and_persist(workspace):
    app, _client, ws = workspace
    sid = make_meeting(app, items=[])
    events = command(ws, {'action': 'create_task', 'session_id': sid, 'title': 'Manual task'})
    assert any(event['type'] == 'invalidate' and event['session_id'] == sid for event in events)
    created = call_tasks(app, 'page', session_id=sid)['tasks'][0]
    command(ws, {'action': 'update_task', 'task_id': created['id'], 'status': 'done',
                 'owner_label': 'Bob', 'due_date': '2026-12-24'})
    row = call_tasks(app, '_task', created['id'])
    assert row['status'] == 'done' and row['owner_label'] == 'Bob'
    assert row['completed_at'] is not None
    command(ws, {'action': 'delete_task', 'task_id': created['id']})
    assert call_tasks(app, '_task', created['id'])['status'] == 'deleted'
    command(ws, {'action': 'restore_task', 'task_id': created['id']})
    assert call_tasks(app, '_task', created['id'])['status'] == 'done', \
        'restore returns the prior status'
    rejected(ws, {'action': 'create_task', 'session_id': sid, 'title': '', 'extra': 1})
    rejected(ws, {'action': 'delete_task', 'task_id': 'not-an-id'})
    rejected(ws, {'action': 'update_task', 'task_id': created['id'], 'status': 'nope'})

