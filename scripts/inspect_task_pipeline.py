"""Read-only F4/F5 triage: why a meeting recorded zero tasks.

Prints per-meeting pipeline state — recording status, transcript size, grounded
summary presence and action-item counts, suggestion states, task counts, summary
outcome codes and notes-generation run states — without printing transcript,
summary or note content. Titles are truncated identifiers only.

Safety: refuses to open a workspace whose process lock is held (the desktop app
is running), reads the existing Keychain key without ever creating or rotating
one, never runs migrations, and executes SELECT statements only.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_WORKSPACE = Path('~/Library/Application Support/ScribeLoop/meetings.sqlcipher')


def iso(ms):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(ms / 1000)) if ms else '—'


def counts(conn, sql, args=()):
    return {row[0]: row[1] for row in conn.execute(sql, args)}


def one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return row if row is not None else None


def inspect_meeting(conn, row):
    sid = row['id']
    info = {
        'meeting_id': sid,
        'title': (row['title'] or 'Untitled')[:60],
        'status': row['status'],
        'started': iso(row['started_at']),
        'source': row['source'],
    }
    ended = one(conn, "SELECT payload_json FROM events WHERE session_id=? AND type='recording_end' "
                      'ORDER BY id DESC LIMIT 1', (sid,))
    try:
        info['end_reason'] = json.loads(ended['payload_json'])['end_reason'] if ended else 'unknown'
    except (TypeError, ValueError):
        info['end_reason'] = 'unknown'
    segment = one(conn, 'SELECT COUNT(*) AS n,MAX(revision) AS r FROM segments WHERE session_id=?', (sid,))
    info['segments'] = segment['n'] if segment else 0
    info['transcript_revision'] = segment['r'] if segment and segment['r'] else None
    summary = one(conn, 'SELECT id,provider,model,created_at,summary_json '
                        'FROM grounded_summaries WHERE session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (sid,))
    if summary:
        try:
            parsed = json.loads(summary['summary_json'])
            items = parsed.get('action_items') if isinstance(parsed, dict) else None
            action_items = items if isinstance(items, list) else []
        except (TypeError, ValueError):
            action_items = []
        info['grounded_summary'] = {
            'id': summary['id'], 'provider': summary['provider'], 'model': summary['model'],
            'created': iso(summary['created_at']),
            'action_items': len(action_items),
            'action_items_with_owner': sum(1 for i in action_items if isinstance(i, dict) and i.get('owner')),
            'action_items_with_due': sum(1 for i in action_items if isinstance(i, dict) and i.get('due')),
        }
    else:
        info['grounded_summary'] = None
    info['suggestions_by_state'] = counts(conn, 'SELECT state,COUNT(*) FROM task_suggestions '
                                               'WHERE session_id=? GROUP BY state', (sid,))
    info['tasks_by_status'] = counts(conn, 'SELECT status,COUNT(*) FROM tasks '
                                           'WHERE session_id=? GROUP BY status', (sid,))
    outcome = one(conn, "SELECT payload_json FROM events WHERE session_id=? AND type='summary_outcome' "
                        'ORDER BY id DESC LIMIT 1', (sid,))
    if outcome:
        try:
            payload = json.loads(outcome['payload_json'])
            info['last_summary_outcome'] = payload.get('code') or 'ok'
        except (TypeError, ValueError):
            info['last_summary_outcome'] = 'undecodable'
    info['generation_runs'] = [
        {'kind': r['kind'], 'state': r['state'], 'final_revision': r['final_revision']}
        for r in conn.execute('SELECT r.kind AS kind,r.state AS state,r.final_revision AS final_revision '
                              'FROM note_generation_runs r JOIN note_generations g ON g.id=r.id '
                              'WHERE g.session_id=? ORDER BY g.created_at DESC LIMIT 5', (sid,))]
    provisional = counts(conn, 'SELECT kind,COUNT(*) FROM summaries WHERE session_id=? GROUP BY kind', (sid,))
    if provisional:
        info['provisional_note_rows'] = provisional
    info['verdict'] = verdict(info)
    return info

def verdict(info):
    if info['status'] in {'recording', 'processing', 'preparing', 'stopping'}:
        return 'recording/processing has not finished; notes and tasks come after Stop'
    if info['grounded_summary'] is None:
        if any(r['state'] == 'failed' for r in info['generation_runs']):
            return ("notes generation FAILED (a generation run is in state 'failed'; last outcome "
                    f"{info.get('last_summary_outcome')}) — automatic retries stay blocked; "
                    "press Generate notes in the Notes tab to retry explicitly")
        if info['provisional_note_rows']:
            return 'only provisional/rolling notes exist; no final cited summary was saved yet'
        if not info['segments']:
            return 'no transcript segments; nothing for notes or tasks'
        return ('no grounded summary saved (automatic notes off at record time, '
                'notes provider missing, or generation never ran)')
    if info['grounded_summary']['action_items'] == 0:
        return ('the saved summary contains zero action_items — the extraction prompt only claims '
                'explicit commitments; a long exploratory conversation can legitimately yield none')
    if not info['suggestions_by_state']:
        return 'summary has action items but no suggestion rows (unexpected; investigate seeding)'
    pending = info['suggestions_by_state'].get('proposed', 0)
    if pending and not info['tasks_by_status']:
        return (f'{pending} suggestion(s) await review — tasks exist only after '
                'you accept them in the Tasks card')
    return 'tasks/suggestions present; see counts above'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--consent-inspect-workspace', action='store_true', required=True,
                        help='authorize opening this encrypted workspace read-only')
    args = parser.parse_args()
    if not args.consent_inspect_workspace:
        parser.error('Pass --consent-inspect-workspace to authorize the read-only inspection')
    path = args.workspace.expanduser().resolve()
    if not path.exists():
        raise SystemExit(f'Workspace not found: {path}')
    from scribeloop_core.storage.db import DATABASE_ERRORS
    from scribeloop_core.storage.encrypted import cipher_driver, connect_encrypted
    from scribeloop_core.storage.keys import NativeKeyStore
    cipher_driver()
    lock_fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('The workspace lock is held — ScribeLoop is running. '
                             'Quit ScribeLoop, then run this command again.') from None
        account = 'database:' + hashlib.sha256(os.fsencode(path)).hexdigest()
        key = NativeKeyStore().get(account)
        if key is None:
            raise SystemExit('The workspace key is not readable from this process (Keychain '
                             'denied access). No key was created; nothing was changed.')
        try:
            conn = connect_encrypted(path, key)
        except DATABASE_ERRORS:
            raise SystemExit('Workspace could not be opened read-only.') from None
        try:
            totals = {
                'meetings': one(conn, 'SELECT COUNT(*) AS n FROM sessions')['n'],
                'grounded_summaries': one(conn, 'SELECT COUNT(*) AS n FROM grounded_summaries')['n'],
                'suggestions': one(conn, 'SELECT COUNT(*) AS n FROM task_suggestions')['n'],
                'tasks': one(conn, 'SELECT COUNT(*) AS n FROM tasks')['n'],
            }
            rows = conn.execute('SELECT * FROM sessions ORDER BY started_at DESC,id DESC LIMIT ?',
                                (max(1, args.limit),)).fetchall()
            meetings = [inspect_meeting(conn, row) for row in rows]
        finally:
            conn.close()
    finally:
        os.close(lock_fd)
    if args.json:
        print(json.dumps({'workspace': str(path), 'totals': totals, 'meetings': meetings},
                         ensure_ascii=False, indent=2))
        return
    print(f'Workspace totals: {totals}')
    for meeting in meetings:
        gs = meeting['grounded_summary']
        print(f"\n● {meeting['title']}  ({meeting['meeting_id'][:8]}, {meeting['started']})")
        print(f"  status={meeting['status']} end={meeting['end_reason']} "
              f"segments={meeting['segments']} revision={meeting['transcript_revision']}")
        summary_text = 'none' if not gs else (
            f"{gs['provider']}/{gs['model']} at {gs['created']} · "
            f"action_items={gs['action_items']}")
        print(f'  grounded summary: {summary_text}')
        print(f"  suggestions: {meeting['suggestions_by_state'] or 'none'} · "
              f"tasks: {meeting['tasks_by_status'] or 'none'}")
        if meeting['generation_runs']:
            print(f"  generation runs: {meeting['generation_runs']}")
        if meeting.get('last_summary_outcome') is not None:
            print(f"  last summary outcome: {meeting['last_summary_outcome']}")
        print(f"  → {meeting['verdict']}")


if __name__ == '__main__':
    sys.exit(main())
