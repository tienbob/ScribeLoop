"""Bounded desktop reads and dispatch receipts over the existing core commands."""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from uuid import uuid4

from ..llm.rolling import RollingNotes
from ..storage.search_work import SearchWork

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
            'account':app.state.account.public() if getattr(app.state,'account',None) else {'configured':False,'phase':'setup_required'},
            'active_id':active_id, 'summary_active_id':summary_id,
            'recording_paused':bool(getattr(app.state.recording,'paused',False)),
            'processing_id':(getattr(app.state.recording,'reprocess_id',None) or
                             getattr(app.state.recording,'processing_id',None)),
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
        original = store.cleanup_originals(sid,revision,limit=100,offset=offset) if revision else []
        for row,before in zip(rows,original):
            if all(row[k]==before[k] for k in ('start_ms','end_ms','stream')):
                row['cleanup_needs_review']=before.get('cleanup_needs_review',False)
                if row['text']!=before['text']:
                    row['cleanup_original_text']=before['text'][:1000]
                    row['cleanup_original_truncated']=len(before['text'])>1000
        roster=store.speaker_roster(sid,revision) if revision else []
        numbers={row['id']:row['person_number'] for row in roster}
        labels={row['id']:row['name'] for row in roster}
        for row in rows:
            row['person_number']=numbers.get(row.get('display_speaker_id'))
            if row.get('display_speaker_id') in labels:
                row['speaker_label']=labels[row['display_speaker_id']]
        # Notes are bounded at their original provider limits; no full transcript.
        summary = store.grounded_summary(sid)
        from .calendar_links import read_link
        return {'type':'meeting_page', 'session':session, 'revision':revision, 'offset':offset,
            'calendar_event':read_link(store,sid),
            'transcript_provider':store.transcript_provider(sid),
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
        if action == 'search_meetings':
            allowed={'action','query','limit','offset','meeting_id','person_id','date_from','date_to'}
            if set(command)-allowed:
                raise ValueError('Invalid search request')
            for field in ('meeting_id','person_id'):
                value=command.get(field)
                if value is not None and (not isinstance(value,str) or not ID.fullmatch(value)):
                    raise ValueError('Invalid search filter')
            for field in ('date_from','date_to'):
                value=command.get(field)
                if value is not None and (type(value) is not int or value<0):
                    raise ValueError('Invalid search date')
            recorder=app.state.recording
            recording_task=getattr(recorder,'task',None)
            if (not app.state.store.agreement()['accepted'] or active_id or summary_id or
                    (recording_task and not recording_task.done())):
                raise ValueError('Search is available after setup and while recording is idle')
            from ..storage.search import MeetingSearch
            result=await MeetingSearch(app.state.store,
                embedding_config=self.settings.value.embedding(),
                work=SearchWork(authorize=app.state.store.search_authorization())).query_async(command.get('query'),
                limit=command.get('limit',50),offset=command.get('offset',0),
                meeting_id=command.get('meeting_id'),person_id=command.get('person_id'),
                date_from=command.get('date_from'),date_to=command.get('date_to'))
            return {'type':'search_results',**result}
        if action == 'ask_evidence':
            allowed={'action','question','scope','current_id','selected_ids'}
            if set(command)-allowed:
                raise ValueError('Invalid Ask request')
            selected=command.get('selected_ids')
            if selected is not None and (not isinstance(selected,list) or
                    any(not isinstance(value,str) or not ID.fullmatch(value) for value in selected)):
                raise ValueError('Invalid Ask meetings')
            current=command.get('current_id')
            if current is not None and (not isinstance(current,str) or not ID.fullmatch(current)):
                raise ValueError('Invalid current meeting')
            recorder=app.state.recording
            recording_task=getattr(recorder,'task',None)
            if (not app.state.store.agreement()['accepted'] or active_id or summary_id or
                    (recording_task and not recording_task.done())):
                raise ValueError('Ask is available after setup and while recording is idle')
            from ..llm.meeting_ask import AskScope, MeetingEvidence
            self.settings.value.require_hybrid_for_ask()
            scope=AskScope.resolve(app.state.store,command.get('scope'),current_id=current,
                                   selected_ids=selected)
            result=await MeetingEvidence(app.state.store,
                embedding_config=self.settings.value.embedding(),
                work=SearchWork(authorize=app.state.store.search_authorization())).retrieve(command.get('question'),scope)
            return {'type':'ask_evidence',**result}
        if action == 'ask_history':
            allowed={'action','scope','current_id','selected_ids','limit'}
            if set(command)-allowed:
                raise ValueError('Invalid Ask history request')
            selected=command.get('selected_ids')
            if selected is not None and (not isinstance(selected,list) or
                    any(not isinstance(value,str) or not ID.fullmatch(value) for value in selected)):
                raise ValueError('Invalid Ask meetings')
            current=command.get('current_id')
            if current is not None and (not isinstance(current,str) or not ID.fullmatch(current)):
                raise ValueError('Invalid current meeting')
            if not app.state.store.agreement()['accepted']:
                raise ValueError('Ask is available after setup')
            from ..llm.meeting_ask import AskScope, MeetingAsk
            self.settings.value.require_hybrid_for_ask()
            scope=AskScope.resolve(app.state.store,command.get('scope'),current_id=current,
                                   selected_ids=selected)
            result=await MeetingAsk(app.state.store,summary_provider,
                embedding_config=self.settings.value.embedding(),
                work=SearchWork(authorize=app.state.store.search_authorization())).history(
                scope,limit=command.get('limit',50))
            return {'type':'ask_history','scope':{'kind':scope.kind,'meeting_ids':list(scope.meeting_ids)},
                    'answers':result}
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
