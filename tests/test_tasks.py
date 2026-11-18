"""F4 action-item tracking: reconciliation, identity reservation and task CRUD."""
import json
from uuid import uuid4

import pytest
from scribeloop_core.speakers.registry import SpeakerRegistry
from scribeloop_core.storage import tasks as task_module
from scribeloop_core.storage.db import SCHEMA_VERSION, connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from scribeloop_core.storage.tasks import TaskStore


@pytest.fixture()
def store(tmp_path):
    conn = migrate(connect(tmp_path / 'tasks.db'))
    yield SessionStore(conn)
    conn.close()


def saved_meeting(store, title='Standup', *, items=None, language='en'):
    sid = store.create_recording(title, {})['id']
    row = store.append(sid, {'text': 'We agreed on the plan.', 'language': language,
                             'start_ms': 0, 'end_ms': 1000, 'is_final': True})
    store.transition(sid, 'completed')
    if items is not None:
        save_summary(store, sid, [action_row(item, row) for item in items])
    return sid


def action_row(item, row):
    return {**item, 'segment_ids': [row['id']]}


def save_summary(store, sid, items):
    rows, fingerprint, _ = store.summary_input(sid, 1, build_messages=False)
    items = [action_row(item, rows[0]) for item in items]
    cited = {'text': 'Plan discussed.', 'segment_ids': [rows[0]['id']]}
    value = {'overview': cited, 'key_points': [], 'decisions': [],
             'action_items': items, 'open_questions': [], 'uncertainties': []}
    store.save_grounded_summary(sid, 1, fingerprint, value, provider='test', model='test')


def action(task='Ship the report', owner='Alice', due='2026-12-01', row=None):
    # segment_ids=None is a placeholder that saved_meeting binds to the new row.
    return {'task': task, 'owner': owner, 'due': due,
            'segment_ids': [row['id']] if row else None}


def suggestion(store, sid, ordinal=0):
    rows = store_tasks(store).suggestions(sid)
    return rows[ordinal]


def store_tasks(store):
    return TaskStore(store.conn)


def accept_all(store, sid, **overrides):
    suggestions = store_tasks(store).suggestions(sid)
    items = [{'suggestion_id': row['id'], **overrides} for row in suggestions
             if row['state'] == 'proposed']
    return store_tasks(store).accept_suggestions(sid, items)


# ---------- reconciliation ----------

def test_reconciliation_links_regenerated_suggestions_without_duplicates(store):
    sid = saved_meeting(store, items=[action()])
    first = accept_all(store, sid)
    assert len(first) == 1 and first[0]['origin'] == 'suggestion'
    save_summary(store, sid, [action()])  # regeneration, identical content
    rows = store_tasks(store).suggestions(sid)
    assert rows[0]['state'] == 'linked' and rows[0]['task_id'] == first[0]['id']
    assert store_tasks(store).counts(sid)['all'] == 1


def test_fingerprint_ignores_segment_ids_and_case(store):
    sid = saved_meeting(store, items=[action(task='Ship the  REPORT', owner='ALICE')])
    accept_all(store, sid)
    save_summary(store, sid, [action(task='Ship the report', owner='alice')])
    assert store_tasks(store).suggestions(sid)[0]['state'] == 'linked'


def test_dismissal_survives_regeneration_and_undo_reconciles(store):
    sid = saved_meeting(store, items=[action()])
    suggestion_id = suggestion(store, sid)['id']
    store_tasks(store).dismiss_suggestions(sid, [suggestion_id])
    save_summary(store, sid, [action()])
    assert store_tasks(store).suggestions(sid)[0]['state'] == 'dismissed'
    store_tasks(store).restore_suggestions(sid, [suggestion_id])
    assert store_tasks(store).suggestions(sid)[0]['state'] == 'proposed'
    save_summary(store, sid, [action()])
    assert store_tasks(store).suggestions(sid)[0]['state'] == 'proposed', \
        'undo must clear dismissal history across saved summaries'


