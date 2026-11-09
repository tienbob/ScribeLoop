"""Separate deployable account API. No audio, transcript, or payment-card routes."""
from __future__ import annotations

import asyncio
import base64
import time
from collections import OrderedDict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import AccountConfig, Entitlement, canonical


class GoogleVerifier:
    def __init__(self, client_id):
        # Optional server dependency only; never part of the desktop runtime.
        import requests
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.id_token import verify_oauth2_token

        session = requests.Session()
        session.trust_env = False
        transport = GoogleRequest(session=session)
        self.verify = verify_oauth2_token
        self.errors = (GoogleAuthError,ValueError)
        self.request = lambda *a,**kw: transport(*a,**{**kw,'timeout':10})
        self.client_id = client_id

    def __call__(self, token):
        try:
            return self.verify(token,self.request,audience=self.client_id,clock_skew_in_seconds=30)
        except self.errors:
            raise ValueError('Google identity could not be verified') from None


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    id_token: str = Field(min_length=20,max_length=12000)


class Refresh(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    refresh_token: str = Field(min_length=32,max_length=128)


def create_account_app(config: AccountConfig, store, signing_key, *, verifier=None):
    if not config.configured:
        raise ValueError('Configure account origin and signing public key')
    from cryptography.hazmat.primitives import serialization
    public = base64.b64encode(signing_key.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    if public != config.entitlement_public_key:
        raise ValueError('Signing key does not match desktop configuration')
    verifier = verifier or GoogleVerifier(config.google_client_id)
    app = FastAPI(title='ScribeLoop accounts',docs_url=None,redoc_url=None,openapi_url=None)
    rates = OrderedDict()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # FastAPI's default error detail can echo invalid token input.
        return JSONResponse({'detail':'Invalid account request'},status_code=422)

    @app.middleware('http')
    async def bounds(request, call_next):
        # Also enforce body/rate limits at the deployment reverse proxy.
        if request.method == 'POST':
            length = request.headers.get('content-length','')
            if not length.isdigit() or int(length)>16000:
                return JSONResponse({'detail':'Request exceeds bounds'},status_code=413)
        peer = request.client.host if request.client else 'unknown'
        now = time.monotonic()
        since,count = rates.get(peer,(now,0))
        if now-since>=60:
            since,count = now,0
        rates[peer] = (since,count+1)
        rates.move_to_end(peer)
        if len(rates)>4096:
            rates.popitem(last=False)
        if count>=60:
            return JSONResponse({'detail':'Try again later'},status_code=429)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def snapshot(uid):
        user,flags = store.account(uid)
        payload = Entitlement(user_id=uid,issuer=config.api_url,issued_at=int(store.clock()),
                              local_owned=bool(flags['local_owned']),cloud_until=flags['cloud_until']).model_dump()
        return {'user':user.model_dump(),'entitlement':{
            'payload':payload,'signature':base64.b64encode(signing_key.sign(canonical(payload))).decode()}}

    @app.post('/v1/auth/challenge')
    async def challenge():
        try:
            return {'nonce':store.challenge()}
        except ValueError:
            raise HTTPException(429,'Try again later') from None

    @app.post('/v1/auth/google')
    async def google(body: Login):
        try:
            claims = await asyncio.to_thread(verifier,body.id_token)
            session = store.login(claims)
            return {**session,**snapshot(session['user_id'])}
        except Exception:  # noqa: BLE001 - never reflect provider/token content
            raise HTTPException(401,'Google sign-in could not be verified') from None

    @app.post('/v1/auth/refresh')
    async def refresh(body: Refresh):
        try:
            session = store.rotate(body.refresh_token)
            return {**session,**snapshot(session['user_id'])}
        except ValueError:
            raise HTTPException(401,'Sign in again') from None

    @app.post('/v1/auth/logout')
    async def logout(body: Refresh):
        store.logout(body.refresh_token)
        return {'ok':True}

    @app.get('/v1/account')
    async def account(request: Request):
        auth = request.headers.get('authorization','')
        try:
            if not auth.startswith('Bearer ') or not 32<=len(auth[7:])<=128:
                raise ValueError('Invalid bearer')
            return snapshot(store.authenticate(auth[7:]))
        except ValueError:
            raise HTTPException(401,'Sign in again') from None

    return app
