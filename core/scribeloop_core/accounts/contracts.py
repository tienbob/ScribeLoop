from __future__ import annotations

import base64
import json
import time
from typing import Literal
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GOOGLE_CLIENT_ID = '265252461155-rciggi2us36f3hi9rqiovppe7sa7g64m.apps.googleusercontent.com'


class AccountConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    google_client_id: str = GOOGLE_CLIENT_ID
    api_url: str = ''
    entitlement_public_key: str = ''

    @field_validator('google_client_id')
    @classmethod
    def client_id_valid(cls, value):
        if not 20 <= len(value) <= 256 or not value.endswith('.apps.googleusercontent.com') or any(c.isspace() for c in value):
            raise ValueError('Invalid Google desktop client ID')
        return value

    @field_validator('api_url')
    @classmethod
    def api_valid(cls, value):
        if value:
            parsed = urlsplit(value)
            if (len(value)>500 or parsed.scheme!='https' or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in ('','/') or parsed.port not in (None,443)):
                raise ValueError('Account service requires an HTTPS origin')
        return value.rstrip('/')

    @field_validator('entitlement_public_key')
    @classmethod
    def key_valid(cls, value):
        if value:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(value,validate=True))
        return value

    @model_validator(mode='after')
    def paired_config(self):
        if bool(self.api_url) != bool(self.entitlement_public_key):
            raise ValueError('Configure account API and entitlement public key together')
        return self

    @property
    def configured(self):
        return bool(self.api_url and self.entitlement_public_key)


class AccountUser(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    email: str = Field(max_length=320)
    name: str = Field(max_length=160)


class Entitlement(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1] = 1
    user_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    issuer: str = Field(max_length=500)
    issued_at: int = Field(ge=0)
    local_owned: bool = False
    cloud_until: int | None = Field(default=None,ge=0)


class SignedEntitlement(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    payload: Entitlement
    signature: str = Field(max_length=128)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def verify_entitlement(value, config, user_id, *, now=None):
    signed = SignedEntitlement.model_validate(value)
    payload = signed.payload
    now = int(time.time()) if now is None else now
    if payload.user_id != user_id or payload.issuer != config.api_url or payload.issued_at>now+60:
        raise ValueError('Entitlement belongs to another account, issuer, or clock')
    try:
        signature = base64.b64decode(signed.signature,validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(config.entitlement_public_key,validate=True)).verify(
            signature,canonical(payload.model_dump()))
    except (ValueError,InvalidSignature):
        raise ValueError('Invalid entitlement signature') from None
    return payload
