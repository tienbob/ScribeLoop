"""Durable action-item tasks (F4): suggestions, reconciliation and CRUD.

Tasks are meeting-scoped, user-approved work items derived from cited summary
action items. Summary regeneration only ever writes ``task_suggestions``; it
never rewrites an accepted task. Deleted task identities stay reserved by their
fingerprint (partial unique index) so reaccepting a suggestion restores the same
row instead of creating a replacement that could block Undo. Carryover reserves
one destination identity per source task for the same reason.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

log = logging.getLogger(__name__)

ID = re.compile(r'^[0-9a-f]{32}$')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MAX_TASKS = 500
MAX_TITLE = 2000
MAX_OWNER = 100
MAX_PAGE = 200
MAX_CARRYOVER = 25
MAX_BATCH = 20
SIMILARITY_THRESHOLD = 0.7
STATUSES = ('open', 'done', 'dismissed', 'deleted')
RESTORE_STATUSES = ('open', 'done', 'dismissed')


def local_today():
    """Today in the machine's local timezone; patched by timezone tests."""
    return datetime.now().astimezone().date()


def normalize_text(value):
    """NFC, whitespace-collapsed, casefolded text; blank becomes None."""
    if not isinstance(value, str):
        return None
    return ' '.join(unicodedata.normalize('NFC', value).split()).casefold() or None


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def suggestion_fingerprint(task, owner, due):
    """Structural suggestion identity within one meeting (never segment IDs)."""
    return hashlib.sha256(canonical_json({
        't': normalize_text(task), 'o': normalize_text(owner), 'd': normalize_text(due),
    }).encode('utf-8')).hexdigest()


