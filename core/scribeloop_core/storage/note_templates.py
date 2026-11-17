"""Workspace note templates and immutable generation policy in SQLCipher."""
from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlsplit
from uuid import uuid4

from ..llm.note_templates import NoteTemplate, canonical_json, definition_hash


class NoteTemplateStore:
    MAX_CUSTOM = 50

    def __init__(self, conn):
        self.conn = conn

    def get(self, template_id):
        row = self.conn.execute('SELECT * FROM note_templates WHERE id=?', (template_id,)).fetchone()
        if row is None:
            raise ValueError('Note template no longer exists')
        definition = NoteTemplate.model_validate(json.loads(row['definition_json'])).model_dump()
        return {'id': row['id'], 'version': row['version'], 'builtin': bool(row['builtin']),
                'definition': definition, 'definition_hash': definition_hash(definition)}

    def list(self):
        ids = self.conn.execute('SELECT id FROM note_templates ORDER BY builtin DESC, name COLLATE NOCASE, id').fetchall()
        return [self.get(row['id']) for row in ids]

    def default_id(self):
        return self.conn.execute('SELECT default_id FROM note_template_preferences WHERE id=1').fetchone()[0]

    def set_default(self, template_id):
        self.get(template_id)
        with self.conn:
            self.conn.execute('UPDATE note_template_preferences SET default_id=? WHERE id=1', (template_id,))

    def attach_new_session(self, session_id):
        """Participate in the caller's recording-creation transaction; never commit."""
        self.conn.execute('INSERT INTO session_note_templates(session_id,template_id) VALUES (?,?)',
                          (session_id, self.default_id()))

    def selection(self, session_id):
        if not self.conn.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
            raise ValueError('Conversation no longer exists')
        row = self.conn.execute('SELECT template_id FROM session_note_templates WHERE session_id=?', (session_id,)).fetchone()
        return self.get(row['template_id'] if row else 'general')

    def select(self, session_id, template_id):
        self.selection(session_id)
        self.get(template_id)
        with self.conn:
            self.conn.execute('INSERT INTO session_note_templates(session_id,template_id) VALUES (?,?) '
                              'ON CONFLICT(session_id) DO UPDATE SET template_id=excluded.template_id',
                              (session_id, template_id))
        return self.selection(session_id)

    def create(self, definition):
        template = NoteTemplate.model_validate(definition)
        template_id = 'custom-' + uuid4().hex
        with self.conn:
            count = self.conn.execute('SELECT COUNT(*) FROM note_templates WHERE builtin=0').fetchone()[0]
            if count >= self.MAX_CUSTOM:
                raise ValueError('The workspace supports at most 50 custom note templates')
            self.conn.execute('INSERT INTO note_templates(id,name,version,builtin,definition_json,updated_at) '
                              'VALUES (?,?,1,0,?,?)',
                              (template_id, template.name, canonical_json(template.model_dump()), int(time.time())))
        return self.get(template_id)

    def update(self, template_id, definition, *, expected_version):
        template = NoteTemplate.model_validate(definition)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError('Invalid template version')
        with self.conn:
            current = self.get(template_id)
            if current['builtin']:
                raise ValueError('Copy a built-in template before editing it')
            cursor = self.conn.execute('UPDATE note_templates SET name=?,definition_json=?,version=version+1,updated_at=? '
                                       'WHERE id=? AND builtin=0 AND version=?',
                                       (template.name, canonical_json(template.model_dump()), int(time.time()),
                                        template_id, expected_version))
            if cursor.rowcount != 1:
                raise ValueError('Template changed. Reopen it before saving')
        return self.get(template_id)

    def delete(self, template_id):
        with self.conn:
            if self.get(template_id)['builtin']:
                raise ValueError('Built-in note templates cannot be deleted')
            meetings = self.conn.execute('SELECT COUNT(*) FROM session_note_templates WHERE template_id=?', (template_id,)).fetchone()[0]
            default = self.default_id() == template_id
            self.conn.execute('UPDATE session_note_templates SET template_id=\'general\' WHERE template_id=?', (template_id,))
            self.conn.execute('UPDATE note_template_preferences SET default_id=\'general\' WHERE default_id=?', (template_id,))
            self.conn.execute('DELETE FROM note_templates WHERE id=?', (template_id,))
        from .db import purge_checkpoint
        purge_checkpoint(self.conn)
        return {'affected_meetings': meetings, 'default_reset': default}

    def begin_generation(self, session_id, revision, *, guidance='', provider, model, destination,
                         language_policy='conversation', kind='manual'):
        """Create once before dispatch. Callers must carry this snapshot through all nodes."""
        if type(revision) is not int or revision < 1:
            raise ValueError('Invalid source revision')
        for label, value, limit in [('guidance', guidance, 2000), ('provider', provider, 80),
                                    ('model', model, 100), ('destination', destination, 500)]:
            if not isinstance(value, str) or len(value) > limit or (label != 'guidance' and not value.strip()):
                raise ValueError('Invalid generation ' + label)
        url = urlsplit(destination)
        if (url.scheme not in ('https', 'http') or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise ValueError('Generation destination must be a public endpoint without credentials or query')
        if language_policy not in ('conversation', 'en', 'vi'):
            raise ValueError('Invalid generation language policy')
        if kind not in ('manual', 'automatic', 'rolling'):
            raise ValueError('Invalid notes generation kind')
        with self.conn:
            if not self.conn.execute('SELECT 1 FROM transcript_revisions WHERE session_id=? AND revision=?',
                                     (session_id, revision)).fetchone():
                raise ValueError('Transcript revision no longer exists')
            template = self.selection(session_id)
            generation_id = uuid4().hex
            snapshot = {'id': generation_id, 'session_id': session_id, 'source_revision': revision,
                        'template': template, 'guidance': guidance, 'presentation_version': 1,
                        'language_policy': language_policy, 'provider': provider, 'model': model,
                        'destination': destination}
            payload = canonical_json(snapshot)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            self.conn.execute('INSERT INTO note_generations(id,session_id,source_revision,snapshot_json,snapshot_hash,created_at) '
                              'VALUES (?,?,?,?,?,?)', (generation_id, session_id, revision, payload, digest, int(time.time())))
            self.conn.execute("INSERT INTO note_generation_runs(id,kind,state) VALUES (?,?,'active')", (generation_id, kind))
        return {**snapshot, 'snapshot_hash': digest}

    def rolling_generation(self, session_id, revision, *, final=False):
        # Two static statements instead of interpolating the comparison operator:
        # the snapshot lookup never builds SQL from values (bandit B608).
        if final:
            row = self.conn.execute(
                "SELECT g.id FROM note_generations g JOIN note_generation_runs r ON r.id=g.id "
                "WHERE g.session_id=? AND g.source_revision<=? AND r.kind='rolling' "
                "AND r.state IN ('active','failed') ORDER BY g.rowid DESC LIMIT 1",
                (session_id, revision)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT g.id FROM note_generations g JOIN note_generation_runs r ON r.id=g.id "
                "WHERE g.session_id=? AND g.source_revision=? AND r.kind='rolling' "
                "AND r.state IN ('active','failed') ORDER BY g.rowid DESC LIMIT 1",
                (session_id, revision)).fetchone()
        return self.generation(row['id']) if row else None

    def rolling_run_state(self, session_id, revision):
        """State of the rolling run the monitor would reuse; None means a fresh generation would begin."""
        row = self.conn.execute(
            "SELECT r.state FROM note_generations g JOIN note_generation_runs r ON r.id=g.id "
            "WHERE g.session_id=? AND g.source_revision=? AND r.kind='rolling' "
            "AND r.state IN ('active','failed') ORDER BY g.rowid DESC LIMIT 1",
            (session_id, revision)).fetchone()
        return row['state'] if row else None

    def admit_final_revision(self, generation_id, revision):
        policy = self.generation(generation_id)
        if type(revision) is not int or revision < policy['source_revision']:
            raise ValueError('Invalid final notes source revision')
        if not self.conn.execute('SELECT 1 FROM transcript_revisions WHERE session_id=? AND revision=?',
                                 (policy['session_id'], revision)).fetchone():
            raise ValueError('Final transcript revision no longer exists')
        with self.conn:
            changed = self.conn.execute("UPDATE note_generation_runs SET final_revision=? WHERE id=? AND kind='rolling' "
                                        "AND state IN ('active','failed') AND (final_revision IS NULL OR final_revision=?)",
                                        (revision, generation_id, revision)).rowcount
            if changed != 1:
                raise ValueError('Final notes source scope is already fixed')

    def generation_allows_revision(self, generation_id, revision):
        policy = self.generation(generation_id)
        if policy['source_revision'] == revision:
            return True
        row = self.conn.execute('SELECT final_revision FROM note_generation_runs WHERE id=?', (generation_id,)).fetchone()
        return bool(row and row['final_revision'] == revision)

    def mark_generation(self, generation_id, state):
        if state not in ('failed', 'completed'):
            raise ValueError('Invalid notes generation state')
        with self.conn:
            # A late UI disconnect/cancellation must not relabel a committed success.
            self.conn.execute("UPDATE note_generation_runs SET state=? WHERE id=? AND state!='completed'", (state, generation_id))

    def generation(self, generation_id):
        row = self.conn.execute('SELECT snapshot_json,snapshot_hash FROM note_generations WHERE id=?', (generation_id,)).fetchone()
        if row is None:
            raise ValueError('Notes generation no longer exists')
        if hashlib.sha256(row['snapshot_json'].encode()).hexdigest() != row['snapshot_hash']:
            raise ValueError('Notes generation snapshot is invalid')
        return {**json.loads(row['snapshot_json']), 'snapshot_hash': row['snapshot_hash']}
