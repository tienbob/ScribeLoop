"""Local template drafts, committed together only when Settings is saved."""
import hashlib
import re
import time

from ..llm.note_templates import NoteTemplate, canonical_json
from ..storage.note_templates import NoteTemplateStore


def library(store):
    templates = NoteTemplateStore(store.conn)
    entries = templates.list()
    default = templates.default_id()
    revision = hashlib.sha256(canonical_json([entries, default]).encode()).hexdigest()
    for entry in entries:
        entry['affected_meetings'] = store.conn.execute(
            'SELECT COUNT(*) FROM session_note_templates WHERE template_id=?', (entry['id'],)).fetchone()[0]
    return {'templates': entries, 'default_id': default, 'library_revision': revision}


def active_generation(store, session_id):
    if not session_id:
        return {}
    row = store.conn.execute(
        "SELECT g.id FROM note_generations g JOIN note_generation_runs r ON r.id=g.id "
        "WHERE g.session_id=? AND r.state='active' ORDER BY g.rowid DESC LIMIT 1", (session_id,)).fetchone()
    return NoteTemplateStore(store.conn).generation(row['id']) if row else {}


def save_library(store, command):
    if set(command) != {'action', 'templates', 'default_id', 'library_revision'}:
        raise ValueError('Invalid template settings')
    drafts = command['templates']
    if not isinstance(drafts, list) or len(drafts) > 50:
        raise ValueError('Keep at most 50 custom templates')
    validated = {}
    for draft in drafts:
        if (not isinstance(draft, dict) or set(draft) != {'id', 'definition'}
                or not isinstance(draft['id'], str)
                or not re.fullmatch(r'custom-[0-9a-f]{32}', draft['id'])
                or draft['id'] in validated):
            raise ValueError('Invalid custom template')
        validated[draft['id']] = NoteTemplate.model_validate(draft['definition'])
    with store.conn:
        current = library(store)
        if command['library_revision'] != current['library_revision']:
            raise ValueError('Templates changed. Reopen Settings before saving')
        prior = {entry['id']: entry for entry in current['templates']}
        default = command['default_id']
        builtins = {key for key, entry in prior.items() if entry['builtin']}
        if not isinstance(default, str) or default not in builtins | validated.keys():
            raise ValueError('Choose an available default template')
        removed = [key for key in prior if key not in builtins and key not in validated]
        affected = 0
        for key in removed:
            affected += store.conn.execute(
                "UPDATE session_note_templates SET template_id='general' WHERE template_id=?", (key,)).rowcount
            store.conn.execute("UPDATE note_template_preferences SET default_id='general' WHERE default_id=?", (key,))
            store.conn.execute('DELETE FROM note_templates WHERE id=?', (key,))
        for key, definition in validated.items():
            payload = canonical_json(definition.model_dump())
            if key in prior:
                if definition.model_dump() != prior[key]['definition']:
                    store.conn.execute('UPDATE note_templates SET name=?,definition_json=?,version=version+1,updated_at=? WHERE id=?',
                                       (definition.name, payload, int(time.time()), key))
            else:
                store.conn.execute('INSERT INTO note_templates VALUES (?,?,1,0,?,?)',
                                   (key, definition.name, payload, int(time.time())))
        store.conn.execute('UPDATE note_template_preferences SET default_id=? WHERE id=1', (default,))
    if removed:
        from ..storage.db import purge_checkpoint
        purge_checkpoint(store.conn)
    return {**library(store), 'affected_meetings': affected}
