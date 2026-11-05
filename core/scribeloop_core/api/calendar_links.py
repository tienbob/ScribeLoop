"""User-selected calendar snapshots, local to the encrypted meeting workspace."""
import json
import logging
import time

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CalendarEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[0-9a-f]{64}$')
    title: str = Field(min_length=1,max_length=160)
    calendar_title: str = Field(min_length=1,max_length=100)
    start_ms: int = Field(ge=0,le=8_000_000_000_000)
    end_ms: int = Field(ge=0,le=8_000_000_000_000)
    timezone: str = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def valid(self):
        if self.end_ms < self.start_ms or any(ord(c)<32 for c in self.title+self.calendar_title+self.timezone):
            raise ValueError('Invalid calendar snapshot')
        return self


class CalendarLinkRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    action: str = Field(pattern=r'^link_calendar_event$')
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    event: CalendarEvent | None


def read_link(store,sid):
    row = store.conn.execute("SELECT payload_json FROM events WHERE session_id=? "
        "AND type='calendar_link' ORDER BY rowid DESC LIMIT 1",(sid,)).fetchone()
    if row is None:
        return None
    try:
        if not isinstance(row[0],str) or len(row[0])>8192:
            raise ValueError('Invalid stored calendar snapshot')
        payload=json.loads(row[0])
        if not isinstance(payload,dict) or set(payload)!={'event'}:
            raise ValueError('Invalid stored calendar snapshot')
        return CalendarEvent.model_validate(payload['event']).model_dump() if payload['event'] is not None else None
    except (ValueError,TypeError,RecursionError):
        logging.getLogger(__name__).warning('Ignoring invalid stored calendar link for session %s',sid)
        return None


def save_link(store,command):
    request = CalendarLinkRequest.model_validate(command)
    session = store.get(request.session_id)
    if not session or session['source']!='meeting' or session['status']=='deleting':
        raise ValueError('Choose an available conversation')
    event = request.event.model_dump() if request.event else None
    # First validate everything, then append atomically. This needs no schema
    # migration and survives transcript replacement without touching its sources.
    with store.conn:
        store.conn.execute('INSERT INTO events(session_id,ts,level,type,payload_json) '
            "VALUES (?,?,?,'calendar_link',?)",(request.session_id,int(time.time()*1000),'info',json.dumps({'event':event})))
    return {'type':'calendar_link','session_id':request.session_id,'event':event}
