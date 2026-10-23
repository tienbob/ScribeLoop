"""Explicit native keystore backends; no plugin selection or plaintext fallback."""
from __future__ import annotations

import base64
import secrets
import sys
from typing import Protocol

SERVICE = 'ai.scribeloop.storage.v1'


class KeyUnavailable(RuntimeError):
    pass


class KeyStore(Protocol):
    def get(self, account: str) -> bytes | None: ...
    def create(self, account: str) -> bytes: ...
    def delete(self, account: str) -> None: ...


class NativeKeyStore:
    """Use macOS Keychain or Linux Secret Service exclusively.

    Calls can prompt for unlock/access and block; use before starting capture.
    The OS may authorize the Python interpreter in this unsigned dev build.
    """

    def __init__(self):
        try:
            if sys.platform == 'darwin':
                from keyring.backends.macOS import Keyring
            elif sys.platform.startswith('linux'):
                from keyring.backends.SecretService import Keyring
            else:
                raise KeyUnavailable('No supported native keystore on this platform')
            self._backend = Keyring()
        except (ImportError, RuntimeError):
            raise KeyUnavailable('Native keystore unavailable; no fallback is permitted') from None

    def get(self, account: str) -> bytes | None:
        try:
            encoded = self._backend.get_password(SERVICE, account)
            if encoded is None:
                return None
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError('Invalid key size')
            return key
        except Exception:  # noqa: BLE001 - sanitize native provider errors
            raise KeyUnavailable('Native keystore read failed') from None

    def create(self, account: str) -> bytes:
        # Caller must hold the database's exclusive process lock. Session accounts
        # use random IDs; existing keys must never be silently overwritten.
        if self.get(account) is not None:
            raise KeyUnavailable('Key already exists; refusing to replace it')
        key = secrets.token_bytes(32)
        try:
            self._backend.set_password(SERVICE, account, base64.b64encode(key).decode('ascii'))
            if self.get(account) != key:
                raise KeyUnavailable('Native keystore did not retain the new key')
        except Exception:  # noqa: BLE001 - sanitize native provider errors
            raise KeyUnavailable('Native keystore write failed') from None
        return key

    def delete(self, account: str) -> None:
        if self.get(account) is None:
            raise KeyUnavailable('Key not found')
        try:
            self._backend.delete_password(SERVICE, account)
            if self.get(account) is not None:
                raise KeyUnavailable('Key still present')
        except Exception:  # noqa: BLE001 - sanitize native provider errors
            raise KeyUnavailable('Native keystore deletion failed') from None
