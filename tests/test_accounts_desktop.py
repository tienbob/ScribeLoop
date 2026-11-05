"""The real desktop protocol carries public account state, never credentials."""
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_fixture import FixtureKeys
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.meetings.service import DiscoveryService
from test_desktop import Vault, envelope, receive


def test_account_protocol_snapshot_receipts_and_deduplication(tmp_path,require_sqlcipher):
    class Account:
        config = SimpleNamespace(configured=True)
        error = ''
        calls = 0
        phase = 'signed_out'
        def public(self):
            return {'configured':True,'phase':self.phase,'error':self.error,'user':None}
        async def begin(self,publish):
            self.calls += 1
            self.phase = 'signing_in'
            return 'https://accounts.google.com/o/oauth2/v2/auth?state=synthetic-state'
        async def cancel(self): self.phase = 'signed_out'
        async def refresh(self): raise ValueError('private token must not appear')
        async def sign_out(self): self.phase = 'signed_out'
    account = Account()
    settings = DesktopSettings(Vault())
    contract = DesktopContract(settings,Path('/fake/helper'))
    app,token = create_app(database=tmp_path/'workspace.sqlcipher',storage='encrypted',
        key_store=FixtureKeys(),desktop=contract,account=account,summary_provider=settings.value.summary(),
        discovery_factory=lambda publish:DiscoveryService(publish))
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        assert receive(ws,'snapshot')['account']['phase']=='signed_out'
        command = envelope({'action':'account_sign_in'})
        ws.send_json(command)
        assert receive(ws,'account_authorize')['url'].startswith('https://accounts.google.com/')
        assert receive(ws,'account_state')['phase']=='signing_in'
        assert receive(ws,'receipt')['status']=='accepted'
        ws.send_json(command)
        assert receive(ws,'receipt')['status']=='accepted' and account.calls==1
        ws.send_json(envelope({'action':'account_cancel'}))
        assert receive(ws,'account_state')['phase']=='signed_out'
        receive(ws,'receipt')
        ws.send_json(envelope({'action':'account_refresh'}))
        state = receive(ws,'account_state')
        assert state['error'] and 'private' not in str(state)
        assert receive(ws,'error')['context']=='account'
        assert receive(ws,'receipt')['status']=='rejected'
