"""Synthetic identities and isolated account DBs; never contact Google or charge."""
import base64
import copy
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from scribeloop_core.accounts.contracts import AccountConfig, verify_entitlement
from scribeloop_core.accounts.desktop import AccountVault, DesktopAccount, OAuthCallback
from scribeloop_core.accounts.server import create_account_app
from scribeloop_core.accounts.store import AccountStore


@pytest.fixture()
def account_setup(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    config = AccountConfig(api_url='https://accounts.example',entitlement_public_key=public)
    clock = [1_800_000_000]
    store = AccountStore(tmp_path/'accounts.sqlite',clock=lambda:clock[0])
    claims = {}
    def verify(token):
        if token not in claims:
            raise ValueError('private provider error and token')
        return claims[token]
    app = create_account_app(config,store,key,verifier=verify)
    with TestClient(app) as client:
        yield config,store,claims,client,clock,key
    store.conn.close()


def login(claims, client, *, subject='google-one', email='user@example.test'):
    nonce = client.post('/v1/auth/challenge',json={}).json()['nonce']
    token = 'synthetic-google-id-token-'+subject
    claims[token] = {'sub':subject,'email':email,'email_verified':True,'name':'Tester','nonce':nonce}
    response = client.post('/v1/auth/google',json={'id_token':token})
    assert response.status_code == 200
    return response.json(),token


def test_google_login_is_unpaid_and_signed_not_a_purchase(account_setup):
    config,store,claims,client,clock,_key = account_setup
    value,token = login(claims,client)
    payload = verify_entitlement(value['entitlement'],config,value['user']['id'],now=clock[0])
    assert not payload.local_owned and payload.cloud_until is None
    assert client.post('/v1/auth/google',json={'id_token':token}).status_code == 401
    raw = '\n'.join(store.conn.iterdump()).encode()
    assert value['access_token'].encode() not in raw
    assert value['refresh_token'].encode() not in raw
    assert token.encode() not in raw
    assert client.get('/v1/account',headers={'Authorization':'Bearer '+value['access_token']}).status_code == 200
    assert client.get('/v1/account').status_code == 401


def test_email_changes_keep_subject_ownership_but_same_email_does_not_merge_accounts(account_setup):
    _config,store,claims,client,_clock,_key = account_setup
    first,_ = login(claims,client)
    with store.conn:
        store.conn.execute('UPDATE entitlements SET local_owned=1 WHERE user_id=?',(first['user']['id'],))
    renamed,_ = login(claims,client,email='renamed@example.test')
    assert renamed['user']['id']==first['user']['id']
    assert renamed['entitlement']['payload']['local_owned']
    other,_ = login(claims,client,subject='google-two',email='renamed@example.test')
    assert other['user']['id']!=first['user']['id']
    assert not other['entitlement']['payload']['local_owned']


def test_refresh_is_rotating_and_logout_revokes_session(account_setup):
    _config,_store,claims,client,clock,_key = account_setup
    value,_ = login(claims,client)
    clock[0] += 901
    assert client.get('/v1/account',headers={'Authorization':'Bearer '+value['access_token']}).status_code==401
    refreshed = client.post('/v1/auth/refresh',json={'refresh_token':value['refresh_token']}).json()
    assert refreshed['refresh_token'] != value['refresh_token']
    assert client.post('/v1/auth/refresh',json={'refresh_token':value['refresh_token']}).status_code==401
    assert client.post('/v1/auth/logout',json={'refresh_token':refreshed['refresh_token']}).status_code==200
    assert client.get('/v1/account',headers={'Authorization':'Bearer '+refreshed['access_token']}).status_code==401


def test_expired_challenge_and_invalid_google_tokens_are_redacted(account_setup):
    _config,_store,claims,client,clock,_key = account_setup
    nonce = client.post('/v1/auth/challenge',json={}).json()['nonce']
    claims['synthetic-long-token'] = {'sub':'one','nonce':nonce,'email_verified':True,'email':'u@example.test'}
    clock[0] += 301
    response = client.post('/v1/auth/google',json={'id_token':'synthetic-long-token'})
    assert response.status_code==401
    response = client.post('/v1/auth/google',json={'id_token':'unknown-private-token'})
    assert response.status_code==401 and 'private' not in response.text
    assert 'unknown-private-token' not in response.text
    invalid = client.post('/v1/auth/google',json={'id_token':'short-secret'})
    assert invalid.status_code==422 and 'short-secret' not in invalid.text


def test_signature_user_issuer_and_clock_guards(account_setup):
    config,_store,claims,client,clock,_key = account_setup
    value,_ = login(claims,client)
    bad = copy.deepcopy(value['entitlement'])
    bad['payload']['local_owned'] = True
    with pytest.raises(ValueError): verify_entitlement(bad,config,value['user']['id'],now=clock[0])
    with pytest.raises(ValueError): verify_entitlement(value['entitlement'],config,'f'*32,now=clock[0])
    with pytest.raises(ValueError): verify_entitlement(value['entitlement'],config,value['user']['id'],now=clock[0]-100)
    wrong = config.model_copy(update={'api_url':'https://different.example'})
    with pytest.raises(ValueError): verify_entitlement(value['entitlement'],wrong,value['user']['id'],now=clock[0])


@pytest.mark.parametrize('url',['http://example.com','https://user:secret@example.com','https://example.com/path','https://example.com?token=secret'])
def test_account_api_must_be_an_https_origin(url):
    with pytest.raises(ValueError): AccountConfig(api_url=url)


@pytest.mark.asyncio
async def test_loopback_callback_checks_state_duplicates_and_pkce():
    callback = OAuthCallback()
    await callback.start()
    try:
        parsed = parse_qs(urlsplit(callback.authorization_url(AccountConfig().google_client_id,'nonce')).query)
        assert parsed['code_challenge_method']==['S256']
        assert parsed['scope']==['openid email profile'] and 'client_secret' not in parsed
        assert parsed['redirect_uri']==[callback.redirect_uri]
        async with httpx.AsyncClient(trust_env=False) as client:
            bad = await client.get(callback.redirect_uri,params={'code':'private','state':'wrong'})
            assert bad.status_code==400 and not callback.result.done()
            good = await client.get(callback.redirect_uri,params={'code':'private-code','state':callback.state})
            assert good.status_code==200 and 'private-code' not in good.text
            assert await callback.result == 'private-code'
            assert (await client.get(callback.redirect_uri,params={'code':'duplicate','state':callback.state})).status_code==400
    finally:
        await callback.close()


class Vault:
    def __init__(self): self.value = {}
    def read(self): return self.value
    def save(self,value): self.value = copy.deepcopy(value)


@pytest.mark.asyncio
async def test_desktop_refresh_cache_and_signed_offline_ownership(account_setup):
    config,store,claims,client,clock,_key = account_setup
    clock[0] = int(__import__('time').time())
    value,_ = login(claims,client)
    with store.conn:
        store.conn.execute('UPDATE entitlements SET local_owned=1 WHERE user_id=?',(value['user']['id'],))
    value = client.post('/v1/auth/refresh',json={'refresh_token':value['refresh_token']}).json()
    vault = Vault()
    desktop = DesktopAccount(config,vault=vault)
    await desktop._save(value)
    assert desktop.public()['local_owned'] and 'access_token' not in vault.value
    restored = DesktopAccount(config,vault=vault)
    assert restored.public()['phase']=='cached' and restored.public()['local_owned']
    async def unavailable(*args,**kwargs): raise OSError('private URL/token')
    restored._request = unavailable
    await restored.refresh()
    assert restored.public()['local_owned'] and 'private' not in restored.public()['error']
    await restored.sign_out()
    assert vault.value=={} and restored.public()['user'] is None


def test_keychain_credentials_are_namespaced_by_service_and_key(account_setup):
    config,*_ = account_setup
    assert AccountVault(config,backend=object()).account != AccountVault(
        config.model_copy(update={'api_url':'https://other.example'}),backend=object()).account


@pytest.mark.asyncio
async def test_desktop_full_mock_sign_in_and_no_tokens_in_events(account_setup):
    config,_store,claims,client,clock,_key = account_setup
    clock[0] = int(__import__('time').time())
    events = []
    async def publish(event): events.append(event)
    desktop = DesktopAccount(config,vault=Vault())
    nonce = client.post('/v1/auth/challenge',json={}).json()['nonce']
    token = 'synthetic-google-token-for-desktop'
    claims[token] = {'sub':'desktop','email':'test@example.test','name':'Tester','email_verified':True,'nonce':nonce}
    async def request(method,url,**kwargs):
        if url.endswith('/challenge'): return {'nonce':nonce}
        if url=='https://oauth2.googleapis.com/token':
            assert kwargs['data']['code_verifier']==desktop.callback.verifier
            return {'id_token':token}
        assert url==config.api_url+'/v1/auth/google'
        return client.post('/v1/auth/google',json=kwargs['json']).json()
    desktop._request = request
    url = await desktop.begin(publish)
    assert url.startswith('https://accounts.google.com/')
    callback = desktop.callback
    async with httpx.AsyncClient(trust_env=False) as transport:
        await transport.get(callback.redirect_uri,params={'state':callback.state,'code':'code-from-google'})
    await desktop.task
    assert desktop.public()['phase']=='signed_in' and not desktop.public()['local_owned']
    rendered = json.dumps(events)
    assert token not in rendered and 'refresh_token' not in rendered and 'access_token' not in rendered


@pytest.mark.asyncio
async def test_setup_required_does_not_launch_google_or_touch_keychain():
    desktop = DesktopAccount(AccountConfig())
    assert desktop.public()['phase']=='setup_required'
    with pytest.raises(ValueError): await desktop.begin(None)


@pytest.mark.asyncio
async def test_cancel_closes_callback_and_never_exchanges_code(account_setup):
    config,*_ = account_setup
    events = []
    async def publish(event): events.append(event)
    desktop = DesktopAccount(config,vault=Vault())
    requests = []
    async def request(method,url,**kwargs):
        requests.append(url)
        return {'nonce':'test-nonce-with-enough-entropy'}
    desktop._request = request
    await desktop.begin(publish)
    callback = desktop.callback
    await desktop.cancel()
    assert not callback.server.is_serving() and callback.result.cancelled()
    assert requests == [config.api_url+'/v1/auth/challenge']
    assert desktop.public()['phase']=='signed_out'


def test_expired_refresh_and_unverified_email_cannot_sign_in(account_setup):
    _config,_store,claims,client,clock,_key = account_setup
    value,_ = login(claims,client)
    clock[0] += 31*86400
    assert client.post('/v1/auth/refresh',json={'refresh_token':value['refresh_token']}).status_code==401
    nonce = client.post('/v1/auth/challenge',json={}).json()['nonce']
    claims['unverified-google-token'] = {'sub':'unverified','nonce':nonce,'email':'u@example.test','email_verified':False}
    assert client.post('/v1/auth/google',json={'id_token':'unverified-google-token'}).status_code==401


def account_proxy_app(app,*,enabled=True):
    import uvicorn
    config=uvicorn.Config(app,proxy_headers=enabled,forwarded_allow_ips='127.0.0.1',workers=1,
                          log_level='critical')
    config.load()
    return config.loaded_app


def test_proxy_rate_limits_isolate_clients_and_ignore_spoofed_chain(account_setup):
    _config,_store,_claims,client,_clock,_key=account_setup
    proxied=account_proxy_app(client.app)
    with TestClient(proxied,client=('127.0.0.1',32100)) as proxy:
        for _ in range(60):
            assert proxy.get('/v1/account',headers={'X-Forwarded-For':'198.51.100.1'}).status_code==401
        assert proxy.get('/v1/account',headers={'X-Forwarded-For':'198.51.100.1'}).status_code==429
        assert proxy.get('/v1/account',headers={'X-Forwarded-For':'198.51.100.2'}).status_code==401
        # Appending/changing a forged leftmost address cannot reset client A's bucket.
        assert proxy.get('/v1/account',headers={'X-Forwarded-For':'203.0.113.123, 198.51.100.1'}).status_code==429


@pytest.mark.parametrize('enabled,peer',[(False,'127.0.0.1'),(True,'198.51.100.99')])
def test_rate_limit_does_not_trust_headers_without_authorized_proxy(account_setup,enabled,peer):
    _config,_store,_claims,client,_clock,_key=account_setup
    app=account_proxy_app(client.app,enabled=enabled)
    with TestClient(app,client=(peer,32100)) as proxy:
        for n in range(60):
            assert proxy.get('/v1/account',headers={'X-Forwarded-For':f'203.0.113.{n+1}'}).status_code==401
        assert proxy.get('/v1/account',headers={'X-Forwarded-For':'203.0.113.200'}).status_code==429


def test_proxy_without_forwarded_header_is_bounded_and_window_resets(account_setup,monkeypatch):
    from scribeloop_core.accounts import server
    _config,_store,_claims,client,_clock,_key=account_setup
    now=[1000.]
    # Replace this module's clock reference without altering asyncio's own clock.
    from types import SimpleNamespace
    monkeypatch.setattr(server,'time',SimpleNamespace(monotonic=lambda:now[0]))
    with TestClient(account_proxy_app(client.app),client=('127.0.0.1',32100)) as proxy:
        for _ in range(60):assert proxy.get('/v1/account').status_code==401
        assert proxy.get('/v1/account').status_code==429
        now[0]+=60
        assert proxy.get('/v1/account').status_code==401


@pytest.mark.parametrize('trusted',[False,True])
def test_account_cli_proxy_trust_is_explicit_and_ignores_permissive_env(account_setup,tmp_path,monkeypatch,trusted):
    import sys

    import uvicorn
    from scribeloop_core.accounts import __main__ as cli
    config,_store,_claims,_client,_clock,key=account_setup
    config_path=tmp_path/'config.json';config_path.write_text(config.model_dump_json())
    key_path=tmp_path/'signing.key'
    key_path.write_bytes(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,
                                          serialization.NoEncryption()))
    key_path.chmod(0o600)
    argv=['accounts','--config',str(config_path),'--database',str(tmp_path/'server.sqlite'),
          '--signing-key',str(key_path)]
    if trusted:argv.append('--trust-loopback-proxy')
    monkeypatch.setattr(sys,'argv',argv)
    monkeypatch.setattr(cli.os,'umask',lambda value:0)
    # The CLI proxy arguments are independent of the optional Google verifier.
    monkeypatch.setattr(cli,'create_account_app',lambda *args,**kwargs:None)
    monkeypatch.setenv('FORWARDED_ALLOW_IPS','*');monkeypatch.setenv('WEB_CONCURRENCY','8')
    calls=[]
    monkeypatch.setattr(uvicorn,'run',lambda app,**kwargs:calls.append(kwargs))
    cli.main()
    assert len(calls)==1
    assert calls[0]['proxy_headers'] is trusted
    assert calls[0]['forwarded_allow_ips']=='127.0.0.1'
    assert calls[0]['workers']==1 and calls[0]['host']=='127.0.0.1'
