"""Real SQLCipher tests; native-library optional in the portable CI matrix."""
from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from scribeloop_core.storage.encrypted import (
    EncryptedStorageError,
    connect_encrypted,
    database_account,
    encrypted_database,
)
from scribeloop_core.storage.keys import KeyUnavailable
from scribeloop_core.storage.sessions import SessionStore
from scribeloop_core.storage.vectors import load_vec_extension

cipher = pytest.importorskip('sqlcipher3.dbapi2')
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX encrypted-store lifecycle')


class MemoryKeys:
    def __init__(self):
        self.keys = {}
        self.created = 0

    def get(self, account):
        return self.keys.get(account)

    def create(self, account):
        if account in self.keys:
            raise KeyUnavailable('Existing key')
        self.keys[account] = secrets.token_bytes(32)
        self.created += 1
        return self.keys[account]

    def delete(self, account):
        del self.keys[account]


def test_encrypted_database_reopens_and_encrypts_database_and_wal(tmp_path):
    path = tmp_path / 'private.sqlcipher'
    keys = MemoryKeys()
    sentinel = 'private-transcript-sentinel-68d1f773'
    with encrypted_database(path, keys) as conn:
        store = SessionStore(conn)
        session = store.create('public', sentinel, '/public.wav')
        store.append(session['id'], {'text':sentinel, 'start_ms':0, 'end_ms':100, 'is_final':True})
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA secure_delete').fetchone()[0] == 1
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        for file in [path, Path(str(path)+'-wal')]:
            assert sentinel.encode() not in file.read_bytes()
            assert file.stat().st_mode & 0o777 == 0o600
    assert not path.read_bytes().startswith(b'SQLite format 3')
    with encrypted_database(path, keys) as conn:
        assert SessionStore(conn).segments(session['id'])[0]['text'] == sentinel
    assert keys.created == 1
    with sqlite3.connect(path) as plaintext, pytest.raises(sqlite3.DatabaseError):
        plaintext.execute('SELECT * FROM sessions').fetchall()


def test_missing_key_never_replaces_existing_database(tmp_path):
    path = tmp_path / 'db'
    keys = MemoryKeys()
    with encrypted_database(path, keys):
        pass
    original = path.read_bytes()
    keys.keys.clear()
    with pytest.raises(KeyUnavailable, match='missing'), encrypted_database(path, keys):
        pass
    assert path.read_bytes() == original
    assert keys.created == 1


def test_plaintext_database_is_not_implicitly_migrated(tmp_path):
    path = tmp_path / 'public.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE original(value TEXT)')
    original = path.read_bytes()
    with pytest.raises(KeyUnavailable), encrypted_database(path, MemoryKeys()):
        pass
    assert path.read_bytes() == original


def test_wrong_key_fails_closed_and_releases_resources(tmp_path):
    path = tmp_path / 'db'
    keys = MemoryKeys()
    with encrypted_database(path, keys):
        pass
    with pytest.raises(EncryptedStorageError):
        connect_encrypted(path, secrets.token_bytes(32))
    with encrypted_database(path, keys) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5


def test_exclusive_database_lock_and_exception_release(tmp_path):
    path = tmp_path / 'db'
    keys = MemoryKeys()
    with encrypted_database(path, keys), pytest.raises(EncryptedStorageError, match='in use'), \
            encrypted_database(path, keys):
        pass
    with pytest.raises(RuntimeError), encrypted_database(path, keys):
        raise RuntimeError('application failure')
    with encrypted_database(path, keys):
        pass


def test_keychain_failure_does_not_create_database_or_fallback(tmp_path):
    class Denied(MemoryKeys):
        def get(self, account):
            raise KeyUnavailable('Locked keystore')

    path = tmp_path / 'db'
    with pytest.raises(KeyUnavailable), encrypted_database(path, Denied()):
        pass
    assert not path.exists()
    with encrypted_database(path, MemoryKeys()):
        pass


def test_plain_sqlite_driver_cannot_masquerade_as_cipher(tmp_path, monkeypatch):
    monkeypatch.setattr('scribeloop_core.storage.encrypted.cipher_driver', lambda: sqlite3)
    path = tmp_path / 'db'
    with pytest.raises(EncryptedStorageError), encrypted_database(path, MemoryKeys()):
        pass
    assert path.read_bytes() == b''


def test_sqlite_vec_loads_through_python_cipher_connection(tmp_path):
    with encrypted_database(tmp_path / 'vectors', MemoryKeys()) as conn:
        load_vec_extension(conn)
        conn.execute('CREATE VIRTUAL TABLE test_vec USING vec0(id integer primary key, vec float[2])')
        conn.execute('INSERT INTO test_vec VALUES(1, ?)', (np.array([1,2],dtype=np.float32).tobytes(),))
        assert conn.execute('SELECT count(*) FROM test_vec').fetchone()[0] == 1


def test_encrypted_api_persists_and_reconnects_without_plaintext(tmp_path):
    wav = tmp_path / 'public.wav'
    sf.write(wav, np.zeros(1600,dtype=np.float32),16000)
    keys = MemoryKeys()
    path = tmp_path / 'replay.sqlcipher'

    class ASR:
        def feed(self, frame):
            return [{'text':'encrypted-api-sentinel', 'start_ms':0,'end_ms':100,'is_final':True}]
        def flush(self): return []
        def close(self): pass

    app, token = create_app(storage='encrypted', database=path, fixtures={'public':wav},
                             backend_factory=ASR, key_store=keys)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            assert ws.receive_json()['storage'] == 'encrypted'
            ws.send_json({'action':'start','fixture':'public','title':'Test','consent_confirmed':True})
            for _ in range(8):
                event = ws.receive_json()
                if event['type']=='session' and event['session']['status']=='completed':
                    session_id=event['session']['id']
                    break
            else:
                pytest.fail('Encrypted replay did not finish')
        with client.websocket_connect('ws://127.0.0.1/ws') as ws:
            ws.send_text(token)
            assert ws.receive_json()['segments'][session_id][0]['text']=='encrypted-api-sentinel'
    assert b'encrypted-api-sentinel' not in path.read_bytes()
    assert keys.created == 1
    assert len(keys.keys[database_account(path)]) == 32


def test_cipher_database_errors_stop_replay_without_escaping_server(tmp_path, monkeypatch):
    wav=tmp_path/'public.wav'
    sf.write(wav,np.zeros(1600,dtype=np.float32),16000)

    class ASR:
        def feed(self,frame): return [{'text':'x','start_ms':0,'end_ms':100,'is_final':True}]
        def flush(self): return []
        def close(self): pass

    def fail(self,*args):
        raise cipher.OperationalError('private driver diagnostic')

    monkeypatch.setattr(SessionStore,'append',fail)
    app,token=create_app(storage='encrypted',database=tmp_path/'db',fixtures={'public':wav},
                         backend_factory=ASR,key_store=MemoryKeys())
    with TestClient(app,base_url='http://127.0.0.1') as client, \
            client.websocket_connect('ws://127.0.0.1/ws') as ws:
        ws.send_text(token)
        ws.receive_json()
        ws.send_json({'action':'start','fixture':'public','consent_confirmed':True})
        for _ in range(8):
            event=ws.receive_json()
            if event['type']=='replay_unavailable': break
        else:
            pytest.fail('Cipher error escaped the storage failure path')
        assert 'private driver' not in str(event)