def valid_due(value):
    """Accept only a real ISO calendar date; anything else stays unset."""
    if not isinstance(value, str) or not DATE.fullmatch(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _tokens(text):
    return frozenset(re.findall(r'\w+', text, re.UNICODE)) if text else frozenset()


def similarity(candidate, existing):
    """Token-overlap score in [0,1]; 0 when either side has no tokens."""
    left, right = _tokens(candidate), _tokens(existing)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def similar_task_text(candidate, existing):
    """Ambiguity is surfaced for review, never merged silently."""
    return similarity(candidate, existing) >= SIMILARITY_THRESHOLD


def overdue(task, today=None):
    """Overdue badges apply to open tasks with a past local calendar date."""
    if task.get('status') != 'open' or not task.get('due_date'):
        return False
    today = today or local_today()
    return task['due_date'] < today.isoformat()


def _due_clause(due, params):
    """SQL filter for the global due popup; composed with the status filter."""
    if due == 'overdue':
        # Done/dismissed/deleted tasks are never overdue.
        params.append(local_today().isoformat())
        return "(t.status='open' AND t.due_date IS NOT NULL AND t.due_date < ?)"
    if due == 'next7':
        today = local_today()
        params.extend([today.isoformat(), (today + timedelta(days=7)).isoformat()])
        return '(t.due_date IS NOT NULL AND t.due_date >= ? AND t.due_date <= ?)'
    if due == 'undated':
        return 't.due_date IS NULL'
    raise ValueError('Invalid task due filter')


ORDER = '(due_date IS NULL), due_date, created_at DESC, id ASC'
TASK_COLUMNS = ('id', 'session_id', 'origin', 'title', 'owner_person_id', 'owner_label',
                'owner_removed', 'due_date', 'status', 'restore_status', 'source_session_id',
                'source_summary_id', 'source_revision', 'source_segment_ids',
                'suggestion_fingerprint', 'carryover_task_id', 'carryover_note',
                'created_at', 'updated_at', 'completed_at')


def task_row(row, *, today=None):
    task = {key: row[key] for key in TASK_COLUMNS}
    for key in ('meeting_title', 'meeting_started_at'):
        # Joined meeting metadata is optional per query.
        try:
            task[key] = row[key]
        except IndexError:
            task[key] = None
    raw = task.get('source_segment_ids')
    task['source_segment_ids'] = json.loads(raw) if raw else []
    task['owner_removed'] = bool(task.get('owner_removed'))
    task['overdue'] = overdue(task, today)
    return task


def suggestion_row(row):
    value = dict(row)
    value['segment_ids'] = json.loads(value.pop('segment_ids'))
    return value


# ---------- suggestion reconciliation (shared by saves and migration) ----------

def reconcile_state(conn, session_id, task_text, fingerprint):
    """Deterministic disposition for one suggestion fingerprint in one meeting.

    Exact fingerprints win over dismissal history; deleted-task fingerprints stay
    proposed until explicit acceptance restores the reserved row; ambiguous text
    similarity is surfaced for review instead of being merged silently.
    """
    row = conn.execute('SELECT id FROM tasks WHERE session_id=? AND suggestion_fingerprint=? '
                       "AND status!='deleted'", (session_id, fingerprint)).fetchone()
    if row:
        return 'linked', row['id'], None
    row = conn.execute('SELECT task_id FROM task_suggestions WHERE session_id=? AND fingerprint=? '
                       "AND state IN ('linked','accepted') AND task_id IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                       (session_id, fingerprint)).fetchone()
    if row and conn.execute("SELECT 1 FROM tasks WHERE id=? AND status!='deleted'",
                            (row['task_id'],)).fetchone():
        # A prior accept or explicit Keep-existing mapping links this fingerprint
        # on later regenerations while its target task is non-deleted.
        return 'linked', row['task_id'], None
    if conn.execute("SELECT 1 FROM task_suggestions WHERE session_id=? AND fingerprint=? "
                    "AND state='dismissed'", (session_id, fingerprint)).fetchone():
        return 'dismissed', None, None
    for candidate in conn.execute("SELECT id,title FROM tasks WHERE session_id=? AND status='open'",
                                  (session_id,)).fetchall():
        if similar_task_text(task_text, candidate['title']):
            return 'proposed', None, candidate['id']
    return 'proposed', None, None


def seed_summary_suggestions(conn, session_id, summary_id, summary):
    """Insert one suggestion row per cited action item; idempotent per summary.

    Malformed legacy items are skipped with a diagnostic, but storage errors
    propagate so the caller's summary-save or migration transaction rolls back
    instead of committing a silently incomplete reconciliation. Bounds match the
    grounded-summary schema (20 items).
    """
    items = summary.get('action_items') if isinstance(summary, dict) else None
    if not isinstance(items, list):
        return
    prepared = []
    for ordinal, item in enumerate(items[:20]):
        if not isinstance(item, dict):
            log.warning('Skipping malformed action item %s/%s in summary %s',
                        session_id, ordinal, summary_id)
            continue
        task_text = item.get('task')
        segment_ids = item.get('segment_ids')
        owner, due = item.get('owner'), item.get('due')
        if (not isinstance(task_text, str) or not task_text.strip()
                or not isinstance(segment_ids, list)
                or not all(isinstance(value, str) for value in segment_ids)
                or not (owner is None or isinstance(owner, str))
                or not (due is None or isinstance(due, str))):
            log.warning('Skipping malformed action item %s/%s in summary %s',
                        session_id, ordinal, summary_id)
            continue
        prepared.append((ordinal, task_text, owner, due, segment_ids))
    for ordinal, task_text, owner, due, segment_ids in prepared:
        fingerprint = suggestion_fingerprint(task_text, owner, due)
        state, task_id, similar_id = reconcile_state(conn, session_id, task_text, fingerprint)
        conn.execute('INSERT OR IGNORE INTO task_suggestions '
                     '(id,session_id,summary_id,ordinal,task,owner,due,segment_ids,'
                     'fingerprint,similar_task_id,state,task_id,created_at) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (uuid4().hex, session_id, summary_id, ordinal, task_text, owner, due,
                      json.dumps(segment_ids), fingerprint, similar_id, state, task_id,
                      int(time.time() * 1000)))


def backfill_suggestions(conn):
    """Schema-26 upgrade: seed suggestions from each meeting's latest summary."""
    latest = {}
    for row in conn.execute('SELECT session_id,id,summary_json FROM grounded_summaries '
                            'ORDER BY session_id,created_at DESC,rowid DESC'):
        latest.setdefault(row['session_id'], row)
    for session_id, row in latest.items():
        try:
            summary = json.loads(row['summary_json'])
        except (TypeError, ValueError):
            log.warning('Undecodable legacy summary for %s stays readable; no suggestions seeded',
                        session_id)
            continue
        seed_summary_suggestions(conn, session_id, row['id'], summary)


class TaskStore:
    """Owns the tasks tables. Mutations are local, explicit and immediately durable."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- shared validation ----------

    def _session(self, session_id):
        """Strict check for mutations: tasks require an idle saved conversation."""
        row = self._known_session(session_id)
        if row['status'] not in {'completed', 'failed', 'interrupted'}:
            raise ValueError('Select an idle saved conversation')
        return row

    def _known_session(self, session_id):
        """Softer check for reads: meeting_page serves live sessions too."""
        if not isinstance(session_id, str) or not ID.fullmatch(session_id):
            raise ValueError('Invalid meeting')
        row = self.conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if not row:
            raise ValueError('Meeting no longer exists')
        return row

    def _task(self, task_id):
        if not isinstance(task_id, str) or not ID.fullmatch(task_id):
            raise ValueError('Invalid task')
        row = self.conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Task no longer exists')
        return row

    def _clean_title(self, title):
        if not isinstance(title, str):
            raise TypeError('Invalid task title')
        title = ' '.join(title.split())
        if not 1 <= len(title) <= MAX_TITLE or any(ord(c) < 32 or ord(c) == 127 for c in title):
            raise ValueError('Use a task title from 1 to 2000 characters')
        return title

    def _clean_owner(self, owner):
        if not isinstance(owner, str):
            raise TypeError('Invalid task owner')
        owner = ' '.join(owner.split())
        if not owner:
            return None
        if len(owner) > MAX_OWNER or any(ord(c) < 32 or ord(c) == 127 for c in owner):
            raise ValueError('Use an owner name of at most 100 characters')
        return owner

    def _check_owner_person(self, person_id):
        if person_id is None:
            return None
        if not isinstance(person_id, str) or not ID.fullmatch(person_id) or not \
                self.conn.execute('SELECT 1 FROM speakers WHERE id=?', (person_id,)).fetchone():
            raise ValueError('Unknown person')
        return person_id

    def _check_limit(self, session_id, extra=0):
        count = self.conn.execute("SELECT COUNT(*) FROM tasks WHERE session_id=? AND status!='deleted'",
                                  (session_id,)).fetchone()[0]
        if count + extra > MAX_TASKS:
            raise ValueError(f'Meetings hold at most {MAX_TASKS} tasks. Delete or dismiss one first.')

    def _current_similar(self, session_id, task_text):
        """The most-similar open task right now, not the stale saved candidate.

        Task creation and edits after a summary was saved change what counts as
        ambiguous, so acceptance, linking and review reads revalidate against
        current state instead of trusting the stored similar_task_id.
        """
        best_id, best_score = None, 0.0
        for candidate in self.conn.execute(
                "SELECT id,title FROM tasks WHERE session_id=? AND status='open'",
                (session_id,)).fetchall():
            score = similarity(task_text, candidate['title'])
            if score >= SIMILARITY_THRESHOLD and score > best_score:
                best_id, best_score = candidate['id'], score
        return best_id

    def _refresh_similar(self, suggestion_id, current):
        stored = self.conn.execute('SELECT similar_task_id FROM task_suggestions WHERE id=?',
                                   (suggestion_id,)).fetchone()
        if stored and stored['similar_task_id'] != current:
            self.conn.execute('UPDATE task_suggestions SET similar_task_id=? WHERE id=?',
                              (current, suggestion_id))

    @staticmethod
    def _now():
        return int(time.time() * 1000)

    # ---------- reads ----------

    def page(self, *, session_id=None, status='open', owner_person_id=None,
             due=None, offset=0):
        """Global or meeting-scoped paged read with filter echo and counts."""
        if status not in (*STATUSES, 'all'):
            raise ValueError('Invalid task status filter')
        if due not in (None, 'overdue', 'next7', 'undated'):
            raise ValueError('Invalid task due filter')
        self._check_owner_person(owner_person_id)
        if type(offset) is not int or not 0 <= offset <= 10_000_000:
            raise ValueError('Invalid page offset')
        if session_id is not None:
            self._known_session(session_id)
        scope, params = [], []
        if session_id is not None:
            scope.append('t.session_id=?')
            params.append(session_id)
        if owner_person_id is not None:
            scope.append('t.owner_person_id=?')
            params.append(owner_person_id)
        if due is not None:
            scope.append(_due_clause(due, params))
        if status == 'all':
            status_clause, status_params = "t.status!='deleted'", []
        else:
            status_clause, status_params = 't.status=?', [status]
        where = ' WHERE ' + ' AND '.join([*scope, status_clause]) if (scope or status_clause) else ''
        count_where = (' WHERE ' + ' AND '.join(scope)) if scope else ''
        # One joined read and one aggregate keep meeting_page bounded: no
        # per-row meeting lookups and no per-status count queries.
        rows = self.conn.execute(
            f'SELECT t.*, s.title AS meeting_title, s.started_at AS meeting_started_at '
            f'FROM tasks t JOIN sessions s ON s.id=t.session_id{where} '
            f'ORDER BY {ORDER} LIMIT ? OFFSET ?',
            (*params, *status_params, MAX_PAGE + 1, offset)).fetchall()
        tasks = [task_row(row) for row in rows[:MAX_PAGE]]
        counts = self.conn.execute(
            f"SELECT SUM(t.status='open') AS open,SUM(t.status='done') AS done,"
            f"SUM(t.status='dismissed') AS dismissed,SUM(t.status='deleted') AS deleted,"
            f"SUM(t.status!='deleted') AS all_tasks FROM tasks t{count_where}",
            (*params,)).fetchone()
        counts = {'open': counts['open'] or 0, 'done': counts['done'] or 0,
                  'dismissed': counts['dismissed'] or 0, 'deleted': counts['deleted'] or 0,
                  'all': counts['all_tasks'] or 0}
        return {'type': 'tasks_page', 'tasks': tasks,
                'next_offset': offset + MAX_PAGE if len(rows) > MAX_PAGE else None,
                'offset': offset, 'status': status, 'due': due,
                'owner_person_id': owner_person_id, 'session_id': session_id,
                'counts': counts, 'today': local_today().isoformat()}

    def active_summary(self, session_id):
        return self.conn.execute('SELECT * FROM grounded_summaries WHERE session_id=? '
                                 'ORDER BY created_at DESC,rowid DESC LIMIT 1',
                                 (session_id,)).fetchone()

    def suggestions(self, session_id):
        """Active summary suggestions with reconciliation state, for meeting_page.

        Proposed rows recompute their similarity candidate against the meeting's
        current open tasks so the review UI never offers a stale match.
        """
        summary = self.active_summary(session_id)
        if not summary:
            return []
        rows = self.conn.execute(
            'SELECT s.*, t.title AS similar_task_title FROM task_suggestions s '
            'LEFT JOIN tasks t ON t.id=s.similar_task_id '
            'WHERE s.session_id=? AND s.summary_id=? ORDER BY s.ordinal',
            (session_id, summary['id'])).fetchall()
        values = [suggestion_row(row) for row in rows]
        for value in values:
            if value['state'] != 'proposed':
                continue
            current = self._current_similar(session_id, value['task'])
            if current == value['similar_task_id']:
                continue
            value['similar_task_id'] = current
            title = self.conn.execute('SELECT title FROM tasks WHERE id=?',
                                      (current,)).fetchone() if current else None
            value['similar_task_title'] = title['title'] if title else None
        return values

    def counts(self, session_id):
        row = self.conn.execute(
            "SELECT SUM(status='open') AS open,SUM(status='done') AS done,"
            "SUM(status='dismissed') AS dismissed,SUM(status!='deleted') AS all_tasks,"
            "SUM(status='deleted') AS deleted FROM tasks WHERE session_id=?",
            (session_id,)).fetchone()
        return {'open': row['open'] or 0, 'done': row['done'] or 0,
                'dismissed': row['dismissed'] or 0, 'all': row['all_tasks'] or 0,
                'deleted': row['deleted'] or 0}

    def meeting_overview(self, session_id):
        """First non-deleted task page plus counts and suggestions for meeting_page."""
        self._known_session(session_id)
        page = self.page(session_id=session_id, status='all')
        return {'tasks': page['tasks'], 'tasks_next_offset': page['next_offset'],
                'task_counts': page['counts'],
                'suggestions': self.suggestions(session_id)}

    def carryover_candidates(self, session_id):
        """Open tasks from earlier meetings with the same normalized title."""
        session = self._session(session_id)
        key = normalize_text(session['title'])
        if not key:
            return []
        # Recent meetings first; the Python-side normalized comparison is bounded
        # so a large workspace cannot build an unbounded parameter list.
        meetings = [row for row in self.conn.execute(
            'SELECT id,title,started_at FROM sessions WHERE id!=? AND title IS NOT NULL '
            'AND started_at IS NOT NULL AND started_at<=? '
            'ORDER BY started_at DESC, id DESC LIMIT 500',
            (session_id, session['started_at'] or 0))
            if normalize_text(row['title']) == key][:100]
        if not meetings:
            return []
        marks = ','.join('?' * len(meetings))
        rows = self.conn.execute(
            f'SELECT t.*, s.title AS meeting_title, s.started_at AS meeting_started_at '
            f'FROM tasks t JOIN sessions s ON s.id=t.session_id '
            f'WHERE t.session_id IN ({marks}) AND t.status=\'open\' '
            f'ORDER BY s.started_at DESC, t.created_at DESC, t.id ASC LIMIT ?',
            (*[row['id'] for row in meetings], MAX_CARRYOVER)).fetchall()
        return [task_row(row) for row in rows]

    # ---------- suggestion review ----------

    def _active_or_raise(self, session_id):
        summary = self.active_summary(session_id)
        if not summary:
            raise ValueError('Generate notes before reviewing suggestions')
        return summary

    def _suggestion_batch(self, session_id, values, *, summary=None, state='proposed'):
        if (not isinstance(values, list) or not 1 <= len(values) <= MAX_BATCH
                or any(not isinstance(value, str) or not ID.fullmatch(value) for value in values)):
            raise ValueError(f'Review between one and {MAX_BATCH} suggestions at a time')
        if len(set(values)) != len(values):
            raise ValueError('Review each suggestion once')
        rows = {}
        for value in values:
            row = self.conn.execute('SELECT * FROM task_suggestions WHERE id=? AND session_id=?',
                                    (value, session_id)).fetchone()
            if not row or (summary is not None and row['summary_id'] != summary['id']):
                raise ValueError('Review the suggestions of the current saved notes')
            if row['state'] != state:
                raise ValueError('That suggestion was already reviewed')
            rows[value] = row
        return rows

    def _accept_edits(self, item):
        """Per-item edit slots; omission keeps suggestion/prior values, null clears."""
        if not isinstance(item, dict):
            raise TypeError('Invalid suggestion acceptance')
        if not isinstance(item.get('suggestion_id'), str) or not ID.fullmatch(item['suggestion_id']):
            raise ValueError('Invalid suggestion')
        edits = {}
        if 'title' in item:
            edits['title'] = self._clean_title(item['title'])
        if 'owner' in item:
            owner = item['owner']
            if owner is None:
                edits['owner'] = (None, None)
            elif isinstance(owner, dict) and set(owner) <= {'person_id', 'label'}:
                person = self._check_owner_person(owner.get('person_id'))
                label = self._clean_owner(owner['label']) if owner.get('label') is not None else None
                if person and label:
                    raise ValueError('Choose a person or a name for the owner, not both')
                edits['owner'] = (person, label)
            else:
                raise ValueError('Invalid task owner')
        if 'due_date' in item:
            due = item['due_date']
            if due is None:
                edits['due'] = None
            else:
                cleaned = valid_due(due)
                if cleaned is None:
                    raise ValueError('Use a due date as YYYY-MM-DD')
                edits['due'] = cleaned
        if 'allow_similar' in item and not isinstance(item['allow_similar'], bool):
            raise ValueError('Invalid similar-task confirmation')
        if not set(item) <= {'suggestion_id', 'title', 'owner', 'due_date', 'allow_similar'}:
            raise ValueError('Invalid suggestion acceptance fields')
        return edits

    def _accept_plan(self, session_id, row, item):
        edits = self._accept_edits(item)
        # Ambiguity is judged against the meeting's current open tasks, not the
        # candidate stored when the summary was saved.
        current_similar = self._current_similar(session_id, row['task'])
        if current_similar and not item.get('allow_similar'):
            raise ValueError('Confirm adding a task similar to an existing task')
        fingerprint = row['fingerprint']
        existing = self.conn.execute(
            'SELECT * FROM tasks WHERE session_id=? AND suggestion_fingerprint=?',
            (session_id, fingerprint)).fetchone()
        if existing and existing['status'] != 'deleted':
            # Reconciliation linked it on save; acceptance stays idempotent.
            return {'kind': 'link', 'fingerprint': fingerprint, 'similar_id': current_similar,
                    'suggestion_id': row['id'], 'task_id': existing['id']}
        if existing:
            # The deleted identity stays reserved; acceptance restores that row.
            return {'kind': 'restore', 'fingerprint': fingerprint, 'suggestion_id': row['id'],
                    'task_id': existing['id'], 'edits': edits, 'similar_id': current_similar}
        owner = edits.get('owner')
        if owner is None:  # omitted: prefill only an unambiguous stated owner label
            label = row['owner']
            owner = (None, self._clean_owner(label)
                     if isinstance(label, str) and label.strip() and len(label) <= MAX_OWNER
                     else None)
        due = edits.get('due', valid_due(row['due']))
        return {'kind': 'create', 'fingerprint': fingerprint, 'suggestion_id': row['id'],
                'similar_id': current_similar,
                'title': edits.get('title', self._clean_title(row['task'])),
                'owner': owner, 'due': due,
                'summary_id': row['summary_id'], 'segment_ids': row['segment_ids']}

    def accept_suggestions(self, session_id, items):
        """Atomic batch accept with optional edits; deleted fingerprints restore."""
        self._session(session_id)
        summary = self._active_or_raise(session_id)
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
            raise ValueError(f'Accept between one and {MAX_BATCH} suggestions at a time')
        rows = self._suggestion_batch(
            session_id,
            [item.get('suggestion_id') if isinstance(item, dict) else None for item in items],
            summary=summary)
        plans = [self._accept_plan(session_id, rows[item['suggestion_id']], item)
                 for item in items]
        unique_kinds = {}
        for plan in plans:
            unique_kinds.setdefault(plan['fingerprint'], plan['kind'])
        self._check_limit(session_id,
                          sum(1 for kind in unique_kinds.values() if kind in ('create', 'restore')))
        results, resolved = [], {}
        with self.conn:
            for plan in plans:
                fingerprint = plan['fingerprint']
                if fingerprint in resolved:
                    # Duplicate fingerprints in one batch resolve to one identity.
                    self.conn.execute("UPDATE task_suggestions SET state='linked',task_id=? "
                                      'WHERE id=? AND session_id=?',
                                      (resolved[fingerprint], plan['suggestion_id'], session_id))
                    results.append(task_row(self._task(resolved[fingerprint])))
                    continue
                if plan['kind'] == 'link':
                    task_id = plan['task_id']
                    self.conn.execute("UPDATE task_suggestions SET state='linked',task_id=? "
                                      'WHERE id=? AND session_id=?',
                                      (task_id, plan['suggestion_id'], session_id))
                elif plan['kind'] == 'restore':
                    task_id = self._restore_row(plan['task_id'], plan['edits'])
                else:
                    task_id = self._insert_suggestion_task(session_id, plan)
                if plan.get('similar_id') != rows[plan['suggestion_id']]['similar_task_id']:
                    # Keep the stored review candidate aligned with current state.
                    self.conn.execute('UPDATE task_suggestions SET similar_task_id=? '
                                      'WHERE id=? AND session_id=?',
                                      (plan.get('similar_id'), plan['suggestion_id'], session_id))
                self.conn.execute("UPDATE task_suggestions SET state='accepted',task_id=? "
                                  'WHERE id=? AND session_id=?',
                                  (task_id, plan['suggestion_id'], session_id))
                self.conn.execute("UPDATE task_suggestions SET state='linked',task_id=? "
                                  "WHERE session_id=? AND fingerprint=? AND state='proposed' "
                                  'AND id!=?',
                                  (task_id, session_id, fingerprint, plan['suggestion_id']))
                resolved[fingerprint] = task_id
                results.append(task_row(self._task(task_id)))
        return results

    def _insert_suggestion_task(self, session_id, plan):
        now = self._now()
        person, label = plan['owner']
        task_id = uuid4().hex
        summary = self.conn.execute('SELECT revision FROM grounded_summaries WHERE id=?',
                                    (plan['summary_id'],)).fetchone()
        self.conn.execute(
            'INSERT INTO tasks (id,session_id,origin,title,owner_person_id,owner_label,'
            'owner_removed,due_date,status,restore_status,source_session_id,source_summary_id,'
            'source_revision,source_segment_ids,suggestion_fingerprint,carryover_task_id,'
            "carryover_note,created_at,updated_at,completed_at) "
            "VALUES (?,?,'suggestion',?,?,?,?,?,'open',NULL,?,?,?,?,?,NULL,NULL,?,?,NULL)",
            (task_id, session_id, plan['title'], person, label, 0, plan['due'],
             session_id, plan['summary_id'],
             summary['revision'] if summary else None, plan['segment_ids'],
             plan['fingerprint'], now, now))
        return task_id

    def _restore_row(self, task_id, edits=None):
        """Restore a deleted task inside the caller's transaction; idempotent."""
        row = self._task(task_id)
        if row['status'] != 'deleted':
            return task_id
        edits = edits or {}
        status = row['restore_status'] or 'open'
        title = edits.get('title', row['title'])
        person, label = row['owner_person_id'], row['owner_label']
        owner_removed = row['owner_removed']
        if 'owner' in edits:
            person, label = edits['owner']
            owner_removed = 0
        due = edits.get('due', row['due_date'])
        completed = row['completed_at'] if status == 'done' else None
        self.conn.execute(
            'UPDATE tasks SET status=?,restore_status=NULL,title=?,owner_person_id=?,'
            'owner_label=?,owner_removed=?,due_date=?,completed_at=?,updated_at=? WHERE id=?',
            (status, title, person, label, int(bool(owner_removed)), due, completed,
             self._now(), task_id))
        return task_id

    def dismiss_suggestions(self, session_id, values):
        self._session(session_id)
        summary = self._active_or_raise(session_id)
        rows = self._suggestion_batch(session_id, values, summary=summary)
        with self.conn:
            for suggestion_id in rows:
                self.conn.execute("UPDATE task_suggestions SET state='dismissed' WHERE id=?",
                                  (suggestion_id,))

    def restore_suggestions(self, session_id, values):
        """Undo dismissal: clear that fingerprint's dismissal history, then reconcile."""
        self._session(session_id)
        rows = self._suggestion_batch(session_id, values, state='dismissed')
        with self.conn:
            # Clear the history first so reconciliation cannot see the rows that
            # are being restored and reinstate the dismissal.
            for row in rows.values():
                self.conn.execute("UPDATE task_suggestions SET state='proposed',task_id=NULL "
                                  'WHERE session_id=? AND fingerprint=? AND state=\'dismissed\'',
                                  (session_id, row['fingerprint']))
            for row in rows.values():
                state, task_id, similar_id = reconcile_state(
                    self.conn, session_id, row['task'], row['fingerprint'])
                self.conn.execute(
                    'UPDATE task_suggestions SET state=?,task_id=?,similar_task_id=? '
                    'WHERE session_id=? AND fingerprint=?',
                    (state, task_id, similar_id, session_id, row['fingerprint']))

    def link_suggestion(self, session_id, suggestion_id, task_id):
        """Keep existing: link the suggestion to its current similarity candidate."""
        self._session(session_id)
        summary = self._active_or_raise(session_id)
        rows = self._suggestion_batch(session_id, [suggestion_id], summary=summary)
        row = rows[suggestion_id]
        current = self._current_similar(session_id, row['task'])
        with self.conn:
            self._refresh_similar(suggestion_id, current)
        target = self._task(task_id)
        if (target['session_id'] != session_id or target['status'] != 'open'
                or not similar_task_text(row['task'], target['title'])):
            raise ValueError('That task no longer matches the suggestion. Review the suggestion again.')
        if current != task_id:
            raise ValueError('Link the suggestion to its current similar task')
        with self.conn:
            self.conn.execute("UPDATE task_suggestions SET state='linked',task_id=? WHERE id=?",
                              (task_id, suggestion_id))

    # ---------- task CRUD ----------

    def create(self, session_id, title, *, owner_person_id=None, owner_label=None,
               due_date=None):
        self._session(session_id)
        title = self._clean_title(title)
        person = self._check_owner_person(owner_person_id)
        label = self._clean_owner(owner_label) if owner_label is not None else None
        if person and label:
            raise ValueError('Choose a person or a name for the owner, not both')
        due = valid_due(due_date) if due_date is not None else None
        if due_date is not None and due is None:
            raise ValueError('Use a due date as YYYY-MM-DD')
        now = self._now()
        task_id = uuid4().hex
        with self.conn:
            self._check_limit(session_id, 1)
            self.conn.execute(
                'INSERT INTO tasks (id,session_id,origin,title,owner_person_id,owner_label,'
                'owner_removed,due_date,status,restore_status,source_session_id,source_summary_id,'
                'source_revision,source_segment_ids,suggestion_fingerprint,carryover_task_id,'
                "carryover_note,created_at,updated_at,completed_at) "
                "VALUES (?,?,'manual',?,?,?,0,?,'open',NULL,NULL,NULL,NULL,NULL,"
                'NULL,NULL,NULL,?,?,NULL)', (task_id, session_id, title, person, label,
                                             due, now, now))
        return task_row(self._task(task_id))

    def update(self, task_id, **fields):
        row = self._task(task_id)
        if row['status'] == 'deleted':
            raise ValueError('Restore the task before editing')
        if not set(fields) <= {'title', 'owner_person_id', 'owner_label', 'clear_owner',
                               'due_date', 'clear_due', 'status'}:
            raise ValueError('Invalid task edit')
        owner_changes = {'owner_person_id', 'owner_label', 'clear_owner'} & set(fields)
        if len(owner_changes) > 1:
            raise ValueError('Choose one owner edit')
        if {'due_date', 'clear_due'} & set(fields) == {'due_date', 'clear_due'}:
            raise ValueError('Choose one due-date edit')
        status = fields.get('status')
        if status is not None and status not in ('open', 'done', 'dismissed'):
            raise ValueError('Invalid task status')
        if status is not None and status != row['status'] and row['status'] == 'dismissed':
            raise ValueError('Dismissed tasks stay dismissed')
        title = self._clean_title(fields['title']) if 'title' in fields else None
        person = self._check_owner_person(fields.get('owner_person_id'))
        label = self._clean_owner(fields['owner_label']) if 'owner_label' in fields else None
        due = valid_due(fields['due_date']) if fields.get('due_date') is not None else None
        if fields.get('due_date') is not None and due is None:
            raise ValueError('Use a due date as YYYY-MM-DD')
        with self.conn:
            sets, params = ['updated_at=?'], [self._now()]
            if title is not None:
                sets.append('title=?')
                params.append(title)
            if 'clear_owner' in fields:
                sets.extend(['owner_person_id=NULL', 'owner_label=NULL', 'owner_removed=0'])
            elif 'owner_person_id' in fields:
                sets.extend(['owner_person_id=?', 'owner_label=NULL', 'owner_removed=0'])
                params.append(person)
            elif 'owner_label' in fields:
                sets.extend(['owner_label=?', 'owner_person_id=NULL', 'owner_removed=0'])
                params.append(label)
            if 'clear_due' in fields:
                sets.append('due_date=NULL')
            elif 'due_date' in fields:
                sets.append('due_date=?')
                params.append(due)
            if status is not None:
                sets.append('status=?')
                params.append(status)
                sets.append('completed_at=?')
                params.append(self._now() if status == 'done' else None)
            params.append(task_id)
            self.conn.execute(f'UPDATE tasks SET {", ".join(sets)} WHERE id=?', params)
        return task_row(self._task(task_id))

    def delete(self, task_id):
        """Soft delete: the identity stays reserved so Undo cannot hit a conflict."""
        row = self._task(task_id)
        if row['status'] == 'deleted':
            return task_row(row)  # repeated requests stay idempotent
        with self.conn:
            self.conn.execute("UPDATE tasks SET restore_status=status,status='deleted',"
                              'updated_at=? WHERE id=?', (self._now(), task_id))
            # Suggestions are never hard-deleted by task deletion; they return to
            # proposed so the user can accept again deliberately.
            self.conn.execute("UPDATE task_suggestions SET state='proposed',task_id=NULL "
                              'WHERE task_id=?', (task_id,))
            self.conn.execute('UPDATE task_suggestions SET similar_task_id=NULL '
                              'WHERE similar_task_id=?', (task_id,))
        return task_row(self._task(task_id))

    # ---------- carryover ----------

    def accept_carryover(self, session_id, source_task_id):
        """Explicit carryover: one reserved destination identity per source task."""
        self._session(session_id)
        source = self._task(source_task_id)
        if source['status'] != 'open':
            raise ValueError('Only open tasks can be carried over')
        if source['session_id'] == session_id:
            raise ValueError('Choose a task from another meeting')
        origin = self.conn.execute('SELECT * FROM sessions WHERE id=?',
                                   (source['session_id'],)).fetchone()
        meeting = self.conn.execute('SELECT * FROM sessions WHERE id=?',
                                    (session_id,)).fetchone()
        if (not origin or not origin['title']
                or normalize_text(origin['title']) != normalize_text(meeting['title'])
                or (origin['started_at'] or 0) > (meeting['started_at'] or 0)):
            raise ValueError('Choose a task from an earlier meeting with the same name')
        existing = self.conn.execute('SELECT * FROM tasks WHERE session_id=? AND carryover_task_id=?',
                                     (session_id, source_task_id)).fetchone()
        if existing and existing['status'] != 'deleted':
            return task_row(existing)  # repeated acceptance returns the destination
        self._check_limit(session_id, 1)
        with self.conn:
            if existing:
                # Acceptance after deletion restores without overwriting edits.
                self._restore_row(existing['id'])
            else:
                started = (datetime.fromtimestamp(origin['started_at'] / 1000, timezone.utc)
                           if origin['started_at'] else None)
                note = ('Carried over from ' + (origin['title'] or 'Untitled meeting')
                        + (f", {started.date().isoformat()}" if started else ''))
                evidence = source['source_session_id'] or source['session_id']
                self.conn.execute(
                    'INSERT INTO tasks (id,session_id,origin,title,owner_person_id,owner_label,'
                    'owner_removed,due_date,status,restore_status,source_session_id,'
                    'source_summary_id,source_revision,source_segment_ids,'
                    'suggestion_fingerprint,carryover_task_id,carryover_note,'
                    "created_at,updated_at,completed_at) "
                    "VALUES (?,?,'carryover',?,?,?,?,?,'open',NULL,?,?,?,?,NULL,?,?,?,?,NULL)",
                    (uuid4().hex, session_id, source['title'], source['owner_person_id'],
                     source['owner_label'], source['owner_removed'], source['due_date'],
                     evidence, source['source_summary_id'], source['source_revision'],
                     source['source_segment_ids'], source['id'], note,
                     self._now(), self._now()))
            created = self.conn.execute(
                'SELECT * FROM tasks WHERE session_id=? AND carryover_task_id=?',
                (session_id, source_task_id)).fetchone()
        return task_row(created)

    def restore(self, task_id):
        row = self._task(task_id)
        if row['status'] != 'deleted':
            return task_row(row)  # repeated requests stay idempotent
        self._check_limit(row['session_id'], 1)
        with self.conn:
            self._restore_row(task_id)
            fingerprint = row['suggestion_fingerprint']
            if fingerprint:
                self.conn.execute("UPDATE task_suggestions SET state='linked',task_id=? "
                                  "WHERE session_id=? AND fingerprint=? AND state='proposed'",
                                  (task_id, row['session_id'], fingerprint))
        return task_row(self._task(task_id))

    # ---------- person forgetting ----------

    def forget_owner(self, person_id, canonical_name):
        """Runs inside the caller's forget() transaction; returns affected meetings.

        Includes deleted tasks and carryover copies: the owner link and any
        matching copied/free-text label are removed from derived task data while
        user-approved task text stays intact. Summary deletion cascades to
        suggestions, removing copied owner names from suggestion rows too.
        """
        key = normalize_text(canonical_name)
        linked = self.conn.execute('SELECT id,session_id FROM tasks WHERE owner_person_id=?',
                                   (person_id,)).fetchall()
        linked_ids = {row['id'] for row in linked}
        affected = {row['session_id'] for row in linked}
        matching = [row for row in self.conn.execute(
            'SELECT id,session_id,owner_label FROM tasks WHERE owner_label IS NOT NULL')
            if row['id'] not in linked_ids and normalize_text(row['owner_label']) == key]
        suggestion_sessions = {row['session_id'] for row in self.conn.execute(
            'SELECT session_id,owner FROM task_suggestions WHERE owner IS NOT NULL')
            if normalize_text(row['owner']) == key}
        ids = [row['id'] for row in linked] + [row['id'] for row in matching]
        if ids:
            marks = ','.join('?' * len(ids))
            self.conn.execute(
                'UPDATE tasks SET owner_person_id=NULL,owner_label=NULL,owner_removed=1 '
                f'WHERE id IN ({marks})', ids)
        affected.update(row['session_id'] for row in matching)
        affected.update(suggestion_sessions)
        return affected