def test_ambiguous_suggestion_requires_explicit_choice(store):
    sid = saved_meeting(store, items=[])
    store_tasks(store).create(sid, 'Send the weekly report to the team')
    save_summary(store, sid, [action(task='Send the weekly report to the team!', owner=None, due=None)])
    row = suggestion(store, sid)
    assert row['state'] == 'proposed' and row['similar_task_id']
    with pytest.raises(ValueError, match='similar'):
        store_tasks(store).accept_suggestions(sid, [{'suggestion_id': row['id']}])
    # Keep existing links the suggestion without editing the task.
    store_tasks(store).link_suggestion(sid, row['id'], row['similar_task_id'])
    assert store_tasks(store).suggestions(sid)[0]['state'] == 'linked'
    save_summary(store, sid, [action(task='Send the weekly report to the team!', owner=None, due=None)])
    assert store_tasks(store).suggestions(sid)[0]['state'] == 'linked', \
        'a prior Keep-existing mapping links later regenerations'
    # Add anyway requires the explicit confirmation and creates a separate task.
    other = saved_meeting(store, title='Review', items=[])
    store_tasks(store).create(other, 'Send the weekly report to the team')
    save_summary(store, other, [action(task='Send the weekly report to the team!', owner=None, due=None)])
    ambiguous = suggestion(store, other)
    created = store_tasks(store).accept_suggestions(
        other, [{'suggestion_id': ambiguous['id'], 'allow_similar': True}])
    assert created[0]['id'] != ambiguous['similar_task_id']


def test_stale_and_cross_meeting_suggestion_ids_are_rejected(store):
    sid = saved_meeting(store, items=[action()])
    stale_id = suggestion(store, sid)['id']
    save_summary(store, sid, [action()])  # regeneration replaces the active set
    with pytest.raises(ValueError, match='current saved notes'):
        store_tasks(store).accept_suggestions(sid, [{'suggestion_id': stale_id}])
    other = saved_meeting(store, title='Other', items=[action()])
    fresh = suggestion(store, sid)['id']
    with pytest.raises(ValueError, match='current saved notes'):
        store_tasks(store).dismiss_suggestions(other, [fresh])


# ---------- identity reservation, batches and limits ----------

def test_delete_reaccept_restore_preserves_one_identity_and_edits(store):
    sid = saved_meeting(store, items=[action()])
    task = accept_all(store, sid)[0]
    store_tasks(store).update(task['id'], owner_label='Alice B', due_date='2026-12-02')
    store_tasks(store).update(task['id'], status='done')
    store_tasks(store).delete(task['id'])
    save_summary(store, sid, [action()])
    row = suggestion(store, sid)
    assert row['state'] == 'proposed', 'deleted fingerprints stay proposed until reaccept'
    restored = accept_all(store, sid)[0]
    assert restored['id'] == task['id'], 'reacceptance must restore the reserved identity'
    assert restored['owner_label'] == 'Alice B' and restored['due_date'] == '2026-12-02'
    assert restored['status'] == 'done', 'restore preserves the prior status and edits'
    assert restored['completed_at'] is not None
    store_tasks(store).restore(task['id'])  # Undo after reacceptance stays conflict-free
    assert store_tasks(store).counts(sid)['open'] == 0
    assert store_tasks(store).counts(sid)['done'] == 1


def test_duplicate_fingerprints_in_one_batch_resolve_to_one_identity(store):
    sid = saved_meeting(store, items=[action(), action()])
    rows = store_tasks(store).suggestions(sid)
    assert {row['fingerprint'] for row in rows} == {rows[0]['fingerprint']}
    created = store_tasks(store).accept_suggestions(sid, [
        {'suggestion_id': rows[0]['id']},
        {'suggestion_id': rows[1]['id'], 'title': 'Ship the report now'}])
    assert created[0]['id'] == created[1]['id'], 'batch duplicates share one task identity'
    assert created[1]['title'] == 'Ship the report', \
        'the losing duplicate cannot overwrite the first task'


def test_batch_validation_is_atomic_before_any_write(store):
    sid = saved_meeting(store, items=[action(), action(owner=None, due=None)])
    rows = store_tasks(store).suggestions(sid)
    before = store_tasks(store).counts(sid)
    with pytest.raises(ValueError):
        store_tasks(store).accept_suggestions(sid, [
            {'suggestion_id': rows[0]['id']},
            {'suggestion_id': rows[1]['id'], 'due_date': 'not-a-date'}])
    assert store_tasks(store).counts(sid) == before, 'one invalid item rolls back the batch'
    assert suggestion(store, sid)['state'] == 'proposed'


