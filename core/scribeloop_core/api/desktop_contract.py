"""Bounded desktop reads and dispatch receipts over the existing core commands."""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from uuid import uuid4

from ..llm.rolling import RollingNotes

PROTOCOL = 2
ID = re.compile(r'^[0-9a-f]{32}$')


class DesktopContract:
    def __init__(self, settings, helper):
        self.settings, self.helper = settings, helper
        self.boot_id = uuid4().hex
        self.sequence = 0
        self.receipts = OrderedDict()

    def unwrap(self, envelope):
        if (not isinstance(envelope, dict) or set(envelope) != {'protocol','request_id','command'}
                or type(envelope['protocol']) is not int or envelope['protocol'] != PROTOCOL
                or not isinstance(envelope['request_id'], str) or not ID.fullmatch(envelope['request_id'])
                or not isinstance(envelope['command'], dict)):
            raise ValueError('Invalid desktop envelope')
        return envelope['request_id'], envelope['command']

    def receipt(self, request_id, status):
        value = {'type': 'receipt', 'request_id': request_id, 'status': status}
        self.receipts[request_id] = value
        while len(self.receipts) > 256:
            self.receipts.popitem(last=False)
        return value

    def event(self, event):
        # Heavy legacy mutation events become invalidations; clients fetch one page.
        if event.get('type') in {'people_changed','speakers_corrected','transcript_replaced'}:
            event = {'type':'invalidate', 'session_id':event.get('session_id')}
        self.sequence += 1
        return {**event, 'protocol': PROTOCOL, 'boot_id': self.boot_id, 'sequence': self.sequence}

    def history(self, store, cursor=None):
        if cursor is not None and (not isinstance(cursor, list) or len(cursor) != 2
                or type(cursor[0]) is not int or not isinstance(cursor[1], str) or not ID.fullmatch(cursor[1])):
            raise ValueError('Invalid history cursor')
        query = 'SELECT * FROM sessions '
        args = ()
        if cursor:
            query += 'WHERE (started_at < ? OR (started_at = ? AND id < ?)) '
            args = (cursor[0], cursor[0], cursor[1])
        rows = list(store.conn.execute(query+'ORDER BY started_at DESC, id DESC LIMIT 31', args))
        page = [store._session(row) for row in rows[:30]]
        return {'type':'history_page', 'sessions':page,
            'next_cursor':[page[-1]['started_at'],page[-1]['id']] if len(rows)>30 else None}

    def snapshot(self, app, summary_provider, active_id, summary_id):
        return {'type':'snapshot', 'agreement':app.state.store.agreement(),
            'settings':self.settings.value.public(), 'discovery':app.state.discovery.snapshot(),
            'active_id':active_id, 'summary_active_id':summary_id,
            'processing_id':getattr(app.state.recording,'reprocess_id',None),
            'summary_provider':summary_provider.public(),
            'transcription':app.state.recording.cloud.public() if app.state.recording else {},
            'storage':'encrypted', 'database':str(app.state.desktop_database),
            **{k:v for k,v in self.history(app.state.store).items() if k != 'type'}}

    def meeting(self, app, command, summary_provider):
        if set(command) - {'action','session_id','revision','offset','anchor'}:
            raise ValueError('Invalid meeting read')
        sid = command.get('session_id')
        if not isinstance(sid,str) or not ID.fullmatch(sid):
            raise ValueError('Invalid session')
        store = app.state.store
        session = store.get(sid)
        if not session:
            raise ValueError('Meeting no longer exists')
        revision = session['transcript_revision']
        requested = command.get('revision')
        if requested is not None and (type(requested) is not int or requested != revision):
            return {'type':'invalidate', 'session_id':sid}
        offset = command.get('offset', 0)
        if type(offset) is not int or offset < -1 or offset > 10_000_000:
            raise ValueError('Invalid page offset')
        count = store.conn.execute('SELECT COUNT(*) FROM segments WHERE session_id=? AND revision=?',
                                  (sid,revision)).fetchone()[0]
        anchor = command.get('anchor')
        if anchor is not None:
            if not isinstance(anchor,str) or len(anchor)>180:
                raise ValueError('Invalid citation')
            position = store.conn.execute('SELECT n FROM (SELECT id, ROW_NUMBER() OVER '
                '(ORDER BY start_ms,rowid)-1 AS n FROM segments WHERE session_id=? AND revision=?) WHERE id=?',
                (sid,revision,anchor)).fetchone()
            if not position:
                raise ValueError('Citation is from a different revision')
            offset = (position[0] // 100) * 100
        if offset == -1:
            offset = max(0, ((count-1)//100)*100)
        rows = store.segments(sid, revision=revision, limit=100, offset=offset) if revision else []
        roster=store.speaker_roster(sid,revision) if revision else []
        numbers={row['id']:row['person_number'] for row in roster}
        for row in rows:
            row['person_number']=numbers.get(row.get('display_speaker_id'))
        # Notes are bounded at their original provider limits; no full transcript.
        summary = store.grounded_summary(sid)
        return {'type':'meeting_page', 'session':session, 'revision':revision, 'offset':offset,
            'total':count, 'segments':rows, 'speakers':roster, 'notes':store.notes(sid)[:100000], 'summary':summary,
            'audio':app.state.audio.status(sid) if app.state.audio and session['source']=='meeting' else {},
            'rolling':RollingNotes(store,summary_provider).latest(sid),
            'budget':RollingNotes(store,summary_provider).public(sid)}

    async def read(self, app, command, summary_provider, active_id, summary_id):
        action = command.get('action')
        if action == 'desktop_snapshot' and set(command)=={'action'}:
            return self.snapshot(app, summary_provider, active_id, summary_id)
        if action == 'history_page' and not set(command)-{'action','cursor'}:
            return self.history(app.state.store,command.get('cursor'))
        if action == 'meeting_page':
            return self.meeting(app,command,summary_provider)
        if action in {'audio_info','audio_window'}:
            fields={'action','session_id'} if action=='audio_info' else {'action','session_id','stream','start_ms'}
            sid=command.get('session_id')
            recorder = app.state.recording
            rerun_id = getattr(recorder,'reprocess_id',None)
            if (set(command)!=fields or not isinstance(sid,str) or not ID.fullmatch(sid) or
                    not app.state.audio or active_id or (recorder and recorder.task and
                    not recorder.task.done() and not rerun_id)):
                raise ValueError('Saved audio is unavailable while recording or processing')
            session=app.state.store.get(sid)
            if not session or (session['status'] not in {'completed','failed','interrupted'}
                               and not (sid==rerun_id and session['status']=='processing')):
                raise ValueError('Select a saved meeting')
            if action=='audio_info':
                return {'type':'audio_info','session_id':sid,**await app.state.audio.describe(sid)}
            data=await app.state.audio.seek(sid,command['stream'],command['start_ms'])
            return {'type':'audio_window','session_id':sid,'stream':command['stream'],
                    'start_ms':command['start_ms'],'wav_base64':base64.b64encode(data).decode('ascii')}
        if action == 'request_status' and set(command)=={'action','request_id'}:
            return self.receipts.get(command['request_id'],
                {'type':'receipt','request_id':command['request_id'],'status':'unknown'})
        if action == 'people_page' and not set(command)-{'action','offset'}:
            offset=command.get('offset',0)
            if type(offset) is not int or not 0<=offset<=10_000_000:
                raise ValueError('Invalid people page')
            people=[dict(row) for row in app.state.store.conn.execute(
                'SELECT id,canonical_name,learn_enabled,created_at FROM speakers '
                'ORDER BY canonical_name,id LIMIT 201 OFFSET ?', (offset,))]
            return {'type':'people', 'people':people[:200],
                    'next_offset':offset+200 if len(people)>200 else None}
        return None

    @staticmethod
    def check_size(event):
        if len(json.dumps(event).encode()) > 16_000_000:
            raise ValueError('Desktop response exceeds bounds')
