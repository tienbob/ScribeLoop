"""Email snapshots: no real calls, mail clients, private workspace or providers."""
import asyncio
import io
import os
import sys
import threading
import time
import wave
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api import email_exports as module
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.email_exports import EmailExports
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.service import DiscoveryService
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.retained_audio import RetainedAudio
from scribeloop_core.storage.sessions import SessionStore
from test_local_transcription import Keys, Vault
from test_retained_audio import seed


@pytest.fixture()
def state(tmp_path):
    conn=migrate(connect(':memory:'));store=SessionStore(conn)
    retained=RetainedAudio(store,SessionAudioStore(tmp_path/'audio',Keys()))
    sid=seed(retained,old=False,metadata={'api_key':'synthetic-secret-never-share'})
    manager=EmailExports(tmp_path/'exports',converter='/missing/converter')
    command={'action':'prepare_email_export','session_id':sid,'revision':1,'kind':'summary'}
    yield manager,store,retained,sid,command
    conn.close()


def save_summary(store,sid):
    rows,fingerprint,_=store.summary_input(sid,1)
    item={'text':'A synthetic meeting was discussed.','segment_ids':[rows[0]['id']]}
    value={'overview':item,'key_points':[item],'decisions':[],'action_items':[],
           'open_questions':[],'uncertainties':[]}
    store.save_grounded_summary(sid,1,fingerprint,value,provider='test',model='synthetic')


@pytest.mark.asyncio
async def test_summary_uses_saved_notes_and_omits_transcript_metadata(state,monkeypatch):
    manager,store,retained,sid,command=state;save_summary(store,sid)
    import httpx
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:pytest.fail('Sharing must not call a provider'))
    result=await manager.prepare(store,retained,command)
    file=Path(result['attachment']['path']);content=file.read_text()
    assert 'A synthetic meeting was discussed.' in result['body']
    assert '[1]' not in content and 'Sources' not in content
    assert not result['include_sources']
    assert not any(b['style'] in {'source','references'} for b in result['body_blocks'])
    assert 'Keep this transcript' not in content
    assert 'synthetic-secret-never-share' not in result['body']+content
    assert sid not in content
    assert result['email_eligible'] and file.stat().st_mode&0o077==0
    assert manager.root.stat().st_mode&0o077==0
    assert store.grounded_summary(sid)['provider']=='test'


@pytest.mark.asyncio
async def test_transcript_contains_all_pages_and_no_summary(state):
    manager,store,retained,sid,command=state
    for i in range(150):store.append(sid,{'text':f'Line {i}','start_ms':1000+i*1000,'end_ms':1999+i*1000,'is_final':True})
    result=await manager.prepare(store,retained,{**command,'kind':'transcript'})
    content=Path(result['attachment']['path']).read_text()
    assert 'Line 0\n' in content and 'Line 149' in content
    assert 'Speaker unknown' in content and 'Meeting audio' in content
    assert '0:00' in content and 'synthetic-secret-never-share' not in content
    assert 'full transcript is attached' in result['body']


@pytest.mark.asyncio
async def test_audio_is_authenticated_mixed_playable_and_bounded(state,monkeypatch):
    manager,store,retained,sid,command=state
    result=await manager.prepare(store,retained,{**command,'kind':'audio'})
    file=Path(result['attachment']['path'])
    assert file.name=='Conversation.wav'
    expected=await retained.seek(sid,'conversation',0)
    assert file.read_bytes()==expected
    with wave.open(io.BytesIO(file.read_bytes())) as audio:
        assert audio.getnchannels()==1 and audio.getframerate()==16000
        pcm=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2')
        assert pcm.max()>0 and pcm.min()>=0
    assert not retained.busy
    monkeypatch.setattr(module,'EMAIL_LIMIT',10)
    oversized=await manager.prepare(store,retained,{**command,'kind':'audio'})
    assert not oversized['email_eligible'] and Path(oversized['attachment']['path']).is_file()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform!='darwin',reason='macOS built-in AAC encoder')
async def test_native_audio_compression_uses_playable_m4a(state):
    manager,store,retained,_sid,command=state
    manager.converter='/usr/bin/afconvert'
    result=await manager.prepare(store,retained,{**command,'kind':'audio'})
    file=Path(result['attachment']['path'])
    assert file.suffix=='.m4a' and b'ftyp' in file.read_bytes()[:32]
    assert not file.with_suffix('.wav').exists()
    assert file.stat().st_mode&0o077==0