def test_explicit_null_clears_fields_and_omitted_fields_prefill(store):
    sid = saved_meeting(store, items=[action(owner=None, due='2026-13-40')])
    row = suggestion(store, sid)
    created = store_tasks(store).accept_suggestions(sid, [{'suggestion_id': row['id']}])
    # An invalid extracted date stays visible as source text but unset on the task.
    assert created[0]['due_date'] is None
    assert row['due'] == '2026-13-40'
    save_summary(store, sid, [action(owner=None, due='2026-13-40')])
    store_tasks(store).delete(created[0]['id'])
    # Omitted fields preserve prior edits on the restored task.
    preserved = accept_all(store, sid, owner={'label': 'Bob'}, due_date='2026-11-30')[0]
    assert preserved['owner_label'] == 'Bob' and preserved['due_date'] == '2026-11-30'
    store_tasks(store).delete(preserved['id'])
    save_summary(store, sid, [action(owner=None, due='2026-13-40')])
    # Explicit null clears owner and due instead of preserving them.
    cleared = accept_all(store, sid, owner=None, due_date=None)[0]
    assert cleared['owner_label'] is None and cleared['due_date'] is None


def test_task_limit_is_atomic_and_restoring_hits_it(store):
    sid = saved_meeting(store, items=[])
    tasks = store_tasks(store)
    for index in range(task_module.MAX_TASKS):
        tasks.create(sid, f'Task {index}')
    with pytest.raises(ValueError, match='at most 500'):
        tasks.create(sid, 'One task too many')
    victim = tasks.page(session_id=sid)['tasks'][0]['id']
    tasks.delete(victim)
    recreated = tasks.create(sid, 'Fits after the delete')
    assert tasks.counts(sid)['all'] == task_module.MAX_TASKS
    with pytest.raises(ValueError, match='at most 500'):
        tasks.restore(victim)  # restoring at the limit fails atomically
    tasks.delete(recreated['id'])
    tasks.restore(victim)  # below the limit the same restore succeeds
    assert tasks.counts(sid)['deleted'] == 1


# ---------- pagination, filters and overdue ----------

def test_pagination_reaches_all_tasks_with_tied_sort_keys(store):
    sid = saved_meeting(store, items=[])
    tasks = store_tasks(store)
    for index in range(task_module.MAX_TASKS):
        tasks.create(sid, f'Task {index:03}', due_date='2026-12-01')
    page = tasks.page(session_id=sid)
    assert len(page['tasks']) == 200 and page['next_offset'] == 200
    assert page['counts']['all'] == 500 and page['counts']['open'] == 500
    seen = [task['id'] for task in page['tasks']]
    while page['next_offset'] is not None:
        page = tasks.page(session_id=sid, offset=page['next_offset'])
        assert not {task['id'] for task in page['tasks']} & set(seen)
        seen += [task['id'] for task in page['tasks']]
    assert len(seen) == 500
    for index, offset in ((0, 0), (200, 200), (400, 400), (499, 400)):
        assert tasks.page(session_id=sid, offset=offset)['tasks'][index - offset if index >= offset else index]


def test_filters_compose_and_deleted_semantics(store):
    sid = saved_meeting(store, items=[])
    tasks = store_tasks(store)
    manual = tasks.create(sid, 'Manual task')
    tasks.update(manual['id'], status='done')
    tasks.create(sid, 'Deleted task')
    extra = tasks.create(sid, 'Dismissed task')
    tasks.update(extra['id'], status='dismissed')
    assert tasks.page(session_id=sid)['counts'] == {'open': 1, 'done': 1, 'dismissed': 1,
                                                    'deleted': 0, 'all': 3}
    assert tasks.page(session_id=sid, status='done')['tasks'][0]['title'] == 'Manual task'
    all_rows = tasks.page(session_id=sid, status='all')['tasks']
    assert {task['title'] for task in all_rows} == {'Manual task', 'Deleted task', 'Dismissed task'}
    with pytest.raises(ValueError):
        tasks.page(status='archived')
    with pytest.raises(ValueError):
        tasks.page(due='someday')
    with pytest.raises(ValueError):
        tasks.page(offset=-1)


