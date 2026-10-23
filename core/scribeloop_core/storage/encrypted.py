"""SQLCipher connection lifecycle with OS-keystore keys and an exclusive app lock."""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

from .db import DATABASE_ERRORS, configure, migrate
from .keys import KeyStore, KeyUnavailable, NativeKeyStore


class EncryptedStorageError(RuntimeError):
    pass


def cipher_driver():
    try:
        from sqlcipher3 import dbapi2
        return dbapi2
    except (ImportError, OSError) as exc:
        raise EncryptedStorageError('SQLCipher binding unavailable; plaintext fallback is disabled') from exc


def connect_encrypted(path: Path, key: bytes):
    if not isinstance(key, bytes) or len(key) != 32:
        raise EncryptedStorageError('Database encryption requires a 32-byte key')
    driver = cipher_driver()
    conn = driver.connect(str(path))
    try:
        version = conn.execute('PRAGMA cipher_version').fetchone()
        if not version or int(version[0].split('.')[0]) < 4:
            raise EncryptedStorageError('SQLCipher 4 or newer is required')
        # PRAGMA does not accept bound parameters. Only a validated raw byte key
        # is hex-encoded here. Never enable SQL tracing or log this statement.
        conn.execute(f'''PRAGMA key = "x'{key.hex()}'"''')
        conn.execute('PRAGMA cipher_memory_security=ON')
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
        conn.row_factory = driver.Row
        configure(conn)
        return conn
    except Exception:  # noqa: BLE001 - always release connection on failed initialization
        conn.close()
        raise EncryptedStorageError('Encrypted database could not be opened') from None


def database_account(path: Path) -> str:
    return 'database:' + hashlib.sha256(os.fsencode(path.resolve())).hexdigest()


@contextmanager
def encrypted_database(path: Path, key_store: KeyStore | None = None):
    """Hold the process lock until after connection close. Never re-key an existing DB.

    Moving a DB changes its key account: relocation/export needs an explicit
    migration rather than silently generating a key for an unreadable file.
    """
    if os.name != 'posix':
        raise EncryptedStorageError('Encrypted storage currently requires macOS or Linux')
    import fcntl

    cipher_driver()  # Check dependencies before creating a key or database file.
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    conn = None
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EncryptedStorageError('Encrypted database is already in use') from None
        store = key_store if key_store is not None else NativeKeyStore()
        account = database_account(path)
        key = store.get(account)
        if key is None:
            if path.exists():
                raise KeyUnavailable('Existing database key is missing; refusing to replace it')
            key = store.create(account)
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
        conn = connect_encrypted(path, key)
        migrate(conn)
        yield conn
    except DATABASE_ERRORS:
        raise EncryptedStorageError('Encrypted database operation failed') from None
    finally:
        try:
            if conn is not None:
                conn.close()
        finally:
            os.close(lock_fd)
