from pathlib import Path
from types import SimpleNamespace

import pytest
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.llm.meeting_ask import MAX_EVIDENCE_BYTES, AskScope, MeetingEvidence
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore


class Vault:
    value=None
    def get_password(self,_service,_account):
        return self.value
    def set_password(self,_service,_account,value):
        self.value=value


def meeting(store,title,text):
    row=store.create_recording(title,{})
    store.append(row['id'],{'text':text,'start_ms':1000,'end_ms':2000,'is_final':True,
                 'speaker_key':f"deepgram:{row['id']}:mic:1"},stream='mic')
    store.conn.execute('UPDATE sessions SET status=? WHERE id=?',('completed',row['id']))
    store.conn.commit()
    return row['id']


@pytest.mark.asyncio
async def test_ask_evidence_enforces_scope_and_returns_stable_current_sources():
    store=SessionStore(migrate(connect(':memory:')))
    allowed=meeting(store,'Pricing','Enterprise pricing was approved at forty dollars')
    outside=meeting(store,'Secret plan','Enterprise pricing uses a confidential discount')
    scope=AskScope.resolve(store,'current',current_id=allowed)
    packet=await MeetingEvidence(store).retrieve('enterprise pricing',scope)
    assert packet['sufficient'] and packet['scope']['meeting_ids']==[allowed]
    assert {item['meeting_id'] for item in packet['passages']}=={allowed}
    assert all(item['citation_id'].startswith(allowed+':r1:') for item in packet['passages'])
    assert outside not in repr(packet)


@pytest.mark.asyncio
async def test_ask_evidence_bounds_bytes_passages_and_reports_insufficient():
    store=SessionStore(migrate(connect(':memory:')))
    ids=[meeting(store,f'Planning {index}','roadmap '+('x'*4000)) for index in range(25)]
    scope=AskScope.resolve(store,'selected',selected_ids=ids[:20])
    packet=await MeetingEvidence(store).retrieve('roadmap',scope)
    assert packet['passage_count']<=20
    assert packet['evidence_bytes']<=MAX_EVIDENCE_BYTES
    assert packet['truncated']
    empty=await MeetingEvidence(store).retrieve('nonexistent-term',scope)
    assert not empty['sufficient'] and empty['passages']==[]


def test_ask_scope_rejects_empty_missing_and_implicit_widening():
    store=SessionStore(migrate(connect(':memory:')))
    sid=meeting(store,'One','alpha')
    with pytest.raises(ValueError,match='Choose meetings'):
        AskScope.resolve(store,'selected')
    with pytest.raises(ValueError,match='must contain meetings'):
        AskScope.resolve(store,'selected',selected_ids=[])
    with pytest.raises(ValueError,match='unavailable'):
        AskScope.resolve(store,'current',current_id='f'*32)
    assert AskScope.resolve(store,'all').meeting_ids==(sid,)


@pytest.mark.asyncio
async def test_desktop_ask_evidence_requires_agreement_idle_state_and_explicit_scope():
    store=SessionStore(migrate(connect(':memory:')))
    sid=meeting(store,'Decision','The team approved the launch date')
    desktop=DesktopContract(DesktopSettings(Vault()),Path('/fake/helper'))
    app=SimpleNamespace(state=SimpleNamespace(store=store,recording=None))
    command={'action':'ask_evidence','question':'What launch date was approved?',
             'scope':'current','current_id':sid}
    with pytest.raises(ValueError,match='after setup'):
        await desktop.read(app,command,desktop.settings.value.summary(),None,None)
    store.accept_agreement(store.AGREEMENT_VERSION)
    app.state.recording=SimpleNamespace(task=SimpleNamespace(done=lambda:False))
    with pytest.raises(ValueError,match='recording is idle'):
        await desktop.read(app,command,desktop.settings.value.summary(),None,None)
    app.state.recording=None
    result=await desktop.read(app,command,desktop.settings.value.summary(),None,None)
    assert result['type']=='ask_evidence' and result['sufficient']
    assert result['scope']=={'kind':'current','meeting_ids':[sid]}
