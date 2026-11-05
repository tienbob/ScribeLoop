"""Calendar snapshots stay local, reversible and independent of transcript revisions."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.calendar_links import read_link, save_link
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.service import DiscoveryService
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_local_transcription import Keys, Vault

EVENT = {'id':'a'*64,'title':'Private calendar event','calendar_title':'Work',
         'start_ms':1793667600000,'end_ms':1793671200000,'timezone':'Asia/Ho_Chi_Minh'}


def test_snapshot_link_replace_unlink_and_reopen(tmp_path):
    path = tmp_path/'test.db'
    conn = migrate(connect(path)); store = SessionStore(conn)
    sid = store.create_recording('Conversation',{})['id']
    store.append(sid,{'text':'Hello','start_ms':0,'end_ms':1000,'is_final':True})
    store.transition(sid,'completed')
    assert read_link(store,sid) is None
    command = {'action':'link_calendar_event','session_id':sid,'event':EVENT}
    save_link(store,command)
    rows,_fingerprint,messages = store.summary_input(sid,1)
    assert EVENT['title'] not in json.dumps(messages)
    assert store.get(sid)['title']=='Conversation'
    conn.close(); conn=migrate(connect(path)); store=SessionStore(conn)
    assert read_link(store,sid)==EVENT
    replacement = {**EVENT,'id':'b'*64,'title':'Another occurrence'}
    save_link(store,{**command,'event':replacement})
    assert read_link(store,sid)==replacement
    for invalid in ({**EVENT,'end_ms':0},{**EVENT,'notes':'unwanted private data'},
                    {**EVENT,'title':'bad\nname'},{**EVENT,'id':'not-an-id'}):
        with pytest.raises(ValueError):save_link(store,{**command,'event':invalid})
        assert read_link(store,sid)==replacement
    save_link(store,{**command,'event':None})
    assert read_link(store,sid) is None
    assert store.segments(sid)==rows
    with pytest.raises(ValueError):save_link(store,{**command,'session_id':'f'*32})
    conn.close()


def test_desktop_calendar_link_receipt_page_and_rejection(tmp_path):
    settings=DesktopSettings(Vault()); contract=DesktopContract(settings,Path('/fake/helper'))
    app,token=create_app(storage='encrypted',database=tmp_path/'calendar.sqlcipher',key_store=Keys(),
        desktop=contract,summary_provider=settings.value.summary(),discovery_factory=lambda p:DiscoveryService(p))
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as ws:
        sid=client.portal.call(app.state.store.create_recording,'Test',{})['id']
        ws.send_text(token)
        while ws.receive_json()['type']!='snapshot':pass
        def send(command):
            rid=uuid4().hex; ws.send_json({'protocol':2,'request_id':rid,'command':command});return rid
        rid=send({'action':'link_calendar_event','session_id':sid,'event':EVENT})
        event=ws.receive_json(); assert event['type']=='calendar_link' and event['event']==EVENT
        assert ws.receive_json()['status']=='accepted'
        send({'action':'meeting_page','session_id':sid,'offset':-1})
        assert ws.receive_json()['calendar_event']==EVENT
        ws.receive_json()  # Read receipt.
        rid=send({'action':'link_calendar_event','session_id':sid,'event':{**EVENT,'url':'https://example.com'}})
        rejected=ws.receive_json(); assert rejected['request_id']==rid and rejected['status']=='rejected'
        assert ws.receive_json()['message'].startswith('Calendar link was not saved')
        assert client.portal.call(read_link,app.state.store,sid)==EVENT


@pytest.mark.parametrize('raw',['not json','[]','{}','{"event":null,"extra":1}',
    '{"event":{"title":"invalid"}}',json.dumps({'event':{**EVENT,'notes':'private'}})])
def test_invalid_stored_calendar_snapshot_does_not_break_meeting(tmp_path,raw,caplog):
    conn=migrate(connect(tmp_path/'test.db'));store=SessionStore(conn)
    sid=store.create_recording('Conversation',{})['id']
    with conn:
        conn.execute("INSERT INTO events(session_id,type,payload_json) VALUES (?,'calendar_link',?)",(sid,raw))
    assert read_link(store,sid) is None
    assert store.get(sid)['title']=='Conversation'
    assert raw not in caplog.text
    assert 'Ignoring invalid stored calendar link' in caplog.text
    conn.close()