@pytest.mark.asyncio
async def test_missing_stale_revision_and_expired_options_fail_without_exports(state):
    manager,store,retained,sid,command=state
    with pytest.raises(ValueError,match='up-to-date'):await manager.prepare(store,retained,command)
    save_summary(store,sid)
    with store.conn:store.conn.execute('UPDATE segments SET text=? WHERE session_id=?',('Changed words',sid))
    with pytest.raises(ValueError,match='up-to-date'):await manager.prepare(store,retained,command)
    with pytest.raises(ValueError):await manager.prepare(store,retained,{**command,'revision':99})
    store.mark_audio_expired(sid)
    with pytest.raises(ValueError):await manager.prepare(store,retained,{**command,'kind':'audio'})
    assert not manager.root.exists()


@pytest.mark.asyncio
async def test_corrupt_audio_leaves_no_plaintext_attachment(state):
    manager,store,retained,sid,command=state
    path=retained.audio.path(sid,'mic');raw=path.read_bytes();path.write_bytes(raw[:-4])
    with pytest.raises(ValueError):await manager.prepare(store,retained,{**command,'kind':'audio'})
    assert not retained.busy
    assert not list(manager.root.glob('*/Conversation*'))


@pytest.mark.asyncio
async def test_cancelled_audio_export_holds_reader_until_thread_finishes(state,monkeypatch):
    manager,store,retained,_sid,command=state
    started=threading.Event();release=threading.Event()
    def blocked(folder,*args):
        started.set()
        assert release.wait(3)
        file=folder/'Conversation.wav';file.write_bytes(b'synthetic');return file
    monkeypatch.setattr(manager,'_audio',blocked)
    task=asyncio.create_task(manager.prepare(store,retained,{**command,'kind':'audio'}))
    try:
        assert await asyncio.to_thread(started.wait,2)
        task.cancel();await asyncio.sleep(.01)
        assert retained.busy and not task.done()
    finally:release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not retained.busy and not list(manager.root.glob('*/Conversation*'))


@pytest.mark.asyncio
async def test_export_limits_and_cleanup_are_scoped(state,monkeypatch,tmp_path):
    manager,store,retained,_sid,command=state
    first=await manager.prepare(store,retained,{**command,'kind':'transcript'})
    file=Path(first['attachment']['path']);folder=file.parent
    outside=tmp_path/'keep.txt';outside.write_text('Keep')
    link=manager.root/('f'*32);link.symlink_to(tmp_path,target_is_directory=True)
    manager.discard(['f'*32]);assert outside.read_text()=='Keep' and link.is_symlink()
    with pytest.raises(ValueError):manager.discard(['../outside'])
    manager.cleanup();assert file.exists()
    os.utime(folder,(time.time()-module.TTL-1,time.time()-module.TTL-1))
    manager.cleanup();assert not folder.exists() and outside.exists()
    monkeypatch.setattr(module,'MAX_TEXT',10)
    with pytest.raises(ValueError):await manager.prepare(store,retained,{**command,'kind':'transcript'})


def test_desktop_email_protocol_is_explicit_and_reversible(tmp_path):
    settings=DesktopSettings(Vault());exports=EmailExports(tmp_path/'exports',converter='/missing')
    app,token=create_app(storage='encrypted',database=tmp_path/'email.sqlcipher',key_store=Keys(),
        desktop=DesktopContract(settings,Path('/fake/helper')),summary_provider=settings.value.summary(),
        discovery_factory=DiscoveryService,email_exports=exports)
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as ws:
        sid=client.portal.call(app.state.store.create_recording,'Synthetic email',{})['id']
        client.portal.call(app.state.store.append,sid,{'text':'Hello','start_ms':0,'end_ms':1000,'is_final':True})
        client.portal.call(app.state.store.transition,sid,'completed')
        ws.send_text(token)
        while ws.receive_json()['type']!='snapshot':pass
        def send(command):
            rid=uuid4().hex;ws.send_json({'protocol':2,'request_id':rid,'command':command});return rid
        rid=send({'action':'prepare_email_export','session_id':sid,'revision':1,'kind':'transcript'})
        result=ws.receive_json();assert result['type']=='email_export' and result['response_to']==rid
        assert ws.receive_json()['status']=='accepted'
        file=Path(result['attachment']['path']);assert file.is_file()
        rid=send({'action':'discard_email_exports','export_ids':[result['export_id']]})
        assert ws.receive_json()['status']=='accepted' and not file.exists()
        rid=send({'action':'prepare_email_export','session_id':sid,'revision':1,'kind':'summary'})
        assert ws.receive_json()['status']=='rejected'
        assert 'Could not prepare' in ws.receive_json()['message']


