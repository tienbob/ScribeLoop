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
            if sys.platform == 'darwin':
                from keyring.backends.macOS import api
                self._macos_api = api
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

    def _exists(self, account: str) -> bool:
        api = getattr(self, '_macos_api', None)
        if api is None:
            return self.get(account) is not None
        # No kSecReturnData: checking identity must not unlock/read the secret.
        query = api.create_query(kSecClass=api.k_('kSecClassGenericPassword'),
                                 kSecAttrService=SERVICE, kSecAttrAccount=account)
        status = api.SecItemCopyMatching(query, None)
        if status == api.error.item_not_found:
            return False
        api.Error.raise_for_status(status)
        return True

    def _delete(self, account: str, *, missing_ok: bool) -> None:
        try:
            if not self._exists(account):
                if missing_ok:
                    return
                raise KeyUnavailable('Key not found')
            self._backend.delete_password(SERVICE, account)
            if self._exists(account):
                raise KeyUnavailable('Key still present')
        except Exception:  # noqa: BLE001 - sanitize native provider errors
            raise KeyUnavailable('Native keystore deletion failed') from None

    def delete(self, account: str) -> None:
        self._delete(account, missing_ok=False)

    def delete_if_present(self, account: str) -> None:
        self._delete(account, missing_ok=True)
