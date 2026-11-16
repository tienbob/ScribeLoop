"""Control commands stay responsive while retrieval is slow, cancelled or failing."""
import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.search import MeetingSearch


class Vault:
    def get_password(self,*args): return None
    def set_password(self,*args): pass


def send(ws,action,**values):
    identity=uuid4().hex
    ws.send_json({'protocol':2,'request_id':identity,'command':{'action':action,**values}})
    return identity


def receive(ws,kind,identity=None):
    for _ in range(200):
        value=ws.receive_json()
        if value['type']==kind and (identity is None or value.get('response_to')==identity):
            return value
    raise AssertionError('Expected response missing')


@pytest.fixture
def desktop():
    settings=DesktopSettings(Vault())
    contract=DesktopContract(settings,Path('/fake/helper'))
    app,token=create_app(desktop=contract,summary_provider=settings.value.summary())
    with TestClient(app) as client:
        async def accept(): app.state.store.accept_agreement(app.state.store.AGREEMENT_VERSION)
        client.portal.call(accept)
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token); receive(ws,'snapshot')
            yield app,client,ws


@pytest.mark.parametrize('next_action',['desktop_snapshot','revoke_agreement','cancel_search','record'])
def test_control_commands_do_not_wait_for_provider(desktop,monkeypatch,next_action):
    app,client,ws=desktop
    started=threading.Event();cancelled=threading.Event()
    async def slow(*args,**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    monkeypatch.setattr(MeetingSearch,'query_async',slow)
    identity=send(ws,'search_meetings',query='architecture')
    assert started.wait(2)
    next_id=send(ws,next_action)
    if next_action=='desktop_snapshot':
        receive(ws,'snapshot',next_id)
        assert not cancelled.is_set()
        send(ws,'cancel_search'); receive(ws,'search_cancelled',identity)
    elif next_action=='revoke_agreement':
        receive(ws,'agreement')
        assert not client.portal.call(app.state.store.agreement)['accepted']
    elif next_action=='record':
        # Invalid recording still reaches validation promptly, without capturing hardware.
        receive(ws,'error')
    else:
        receive(ws,'search_cancelled',identity)
    assert cancelled.wait(2)


def test_superseded_search_never_delivers_late_results(desktop,monkeypatch):
    app,client,ws=desktop;started=threading.Event()
    async def query(self,text,**kwargs):
        if text=='old':started.set();await asyncio.Event().wait()
        return {'query':text,'results':[],'index':{},'usage':{}}
    monkeypatch.setattr(MeetingSearch,'query_async',query)
    old=send(ws,'search_meetings',query='old');assert started.wait(2)
    new=send(ws,'search_meetings',query='latest')
    events=[]
    while True:
        event=ws.receive_json();events.append(event)
        if event['type']=='search_results':break
    assert event['response_to']==new and event['query']=='latest'
    assert not any(e['type']=='search_results' and e.get('response_to')==old for e in events)
    assert client.portal.call(lambda:len(app.state.search_tasks))<=1


@pytest.mark.parametrize('mode',['timeout','timeout_ignored','failure'])
def test_search_errors_are_terminal_and_safe(desktop,monkeypatch,mode):
    _app,_client,ws=desktop
    from scribeloop_core.api import search_jobs
    monkeypatch.setattr(search_jobs,'SEARCH_DEADLINE_S',.02)
    async def query(*args,**kwargs):
        if mode=='timeout':await asyncio.Event().wait()
        if mode=='timeout_ignored':
            try:await asyncio.sleep(.04)
            except asyncio.CancelledError:await asyncio.sleep(.03)
            return {'results':[],'index':{},'usage':{}}
        raise RuntimeError('private-key and private transcript')
    monkeypatch.setattr(MeetingSearch,'query_async',query)
    identity=send(ws,'search_meetings',query='architecture')
    error=receive(ws,'search_error',identity)
    assert 'private' not in error['message']
    if mode.startswith('timeout'):assert 'timed out' in error['message']
    next_id=send(ws,'desktop_snapshot');receive(ws,'snapshot',next_id)


def test_disconnect_cancels_retrieval(desktop,monkeypatch):
    _app,_client,ws=desktop
    started=threading.Event();cancelled=threading.Event()
    async def query(*args,**kwargs):
        started.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    monkeypatch.setattr(MeetingSearch,'query_async',query)
    send(ws,'search_meetings',query='architecture');assert started.wait(2)
    ws.close()
    assert cancelled.wait(2)


def test_immediate_cancellation_reconciles_the_receipt(desktop,monkeypatch):
    _app,_client,ws=desktop
    async def query(*args,**kwargs):await asyncio.Event().wait()
    monkeypatch.setattr(MeetingSearch,'query_async',query)
    identity=send(ws,'search_meetings',query='architecture')
    send(ws,'cancel_search')
    receive(ws,'search_cancelled',identity)
    send(ws,'request_status',request_id=identity)
    for _ in range(100):
        event=ws.receive_json()
        if event['type']=='receipt' and event['request_id']==identity:
            assert event['status']=='cancelled'
            break
    else:raise AssertionError('Cancelled search receipt missing')


def test_overlong_query_explains_limit_and_shortened_query_recovers(desktop):
    _app,_client,ws=desktop
    identity=send(ws,'search_meetings',query='x'*201)
    error=receive(ws,'search_error',identity)
    assert error['message']=='Search supports up to 200 characters. Shorten your query; your filters are kept.'
    identity=send(ws,'search_meetings',query='corrected')
    assert receive(ws,'search_results',identity)['query']=='corrected'