def test_disconnected_export_gets_terminal_receipt(tmp_path):
    started=threading.Event()
    class PendingExports:
        async def prepare(self,*args):
            started.set();await asyncio.Future()
    settings=DesktopSettings(Vault());contract=DesktopContract(settings,Path('/fake/helper'))
    app,token=create_app(storage='encrypted',database=tmp_path/'cancel.sqlcipher',key_store=Keys(),
        desktop=contract,summary_provider=settings.value.summary(),discovery_factory=DiscoveryService,email_exports=PendingExports())
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as observer:
        observer.send_text(token)
        while observer.receive_json()['type']!='snapshot':pass
        with client.websocket_connect('ws://127.0.0.1/ws') as source:
            source.send_text(token)
            while source.receive_json()['type']!='snapshot':pass
            rid=uuid4().hex
            source.send_json({'protocol':2,'request_id':rid,'command':{'action':'prepare_email_export',
                'session_id':'a'*32,'revision':0,'kind':'audio'}})
            assert started.wait(2)
        while True:
            event=observer.receive_json()
            if event.get('request_id')==rid:
                assert event['status']=='rejected';break
        assert contract.receipts[rid]['status']=='rejected'


def test_summary_layout_has_compact_grouped_citations_and_literal_blocks():
    from scribeloop_core.api.email_exports import blocks_text, summary_blocks
    rows=[{'id':'a','start_ms':0,'end_ms':1000,'speaker_label':'Quy'},
          {'id':'b','start_ms':1000,'end_ms':2000,'speaker_label':'Bob'}]
    item={'text':'Literal <img src="https://example.test"> tiếng Việt','segment_ids':['a','b','a']}
    saved={'summary':{'overview':item,'key_points':[item]}}
    blocks=summary_blocks(saved,rows,'Meeting title',include_sources=True)
    assert blocks[0]=={'style':'title','text':'Meeting title'}
    assert blocks[1]['text'].endswith('[1, 2]')
    assert [b['style'] for b in blocks]==['title','paragraph','heading','bullet','references','source','source']
    rendered=blocks_text(blocks)
    assert '[1][2]' not in rendered
    assert 'Quy\n[2]' in rendered  # Compact references, not a blank paragraph per citation.
    assert '<img src="https://example.test">' in rendered  # Literal content, not markup.


@pytest.mark.asyncio
async def test_export_speaker_names_match_current_revision_after_rerun(state):
    manager,store,retained,sid,command=state
    for revision,key in [(1,'a'),(2,'b')]:
        for n in range(2):
            store.append(sid,{'text':f'Speech {n}','start_ms':n*1000,'end_ms':n*1000+500,
                'is_final':True,'speaker_key':f'deepgram:{key*32}:mic:{n}'},stream='mic',revision=revision)
    result=await manager.prepare(store,retained,{**command,'kind':'transcript','revision':2})
    content=Path(result['attachment']['path']).read_text()
    assert 'Person 1' in content and 'Person 2' in content
    assert 'Speaker 3' not in content and 'Speaker 4' not in content
    identity=store.speaker_roster(sid,2)[0]['id']
    store.correct_speaker(sid,2,identity,label='Quy')
    result=await manager.prepare(store,retained,{**command,'kind':'transcript','revision':2})
    assert 'Quy' in Path(result['attachment']['path']).read_text()


@pytest.mark.asyncio
async def test_optional_summary_sources_are_explicit_and_do_not_change_saved_notes(state):
    manager,store,retained,sid,command=state;save_summary(store,sid)
    saved=store.grounded_summary(sid)
    plain=await manager.prepare(store,retained,command)
    cited=await manager.prepare(store,retained,{**command,'include_sources':True})
    assert '[1]' not in plain['body'] and 'Sources' not in plain['body']
    assert '[1]' in cited['body'] and 'Sources' in cited['body'] and cited['include_sources']
    assert any(b['style']=='source' for b in cited['body_blocks'])
    assert '[1]' in Path(cited['attachment']['path']).read_text()
    assert store.grounded_summary(sid)==saved
    # Switching back generates a clean payload, rather than reusing a cited one.
    again=await manager.prepare(store,retained,{**command,'include_sources':False})
    assert again['body']==plain['body'] and not again['include_sources']
    for invalid in ('true',1,None):
        with pytest.raises(ValueError):
            await manager.prepare(store,retained,{**command,'include_sources':invalid})


def test_hiding_generated_sources_preserves_literal_brackets_in_summary():
    from scribeloop_core.api.email_exports import blocks_text, summary_blocks
    item={'text':'Discuss [2026] budget','segment_ids':['a']}
    saved={'summary':{'overview':item}}
    rows=[{'id':'a','start_ms':0,'end_ms':1000,'speaker_label':'Quy'}]
    result=blocks_text(summary_blocks(saved,rows,'Meeting title'))
    assert '[2026]' in result and '[1]' not in result and 'Sources' not in result
