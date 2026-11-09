"""No devices/providers: exercise desktop routing and slow/partial settings saves."""
import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.service import DiscoveryService
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig
from test_local_transcription import Keys, Vault

pytestmark = pytest.mark.usefixtures('require_sqlcipher')


def send(ws,command):
    rid=uuid4().hex
    ws.send_json({'protocol':2,'request_id':rid,'command':command})
    return rid


def receipt(ws,rid):
    while True:
        value=ws.receive_json()
        if value.get('type')=='receipt' and value.get('request_id')==rid:return value


def authenticate(ws,token):
    ws.send_text(token)
    while ws.receive_json()['type']!='snapshot':pass


class SpyRecorder:
    active_id=None
    task=None
    def __init__(self):
        self.cloud=DeepgramConfig(); self.selected=None; self.calls=[]
    def select_model(self,model):self.selected=model
    def reprocess(self,sid,**kwargs):self.calls.append(('reprocess',self.selected))
    def resume(self,sid,**kwargs):self.calls.append(('resume',self.selected))
    async def close(self):pass


def test_native_saved_audio_payloads_never_default_local_work_to_cloud(tmp_path):
    settings=DesktopSettings(Vault())
    settings.save({'transcription_engine':'ollama','local_transcription_model':'gemma4:e2b'})
    recorder=SpyRecorder()
    app,token=create_app(database=tmp_path/'routing.sqlcipher',storage='encrypted',key_store=Keys(),
        desktop=DesktopContract(settings,Path('/fake/helper')),summary_provider=settings.value.summary(),
        discovery_factory=DiscoveryService,recording_factory=lambda *args:recorder)
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as ws:
        authenticate(ws,token)
        store=app.state.store
        sid=client.portal.call(store.create_recording,'Synthetic',{'provider':'local-ollama'})['id']
        client.portal.call(store.append,sid,{'text':'Test','start_ms':0,'end_ms':1000,'is_final':True})
        client.portal.call(store.transition,sid,'interrupted')
        client.portal.call(store.accept_agreement,store.AGREEMENT_VERSION)
        app.state.audio.require=lambda *_:None  # No audio is read or sent by the spy.
        assert receipt(ws,send(ws,{'action':'reprocess','session_id':sid}))['status']=='accepted'
        assert recorder.calls==[('reprocess','ollama')]
        # A stale explicit cloud choice is rejected against current local settings.
        assert receipt(ws,send(ws,{'action':'reprocess','session_id':sid,'model':'deepgram'}))['status']=='rejected'
        assert len(recorder.calls)==1
        settings.save({'transcription_engine':'deepgram'})
        assert receipt(ws,send(ws,{'action':'resume','session_id':sid}))['status']=='accepted'
        assert recorder.calls[-1]==('resume','ollama')  # Saved provider wins over current Preferences.
        assert receipt(ws,send(ws,{'action':'reprocess','session_id':sid,'model':'ollama'}))['status']=='rejected'
        assert receipt(ws,send(ws,{'action':'reprocess','session_id':sid,'model':'deepgram'}))['status']=='accepted'
        assert recorder.calls[-1]==('reprocess','deepgram')


class GateVault(Vault):
    def __init__(self):
        super().__init__(); self.block=False; self.started=threading.Event(); self.release=threading.Event()
    def set_password(self,*args):
        if self.block:
            self.started.set()
            if not self.release.wait(5):raise ValueError('Synthetic gate timed out')
        super().set_password(*args)


def test_keychain_save_keeps_loop_responsive_serialized_and_survives_disconnect(tmp_path):
    vault=GateVault();settings=DesktopSettings(vault)
    contract=DesktopContract(settings,Path('/fake/helper'))
    app,token=create_app(database=tmp_path/'save.sqlcipher',storage='encrypted',key_store=Keys(),
        desktop=contract,summary_provider=settings.value.summary(),discovery_factory=DiscoveryService)
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as observer:
        authenticate(observer,token)
        with client.websocket_connect('ws://127.0.0.1/ws') as saving:
            authenticate(saving,token);vault.block=True
            rid=send(saving,{'action':'save_settings','settings':{'duration_s':300}})
            try:
                assert vault.started.wait(2)
                # A different client can read while Keychain is held. No time-based
                # speed assertion: the gate is still closed when this responds.
                assert receipt(observer,send(observer,{'action':'history_page'}))['status']=='accepted'
                assert app.state.settings_saving and not vault.release.is_set()
                assert settings.value.duration_s==3600
                assert receipt(observer,send(observer,{'action':'save_settings','settings':{'duration_s':10800}}))['status']=='rejected'
                assert receipt(observer,send(observer,{'action':'set_detection','enabled':False}))['status']=='rejected'
            except BaseException:
                vault.release.set();raise
        # The reconnecting client may first observe dispatching. It must then
        # receive a terminal receipt without having to reconnect a second time.
        status_read=send(observer,{'action':'request_status','request_id':rid})
        assert receipt(observer,rid)['status']=='dispatching'
        assert receipt(observer,status_read)['status']=='accepted'
        # Closing the requesting window cannot abandon an accepted Keychain write.
        vault.release.set()
        async def settled():
            await asyncio.wait_for(asyncio.shield(app.state.settings_task),2)
        client.portal.call(settled)
        assert settings.value.duration_s==300 and not app.state.settings_saving
        assert receipt(observer,rid)['status']=='accepted'
        assert receipt(observer,send(observer,{'action':'request_status','request_id':rid}))['status']=='accepted'
        assert DesktopSettings(vault).value.duration_s==300


def test_failed_keychain_write_releases_gate_and_can_retry(tmp_path):
    class FailedVault(Vault):
        fail=True
        def set_password(self,*args):
            if self.fail:raise ValueError('Synthetic Keychain failure')
            super().set_password(*args)
    vault=FailedVault();settings=DesktopSettings(vault)
    app,token=create_app(database=tmp_path/'failed-save.sqlcipher',storage='encrypted',key_store=Keys(),
        desktop=DesktopContract(settings,Path('/fake/helper')),summary_provider=settings.value.summary(),discovery_factory=DiscoveryService)
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as ws:
        authenticate(ws,token)
        assert receipt(ws,send(ws,{'action':'save_settings','settings':{'duration_s':300}}))['status']=='rejected'
        assert not app.state.settings_saving and settings.value.duration_s==3600
        vault.fail=False
        assert receipt(ws,send(ws,{'action':'save_settings','settings':{'duration_s':300}}))['status']=='accepted'
        assert settings.value.duration_s==300


def test_failed_later_save_keeps_explicit_success_receipt_for_first_step(tmp_path):
    class FailedDetection(DiscoveryService):
        async def set_enabled(self,enabled):raise ValueError('Synthetic failure')
    settings=DesktopSettings(Vault());contract=DesktopContract(settings,Path('/fake/helper'))
    app,token=create_app(database=tmp_path/'partial.sqlcipher',storage='encrypted',key_store=Keys(),
        desktop=contract,summary_provider=settings.value.summary(),discovery_factory=FailedDetection)
    with TestClient(app) as client,client.websocket_connect('ws://127.0.0.1/ws') as ws:
        authenticate(ws,token)
        first=send(ws,{'action':'save_settings','settings':{'microphone_gain_db':3.0}})
        assert receipt(ws,first)['status']=='accepted'
        later=send(ws,{'action':'set_detection','enabled':False})
        assert receipt(ws,later)['status']=='rejected'
        assert contract.receipts[first]['status']=='accepted'
        assert DesktopSettings(settings.backend).value.microphone_gain_db==3.0