def test_overdue_uses_local_calendar_day_and_open_status(store, monkeypatch):
    import datetime as dt
    fixed = dt.date(2026, 12, 2)
    monkeypatch.setattr(task_module, 'local_today', lambda: fixed)
    sid = saved_meeting(store, items=[])
    tasks = store_tasks(store)
    tasks.create(sid, 'Past task', due_date='2026-12-01')
    tasks.create(sid, 'Today task', due_date='2026-12-02')
    done = tasks.create(sid, 'Done task', due_date='2026-11-01')
    tasks.update(done['id'], status='done')
    rows = {task['title']: task for task in tasks.page(session_id=sid, status='all')['tasks']}
    assert rows['Past task']['overdue'] and not rows['Today task']['overdue']
    assert not rows['Done task']['overdue'], 'done tasks are never overdue'
    overdue_page = tasks.page(session_id=sid, due='overdue')
    assert [task['title'] for task in overdue_page['tasks']] == ['Past task']
    assert overdue_page['counts']['open'] == 1, 'counts respect the due filter'
    assert tasks.page(session_id=sid)['counts']['open'] == 2
    soon = tasks.page(session_id=sid, due='next7')
    assert {task['title'] for task in soon['tasks']} == {'Today task'}
    undated = tasks.page(session_id=sid, due='undated')
    assert undated['tasks'] == [] and undated['counts']['open'] == 0, \
        'counts respect the due filter: no undated tasks exist'


# ---------- carryover ----------

def test_carryover_is_explicit_reserved_and_survives_source_deletion(store):
    earlier = saved_meeting(store, title='Weekly standup', items=[])
    tasks = store_tasks(store)
    source = tasks.create(earlier, 'Prepare the demo environment', due_date='2026-12-05')
    store.conn.execute('UPDATE sessions SET started_at=1000 WHERE id=?', (earlier,))
    later = saved_meeting(store, title='Weekly  Standup', items=[])  # same after normalize
    assert tasks.carryover_candidates(later)[0]['id'] == source['id']
    first = tasks.accept_carryover(later, source['id'])
    assert first['origin'] == 'carryover' and first['status'] == 'open'
    assert first['carryover_note'].startswith('Carried over from Weekly standup')
    assert first['source_session_id'] == earlier
    again = tasks.accept_carryover(later, source['id'])
    assert again['id'] == first['id'], 'repeated acceptance returns one destination'
    # The copied task is independent: deleting the source meeting keeps it.
    tasks.delete(first['id'])
    restored = tasks.accept_carryover(later, source['id'])
    assert restored['id'] == first['id'] and restored['status'] == 'open'
    other = saved_meeting(store, title='Unrelated', items=[])
    with pytest.raises(ValueError, match='same name'):
        tasks.accept_carryover(other, source['id'])
    with pytest.raises(ValueError, match='another meeting'):
        tasks.accept_carryover(earlier, source['id'])


# ---------- upgrade, forgetting and deletion ----------

