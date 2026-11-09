"""Native Linux shell state; no GUI imports so the reducer stays testable."""
from __future__ import annotations

LIVE_STATES = {'loading','replaying','preparing','recording','stopping'}
LANGUAGE_NAMES = {
    'en':'English','vi':'Tiếng Việt','zh':'Chinese','es':'Spanish','fr':'French',
    'de':'German','hi':'Hindi','ru':'Russian','pt':'Portuguese','ja':'Japanese',
    'it':'Italian','nl':'Dutch',
}
SUMMARY_SECTIONS = (
    ('Overview','overview'),('Key points','key_points'),('Decisions','decisions'),
    ('Action items','action_items'),('Open questions','open_questions'),
    ('Uncertainties','uncertainties'),
)


def language_name(code):
    return LANGUAGE_NAMES.get(code,code or '')


def timestamp(ms):
    try:
        seconds=max(0,int(ms)//1000)
    except (TypeError,ValueError):
        return '0:00'
    return f'{seconds//60}:{seconds%60:02d}'


def session_label(session):
    if not session:
        return 'No conversation selected'
    status=str(session.get('status') or 'unknown').replace('_',' ').title()
    title=str(session.get('title') or 'Untitled conversation')
    return f'{title} · {status}'


def speaker_label(row,turns=(),default='Speaker unknown'):
    if row.get('speaker_label'):
        return str(row['speaker_label'])
    start=int(row.get('start_ms') or 0)
    end=int(row.get('end_ms') or 0)
    overlaps=[turn for turn in turns
              if int(turn.get('start_ms') or 0) < end and int(turn.get('end_ms') or 0) > start]
    labels={turn.get('label') for turn in overlaps if turn.get('label')}
    if len(labels)==1:
        return next(iter(labels))
    if overlaps:
        return 'Overlapping / uncertain speakers'
    return default


def summary_text(saved):
    if not saved:
        return ''
    summary=saved.get('summary') or {}
    lines=[]
    overview=summary.get('overview')
    if overview:
        lines.append('OVERVIEW')
        lines.append('• '+str(overview.get('text') or ''))
    for title,key in SUMMARY_SECTIONS[1:]:
        items=summary.get(key) or []
        if not items:
            continue
        lines.append(title.upper())
        for item in items:
            text=item.get('text') or item.get('task') or ''
            if item.get('owner'):
                text+=f" · Owner: {item['owner']}"
            if item.get('due'):
                text+=f" · Due: {item['due']}"
            lines.append('• '+str(text))
    if saved.get('stale'):
        lines.append('Saved summary is outdated. Generate again for this transcript.')
    return '\n'.join(lines).strip()


class LinuxState:
    def __init__(self):
        self.sessions={}
        self.segments={}
        self.notes={}
        self.saved_summaries={}
        self.rolling_drafts={}
        self.speaker_turns={}
        self.language_locks={}
        self.audio_states={}
        self.cloud_usage={}
        self.summary_budgets={}
        self.partials={}
        self.pages={}
        self.summary_active_id=None
        self.people=[]
        self.discovery={'status':'disabled','apps':[],'microphones':[],'candidates':[]}
        self.agreement={'accepted':False,'version':None}
        self.settings=None
        self.summary_provider={'configured':False}
        self.transcription={'cloud':False,'configured':True}
        self.active_id=None
        self.selected_id=None
        self.page_offset=0
        self.page_total=0
        self.request_id=0

    def ordered_sessions(self):
        return sorted(self.sessions.values(),
            key=lambda row:((row.get('started_at') or 0),row.get('id') or ''),reverse=True)

    def selected_session(self):
        return self.sessions.get(self.selected_id)

    def is_busy(self):
        return any(row.get('status') in LIVE_STATES for row in self.sessions.values())

    def select(self,session_id):
        self.selected_id=session_id
        self.page_offset=0
        self.page_total=len(self.segments.get(session_id,[]))

    def next_request_id(self):
        self.request_id+=1
        return self.request_id

    def record_payload(self,*,scope,microphone_id,title,duration_s,language,consent_confirmed):
        return {'action':'record','request_id':self.next_request_id(),'scope':scope,
            'app_id':None,'microphone_id':microphone_id,'candidate_id':None,
            'title':title or 'Meeting conversation','model':'deepgram',
            'duration_s':duration_s,'language':language or 'auto',
            'consent_confirmed':bool(consent_confirmed),'cloud_consent':bool(consent_confirmed)}

    def page_revision(self,session_id):
        revision=(self.pages.get(session_id) or {}).get('revision')
        if type(revision) is int and revision>=1:
            return revision
        rows=self.segments.get(session_id) or []
        value=rows[0].get('revision') if rows else None
        return value if type(value) is int and value>=1 else None

    def page_payload(self,session_id,offset=0):
        return {'action':'conversation_page','session_id':session_id,'offset':int(offset)}

    def apply(self,event):
        handler=getattr(self,'_apply_'+str(event.get('type') or ''),None)
        if handler is not None:
            handler(event)

    def _apply_snapshot(self,event):
        self.agreement=event.get('agreement') or self.agreement
        self.settings=event.get('settings')
        self.people=event.get('people') or []
        self.summary_budgets=event.get('summary_budgets') or {}
        self.audio_states=event.get('audio_states') or {}
        self.discovery=event.get('discovery') or self.discovery
        self.summary_provider=event.get('summary_provider') or self.summary_provider
        self.transcription=event.get('transcription') or self.transcription
        self.summary_active_id=event.get('summary_active_id')
        for row in event.get('sessions') or []:
            if row.get('id'):
                self.sessions[row['id']]=row
        self.active_id=event.get('active_id') or self.active_id
        if not self.selected_id:
            sessions=self.ordered_sessions()
            self.selected_id=self.active_id or (sessions[0].get('id') if sessions else None)
        self.select(self.selected_id)

    def _apply_conversation_page(self,event):
        sid=event.get('session_id')
        if not sid:
            return
        self.segments[sid]=event.get('segments') or []
        self.notes[sid]=event.get('notes') or ''
        self.saved_summaries[sid]=event.get('summary')
        if event.get('rolling'):
            self.rolling_drafts[sid]=event['rolling']
        else:
            self.rolling_drafts.pop(sid,None)
        if event.get('budget'):
            self.summary_budgets[sid]=event['budget']
        self.speaker_turns[sid]=event.get('speaker_turns') or []
        self.language_locks[sid]=event.get('language_lock')
        self.cloud_usage[sid]=event.get('cloud_usage') or {'providers':[],'requests':0,
            'estimated_usd':0,'billed_audio_s':0}
        if event.get('audio'):
            self.audio_states[sid]=event['audio']
        revision=event.get('revision')
        self.pages[sid]={'offset':int(event.get('offset') or 0),'total':int(event.get('total') or 0),
            'revision':revision if type(revision) is int and revision>=1 else None}
        if self.selected_id==sid:
            self.page_offset=self.pages[sid]['offset']
            self.page_total=self.pages[sid]['total']

    def _apply_session(self,event):
        session=event.get('session') or {}
        sid=session.get('id')
        if not sid:
            return
        self.sessions[sid]=session
        if session.get('status') in LIVE_STATES:
            self.active_id=sid
            if not self.selected_id:
                self.selected_id=sid
        elif self.active_id==sid:
            self.active_id=None


    def _apply_segment(self,event):
        sid=event.get('session_id');row=event.get('segment') or {}
        if not sid or not row.get('id'):
            return
        rows=self.segments.setdefault(sid,[])
        for index,existing in enumerate(rows):
            if existing.get('id')==row['id']:
                rows[index]=row
                return
        rows.append(row)

    def _apply_partial(self,event):
        sid=event.get('session_id');stream=event.get('stream') or 'loopback'
        text=(event.get('segment') or {}).get('text') or ''
        if not sid:
            return
        session=self.sessions.get(sid)
        if session and session.get('source')!='meeting':
            self.partials[sid]=text
            return
        self.partials.setdefault(sid,{})[stream]=text

    def _apply_transcript_replaced(self,event):
        sid=event.get('session_id')
        if not sid:
            return
        self.segments[sid]=event.get('segments') or []
        self.notes[sid]=event.get('notes') or self.notes.get(sid,'')
        self.partials.pop(sid,None)

    def _apply_speakers_corrected(self,event):
        sid=event.get('session_id')
        if not sid:
            return
        self.segments[sid]=event.get('segments') or []
        self.notes[sid]=event.get('notes') or self.notes.get(sid,'')
        summary=event.get('summary')
        if summary:
            self.saved_summaries[sid]=summary
        else:
            self.saved_summaries.pop(sid,None)

    def _apply_people_changed(self,event):
        self.people=event.get('people') or []
        for session in event.get('sessions') or []:
            if session.get('id'):
                self.sessions[session['id']]=session
        for sid,rows in (event.get('segments') or {}).items():
            self.segments[sid]=rows
        for sid,value in (event.get('notes') or {}).items():
            self.notes[sid]=value
        for sid,value in (event.get('grounded_summaries') or {}).items():
            if value:
                self.saved_summaries[sid]=value
            else:
                self.saved_summaries.pop(sid,None)

    def _apply_agreement(self,event):
        self.agreement=event.get('agreement') or self.agreement

    def _apply_settings(self,event):
        self.settings=event.get('settings') or self.settings
        self.summary_provider=event.get('summary_provider') or self.summary_provider
        self.transcription=event.get('transcription') or self.transcription

    def _apply_discovery(self,event):
        self.discovery=event.get('discovery') or self.discovery

    def _apply_audio_state(self,event):
        if event.get('session_id'):
            self.audio_states[event['session_id']]=event.get('audio') or {}

    def _apply_cloud_usage(self,event):
        if event.get('session_id'):
            self.cloud_usage[event['session_id']]=event

    def _apply_speaker_turns(self,event):
        if event.get('session_id'):
            self.speaker_turns[event['session_id']]=event.get('turns') or []

    def _apply_language_locked(self,event):
        if event.get('session_id'):
            self.language_locks[event['session_id']]=event

    def _apply_rolling_notes(self,event):
        sid=event.get('session_id')
        if sid:
            self.rolling_drafts[sid]={'summary':event.get('summary'),
                'provider':event.get('provider'),'model':event.get('model')}
            if event.get('budget'):
                self.summary_budgets[sid]=event['budget']

    def _apply_rolling_notes_error(self,event):
        pass

    def _apply_summary_started(self,event):
        self.summary_active_id=event.get('session_id')

    def _apply_summary_ready(self,event):
        sid=event.get('session_id')
        self.summary_active_id=None
        if not sid:
            return
        if event.get('summary'):
            self.saved_summaries[sid]=event['summary']
        if event.get('session'):
            self.sessions[sid]=event['session']
        if event.get('budget'):
            self.summary_budgets[sid]=event['budget']

    def _apply_summary_error(self,event):
        self.summary_active_id=None

    def _apply_session_deleted(self,event):
        sid=event.get('session_id')
        if not sid:
            return
        for store in (self.sessions,self.segments,self.notes,self.saved_summaries,self.rolling_drafts,
                      self.speaker_turns,self.language_locks,self.audio_states,self.cloud_usage,
                      self.summary_budgets,self.partials,self.pages):
            store.pop(sid,None)
        if self.active_id==sid:
            self.active_id=None
        if self.selected_id==sid:
            sessions=self.ordered_sessions()
            self.selected_id=self.active_id or (sessions[0].get('id') if sessions else None)

