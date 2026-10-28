"""Random audio keys wrapped by the already-unlocked workspace key.

Only encrypted envelopes reach disk. Legacy Keychain keys are imported on first
use, never replaced. Native originals remain until authorized recording deletion.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import tempfile
import threading
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .keys import KeyUnavailable


class WorkspaceAudioKeys:
    def __init__(self, root: Path, workspace_key: bytes, legacy):
        if not isinstance(workspace_key, bytes) or len(workspace_key) != 32:
            raise KeyUnavailable('Invalid workspace key')
        self.root = Path(root)
        self.legacy = legacy
        self._lock = threading.RLock()
        self._cipher = AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
            info=b'ai.scribeloop.workspace-audio-keys.v1').derive(workspace_key))
        self._directory()

    def _directory(self):
        if self.root.is_symlink():
            raise KeyUnavailable('Redirected workspace key directory')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise KeyUnavailable('Workspace key directory unavailable')

    def _path(self, account):
        if self._cipher is None:
            raise KeyUnavailable('Workspace is locked')
        if not isinstance(account, str) or not re.fullmatch(r'audio:[0-9a-f]{32}', account):
            raise KeyUnavailable('Invalid audio key identity')
        self._directory()
        return self.root / (account[6:] + '.key')

    def _read(self, path, account):
        # Broken symlinks and malformed envelopes must not fall back to Keychain.
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != 64:
                raise KeyUnavailable('Invalid wrapped audio key file')
            data = source.read(65)
        if len(data) != 64 or data[:4] != b'SLK1':
            raise KeyUnavailable('Invalid wrapped audio key')
        key = self._cipher.decrypt(data[4:16], data[16:], account.encode('ascii'))
        if len(key) != 32:
            raise KeyUnavailable('Invalid audio key size')
        return key

    def _sync(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write(self, path, account, key):
        nonce = secrets.token_bytes(12)
        data = b'SLK1' + nonce + self._cipher.encrypt(nonce, key, account.encode('ascii'))
        fd, temporary = tempfile.mkstemp(prefix='.key-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            # The workspace's exclusive database lock and this lock serialize writes.
            os.replace(temporary, path)
            self._sync()
            if self._read(path, account) != key:
                raise KeyUnavailable('Wrapped audio key verification failed')
        finally:
            Path(temporary).unlink(missing_ok=True)

    def get(self, account):
        with self._lock:
            try:
                path = self._path(account)
                key = self._read(path, account)
                if key is None:
                    key = self.legacy.get(account)
                    if key is not None:
                        if not isinstance(key, bytes) or len(key) != 32:
                            raise KeyUnavailable('Invalid legacy audio key')
                        self._write(path, account, key)
                return key
            except Exception:  # noqa: BLE001 - sanitize native and cryptographic failures
                raise KeyUnavailable('Workspace audio key unavailable') from None

    def create(self, account):
        with self._lock:
            try:
                path = self._path(account)
                exists = getattr(self.legacy, '_exists', None)
                legacy_exists = exists(account) if exists else self.legacy.get(account) is not None
                if path.exists() or path.is_symlink() or legacy_exists:
                    raise KeyUnavailable('Key already exists; refusing to replace it')
                key = secrets.token_bytes(32)
                self._write(path, account, key)
                return key
            except Exception:  # noqa: BLE001 - sanitize native and cryptographic failures
                raise KeyUnavailable('Workspace audio key creation failed') from None

    def delete_if_present(self, account):
        with self._lock:
            try:
                path = self._path(account)
                if path.is_symlink():
                    raise KeyUnavailable('Redirected audio key')
                # Remove any original first; failure keeps the wrapped key and
                # ciphertext intact for retry. Never import/unlock just to delete.
                delete = getattr(self.legacy, 'delete_if_present', None)
                if delete:
                    delete(account)
                elif self.legacy.get(account) is not None:
                    self.legacy.delete(account)
                path.unlink(missing_ok=True)
                self._sync()
            except Exception:  # noqa: BLE001 - sanitize native and cryptographic failures
                raise KeyUnavailable('Workspace audio key deletion failed') from None

    def delete(self, account):
        self.delete_if_present(account)

    def close(self):
        with self._lock:
            self._cipher = None