def test_upgrade_from_schema_25_backfills_and_preserves_dispositions(store):
    sid = saved_meeting(store, items=[action()])
    stale = saved_meeting(store, title='Stale meeting', items=[action()])
    accept_all(store, stale)  # one accepted disposition before the simulated upgrade
    store.append(stale, {'text': 'A later utterance makes the notes stale.', 'language': 'en',
                         'start_ms': 2000, 'end_ms': 3000, 'is_final': True})
    assert store.grounded_summary(stale)['stale'] is True
    broken = saved_meeting(store, title='Broken legacy', items=[])
    store.conn.execute('INSERT INTO grounded_summaries (id,session_id,revision,input_hash,'
                       'provider,model,summary_json,created_at) VALUES (?,?,1,?,?,?,?,?)',
                       (uuid4().hex, broken, 'hash', 'p', 'm',
                        json.dumps({'overview': {'text': 'x', 'segment_ids': []},
                                    'action_items': [{'owner': 'no task text', 'segment_ids': []}]}),
                        store._now()))
    # Simulate reopening a populated schema-25 workspace: drop F4 tables, rewind.
    conn = store.conn
    conn.execute('DROP TABLE task_suggestions')
    conn.execute('DROP TABLE tasks')
    conn.execute('PRAGMA user_version=25')
    conn.commit()
    migrate(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    assert [row['state'] for row in TaskStore(conn).suggestions(sid)] == ['proposed'], \
        'fresh summaries appear as suggestions without generation'
    assert len(TaskStore(conn).suggestions(stale)) == 1, 'stale summaries are included'
    assert TaskStore(conn).suggestions(broken) == [], \
        'malformed legacy items produce a diagnostic instead of invented suggestions'
    assert store.grounded_summary(broken) is not None, 'legacy notes stay readable'
    # Dispositions set after the upgrade survive an idempotent reopen.
    tasks = TaskStore(conn)
    tasks.accept_suggestions(stale, [{'suggestion_id': tasks.suggestions(stale)[0]['id']}])
    tasks.dismiss_suggestions(sid, [TaskStore(conn).suggestions(sid)[0]['id']])
    migrate(conn)
    assert TaskStore(conn).suggestions(stale)[0]['state'] == 'accepted'
    assert TaskStore(conn).suggestions(sid)[0]['state'] == 'dismissed', \
        'reopening the workspace does not reset review dispositions'


def test_meeting_delete_cleans_tasks_child_first_with_foreign_keys(store):
    sid = saved_meeting(store, items=[action()])
    accept_all(store, sid)
    save_summary(store, sid, [action(task='Ship the report now')])
    similar = suggestion(store, sid)
    assert similar['state'] == 'proposed' and similar['similar_task_id']
    earlier = saved_meeting(store, title='Standup', items=[])
    tasks = store_tasks(store)
    source = tasks.create(earlier, 'Carried workload')
    store.conn.execute('UPDATE sessions SET started_at=1 WHERE id=?', (earlier,))
    copied = tasks.accept_carryover(sid, source['id'])
    store.finish_delete(earlier)  # delete the SOURCE meeting
    remaining_ids = {task['id'] for task in tasks.page()['tasks']}
    assert copied['id'] in remaining_ids, \
        'carryover copies survive their source meeting deletion'
    assert copied['carryover_note'] and copied['title'] == 'Carried workload'
    store.finish_delete(sid)  # deleting the destination removes its copy and its own tasks
    assert tasks.page()['tasks'] == []
    conn = store.conn
    assert conn.execute('SELECT COUNT(*) FROM task_suggestions WHERE session_id=?',
                        (sid,)).fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM tasks WHERE session_id=?', (sid,)).fetchone()[0] == 0


def test_person_forgetting_clears_task_owners_and_derived_notes(store):
    sid = saved_meeting(store, items=[action(owner='Renée')])
    tasks = store_tasks(store)
    task = accept_all(store, sid)[0]
    registry = SpeakerRegistry(store)
    person = registry.create('Renée')
    tasks.update(task['id'], owner_person_id=person)
    tasks.create(sid, 'Labelled owner task', owner_label='renée ')
    deleted = tasks.create(sid, 'Deleted owner task', owner_label='Renée')
    tasks.delete(deleted['id'])
    # A carryover copy in a later same-title meeting also carries the owner link.
    series = saved_meeting(store, title='Standup', items=[])
    store.conn.execute('UPDATE sessions SET started_at=1 WHERE id=?', (sid,))
    tasks.accept_carryover(series, task['id'])
    other = saved_meeting(store, title='Other', items=[action(task='Other task', owner='RENÉE')])
    affected = registry.forget(person)
    assert {sid, series, other} <= set(affected)
    live = {row['title']: row for row in tasks.page(session_id=sid, status='all')['tasks']}
    assert live['Ship the report']['owner_person_id'] is None
    assert live['Ship the report']['owner_removed'] is True
    assert live['Ship the report']['title'] == 'Ship the report', 'task text survives'
    assert live['Labelled owner task']['owner_label'] is None
    assert live['Labelled owner task']['owner_removed'] is True
    deleted_rows = tasks.page(session_id=sid, status='deleted')['tasks']
    assert len(deleted_rows) == 1
    assert deleted_rows[0]['owner_label'] is None and deleted_rows[0]['owner_removed'] is True, \
        'forgetting reaches deleted tasks'
    assert tasks.page(session_id=series)['tasks'][0]['owner_person_id'] is None, \
        'carryover copies lose the owner link too'
    assert store.grounded_summary(sid) is None, \
        'derived summaries are removed for the expanded session set'
    assert tasks.suggestions(sid) == [], 'suggestions cascade with their summaries'
    assert store.grounded_summary(other) is None and tasks.suggestions(other) == [], \
        'suggestion-only owner mentions also lose derived notes'
    labelled_row = live['Labelled owner task']
    tasks.update(labelled_row['id'], owner_label='Someone else')
    assert tasks._task(labelled_row['id'])['owner_removed'] == 0, \
        'explicitly setting an owner later resets the removal marker'

