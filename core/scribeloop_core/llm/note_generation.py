"""Resolve immutable editorial policy before scheduling any notes dispatch."""
from __future__ import annotations

import contextlib
from copy import copy

from ..storage.db import DATABASE_ERRORS
from ..storage.note_templates import NoteTemplateStore


class NotesGeneration:
    def __init__(self, store, provider, snapshot, authorize):
        self.store = store
        self.provider = provider
        self.snapshot = snapshot
        self._authorize = authorize

    @classmethod
    def prepare(cls, store, provider, session_id, revision, *, mode):
        templates = NoteTemplateStore(store.conn)
        authorize = store.search_authorization()  # Captures the consent epoch.
        frozen = copy(provider)  # Keys stay in memory; settings cannot replace this request's destination.
        public = frozen.public()
        snapshot = templates.rolling_generation(session_id, revision, final=mode == 'automatic') if mode in ('rolling', 'automatic') else None
        if snapshot is None:
            snapshot = templates.begin_generation(session_id, revision, guidance=getattr(frozen, 'instructions', ''),
                provider=frozen.provider, model=frozen.model, destination=public['endpoint'], kind=mode)
        if (snapshot['provider'], snapshot['model'], snapshot['destination']) != (frozen.provider, frozen.model, public['endpoint']):
            raise ValueError('The active notes generation uses another destination; Generate notes can start a new one')
        if mode == 'automatic' and snapshot['source_revision'] != revision:
            templates.admit_final_revision(snapshot['id'], revision)
        return cls(store, frozen, snapshot, authorize)

    def authorized(self):
        self._authorize()
        return True

    def engine(self):
        from .rolling import RollingNotes
        return RollingNotes(self.store, self.provider, authorized=self.authorized, generation_id=self.snapshot['id'])

    def failed(self):
        # Best-effort: a workspace database failure here must not mask the original
        # notes error, skip the user-facing outcome, or interrupt cancellation.
        with contextlib.suppress(*DATABASE_ERRORS):
            NoteTemplateStore(self.store.conn).mark_generation(self.snapshot['id'], 'failed')
