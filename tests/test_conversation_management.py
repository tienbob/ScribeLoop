"""Manual names remain local metadata and survive future model-generated titles."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_desktop import Vault, envelope, receive


@pytest.fixture()
def saved():
    conn=migrate(connect(':memory:'))
    store=SessionStore(conn)
    sid=store.create_recording('Automatic topic',{'auto_title':True})['id']
    store.append(sid,{'stream':'mic','text':'The team discussed a prototype.',
                     'start_ms':0,'end_ms':100,'is_final':True})
    store.transition(sid,'completed')
    yield store,sid
    conn.close()


def test_manual_title_survives_later_summary_without_changing_transcript(saved):
    store,sid=saved
    before=store.segments(sid)
    renamed=store.rename_conversation(sid,'  Kế hoạch prototype  ')
    assert renamed['title']=='Kế hoạch prototype'
    rows,fingerprint,_=store.summary_input(sid,1)
    item={'text':'Generated topic','segment_ids':[rows[0]['id']]}
    summary={'title':item,'overview':item,'key_points':[],'decisions':[],
             'action_items':[],'open_questions':[],'uncertainties':[]}
    store.save_grounded_summary(sid,1,fingerprint,summary,provider='test',model='test')
    assert store.get(sid)['title']=='Kế hoạch prototype'
    assert store.segments(sid)==before


@pytest.mark.parametrize('title',['','  ','x'*161,'bad\nname','bad\tname','\ud800',False])
def test_invalid_rename_leaves_title_and_events_intact(saved,title):
    store,sid=saved
    with pytest.raises((ValueError,UnicodeError)):
        store.rename_conversation(sid,title)
    assert store.get(sid)['title']=='Automatic topic'
    assert not store.conn.execute("SELECT 1 FROM events WHERE type='conversation_renamed'").fetchone()


def test_active_or_deleted_conversation_cannot_be_renamed(saved):
    store,sid=saved
    with store.conn:
        store.conn.execute("UPDATE sessions SET status='processing' WHERE id=?",(sid,))
    with pytest.raises(ValueError):
        store.rename_conversation(sid,'New name')
    with pytest.raises(ValueError):
        store.rename_conversation('0'*32,'New name')


def test_desktop_rename_receipts_reconnect_and_no_automatic_provider_work():
    class Provider:
        def public(self):
            return {'configured':True,'provider':'Test','model':'Test','cloud':True,'endpoint':'https://test.invalid'}
        async def generate(self,messages):
            pytest.fail('Renaming a conversation dispatched a summary request')
    settings=DesktopSettings(Vault())
    desktop=DesktopContract(settings,Path('/unused'))
    app,token=create_app(desktop=desktop,summary_provider=Provider())
    with TestClient(app) as client:
        def seed():
            store=app.state.store
            sid=store.create_recording('Previous name',{'auto_title':True,'auto_workflow':True})['id']
            store.transition(sid,'completed')
            return sid
        sid=client.portal.call(seed)
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token);receive(ws,'snapshot')
            request=envelope({'action':'rename_conversation','session_id':sid,'title':'Custom topic'})
            ws.send_json(request)
            assert receive(ws,'conversation_renamed')['session']['title']=='Custom topic'
            assert receive(ws,'receipt')['status']=='accepted'
            ws.send_json(request)  # Same request ID is idempotent.
            assert receive(ws,'receipt')['status']=='accepted'
            ws.send_json(envelope({'action':'rename_conversation','session_id':sid,'title':'\n'}))
            assert receive(ws,'receipt')['status']=='rejected'
            assert receive(ws,'error')['context']=='desktop'
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            snapshot=receive(ws,'snapshot')
            assert next(row for row in snapshot['sessions'] if row['id']==sid)['title']=='Custom topic'
        assert client.portal.call(lambda:app.state.store.conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id=? AND type='conversation_renamed'",(sid,)).fetchone()[0])==1
        assert not client.portal.call(lambda:app.state.store.conn.execute(
            "SELECT 1 FROM events WHERE type='summary_request'").fetchone())
