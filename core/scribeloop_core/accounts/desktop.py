"""User-initiated desktop OAuth, bounded loopback callback, Keychain sessions."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .contracts import AccountConfig, AccountUser, verify_entitlement

GOOGLE_AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN = 'https://oauth2.googleapis.com/token'


def load_config(path):
    path = Path(path)
    if not path.exists():
        return AccountConfig()
    if path.stat().st_size>2048:
        raise ValueError('Account configuration exceeds bounds')
    return AccountConfig.model_validate_json(path.read_text())


class AccountVault:
    """One credential record per account-service origin and verification key."""
    def __init__(self, config, backend=None):
        if backend is None:
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        self.backend = backend
        self.account = hashlib.sha256((config.api_url+'|'+config.entitlement_public_key).encode()).hexdigest()

    def read(self):
        raw = self.backend.get_password('ai.scribeloop.accounts.v1',self.account)
        if raw is None:
            return {}
        if len(raw)>16000:
            raise ValueError('Account credential exceeds bounds')
        return json.loads(raw)

    def save(self, value):
        self.backend.set_password('ai.scribeloop.accounts.v1',self.account,json.dumps(value))


class OAuthCallback:
    def __init__(self):
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(64)
        self.path = '/oauth/'+secrets.token_urlsafe(24)
        self.server = None
        self.result = None
        self.connections = 0

    async def start(self):
        self.result = asyncio.get_running_loop().create_future()
        self.server = await asyncio.start_server(self._receive,'127.0.0.1',0,limit=8192)
        port = self.server.sockets[0].getsockname()[1]
        self.redirect_uri = f'http://127.0.0.1:{port}{self.path}'

    def authorization_url(self, client_id, nonce):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        return GOOGLE_AUTH+'?'+urlencode({'client_id':client_id,'response_type':'code',
            'redirect_uri':self.redirect_uri,'scope':'openid email profile','state':self.state,
            'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256','prompt':'select_account'})

    async def _receive(self, reader, writer):
        if self.connections>=8:
            writer.close()
            return
        self.connections += 1
        status = '400 Bad Request'
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3)
            if len(header)>8192:
                raise ValueError('Oversized callback')
            method,target,version = header.split(b'\r\n',1)[0].decode('ascii').split(' ')
            parsed = urlsplit(target)
            query = parse_qs(parsed.query,keep_blank_values=True,strict_parsing=True,max_num_fields=12)
            if (method!='GET' or not version.startswith('HTTP/1.') or parsed.scheme or parsed.netloc
                    or parsed.path!=self.path or parsed.fragment or self.result.done()
                    or len(query.get('state',[])) != 1
                    or not secrets.compare_digest(query['state'][0].encode(),self.state.encode())):
                raise ValueError('Unexpected callback')
            if 'error' in query:
                self.result.set_exception(ValueError('Google sign-in was canceled'))
            elif len(query.get('code',[]))==1 and 1<=len(query['code'][0])<=4096:
                self.result.set_result(query['code'][0])
            else:
                raise ValueError('Missing authorization code')
            status = '200 OK'
        except (ValueError,UnicodeError,asyncio.TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            pass
        finally:
            # Never reflect codes, tokens, profile data or arbitrary error text.
            body = b'ScribeLoop sign-in response received. You may close this window.'
            writer.write((f'HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n'
                f'Content-Length: {len(body)}\r\nCache-Control: no-store\r\n'
                "Content-Security-Policy: default-src 'none'\r\nConnection: close\r\n\r\n").encode()+body)
            with contextlib.suppress(ConnectionError):
                await writer.drain()
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.connections -= 1

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.result and not self.result.done():
            self.result.cancel()


class DesktopAccount:
    def __init__(self, config, *, vault=None, client_factory=httpx.AsyncClient):
        self.config = config
        self.vault = (vault or AccountVault(config)) if config.configured else None
        self.client_factory = client_factory
        self.cached = {}
        self.access = ''
        self.expires = 0
        self.task = None
        self.callback = None
        self.phase = 'signed_out' if config.configured else 'setup_required'
        self.error = ''
        if self.vault:
            try:
                saved = self.vault.read()
                if saved:
                    self._validate(saved)
                    self.cached = saved
                    self.phase = 'cached'
            except Exception:  # noqa: BLE001 - preserve workspace even if account Keychain is locked
                self.error = 'Account credentials could not be read. Sign in again; saved meetings are unchanged.'

    def public(self):
        flags = None
        user = None
        if self.cached:
            try:
                user,flags = self._validate(self.cached)
            except (ValueError,KeyError,TypeError):
                self.cached,self.access = {},''
                self.phase = 'signed_out'
                self.error = 'Saved account state could not be verified. Sign in again; saved meetings remain available.'
        return {'configured':self.config.configured,'phase':self.phase,'error':self.error,
            'user':user.model_dump() if user else None,'local_owned':bool(flags and flags.local_owned),
            'cloud_until':flags.cloud_until if flags else None,
            'cloud_active':bool(flags and flags.cloud_until and flags.cloud_until>time.time()),
            'billing_available':False}

    def _validate(self, value):
        user = AccountUser.model_validate(value['user'])
        flags = verify_entitlement(value['entitlement'],self.config,user.id)
        refresh = value.get('refresh_token')
        if not isinstance(refresh,str) or not 32<=len(refresh)<=128:
            raise ValueError('Invalid account session')
        return user,flags

    async def _request(self, method, url, **kwargs):
        async with self.client_factory(timeout=15,follow_redirects=False,trust_env=False) as client, \
                client.stream(method,url,**kwargs) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body)>32768:
                    raise ValueError('Account response exceeds bounds')
            return json.loads(body)

    async def _save(self, value):
        self._validate(value)
        access,expires = value.get('access_token'),value.get('expires_at')
        if not isinstance(access,str) or not 32<=len(access)<=128 or type(expires) is not int:
            raise ValueError('Invalid account session')
        saved = {k:value[k] for k in ('refresh_token','user','entitlement')}
        # Cancellation must wait for a started write, so Sign out cannot race a
        # late Keychain write which would resurrect the previous credentials.
        write = asyncio.create_task(asyncio.to_thread(self.vault.save,saved))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise
        self.cached = saved
        self.access,self.expires = access,expires
        self.phase,self.error = 'signed_in',''

    async def begin(self, publish):
        if not self.config.configured:
            raise ValueError('Account service is not configured')
        if self.task and not self.task.done():
            raise ValueError('A sign-in is already pending')
        nonce = (await self._request('POST',self.config.api_url+'/v1/auth/challenge',json={}))['nonce']
        if not isinstance(nonce,str) or not 20<=len(nonce)<=128:
            raise ValueError('Invalid sign-in challenge')
        callback = OAuthCallback()
        await callback.start()
        self.callback = callback
        self.phase,self.error = 'signing_in',''
        self.task = asyncio.create_task(self._finish(callback,publish))
        return callback.authorization_url(self.config.google_client_id,nonce)

    async def _finish(self, callback, publish):
        try:
            code = await asyncio.wait_for(asyncio.shield(callback.result),180)
            tokens = await self._request('POST',GOOGLE_TOKEN,data={
                'client_id':self.config.google_client_id,'code':code,'code_verifier':callback.verifier,
                'redirect_uri':callback.redirect_uri,'grant_type':'authorization_code'})
            token = tokens.get('id_token')
            if not isinstance(token,str) or not 20<=len(token)<=12000:
                raise ValueError('Google did not return an identity token')
            value = await self._request('POST',self.config.api_url+'/v1/auth/google',json={'id_token':token})
            await self._save(value)
        except asyncio.CancelledError:
            self.phase = 'cached' if self.cached else 'signed_out'
            raise
        except Exception:  # noqa: BLE001 - no OAuth URLs/codes/provider bodies in UI
            self.phase = 'cached' if self.cached else 'signed_out'
            self.error = 'Sign-in did not finish. Check your browser or account-service connection and try again.'
        finally:
            await callback.close()
            if self.callback is callback:
                self.callback = None
            await publish({'type':'account_state',**self.public()})

    async def refresh(self):
        if self.task and not self.task.done():
            raise ValueError('Finish or cancel the current sign-in')
        if not self.cached:
            raise ValueError('Sign in first')
        try:
            value = await self._request('POST',self.config.api_url+'/v1/auth/refresh',
                json={'refresh_token':self.cached['refresh_token']})
            await self._save(value)
        except Exception:  # noqa: BLE001 - cached signed Local ownership remains usable offline
            self.phase = 'cached'
            self.error = 'Account refresh unavailable. Showing the last verified plan; no purchase was changed.'

    async def cancel(self):
        callback = self.callback
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        # A task canceled before its first instruction never runs its finally.
        if callback is not None and self.callback is callback:
            await callback.close()
            self.callback = None
            self.phase = 'cached' if self.cached else 'signed_out'

    async def sign_out(self):
        await self.cancel()
        # A failed remote revoke must not leave credentials on this Mac.
        saved = self.cached
        await asyncio.to_thread(self.vault.save,{})
        self.cached,self.access,self.expires = {},'',0
        self.phase,self.error = 'signed_out',''
        if saved:
            with contextlib.suppress(Exception):
                await self._request('POST',self.config.api_url+'/v1/auth/logout',
                    json={'refresh_token':saved['refresh_token']})
