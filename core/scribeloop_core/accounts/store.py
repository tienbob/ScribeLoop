"""Account-service database, separate from encrypted desktop workspaces.

Opaque session credentials are stored only as hashes. There is deliberately no
public endpoint for granting purchases; a later verified billing adapter owns it.
"""
import hashlib
import secrets
import sqlite3
import time
from threading import RLock
from uuid import uuid4

from .contracts import AccountUser


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AccountStore:
    def __init__(self, path, *, clock=time.time):
        self.clock = clock
        self.lock = RLock()
        self.conn = sqlite3.connect(str(path),check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        version = self.conn.execute('PRAGMA user_version').fetchone()[0]
        marker = self.conn.execute('PRAGMA application_id').fetchone()[0]
        tables = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if (version not in (0,1) or marker not in (0,1397506371)
                or (tables and (version!=1 or marker!=1397506371))):
            self.conn.close()
            raise ValueError('Select a separate supported ScribeLoop account database')
        self.conn.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT NOT NULL,name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS identities(subject TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS challenges(hash TEXT PRIMARY KEY,expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
                access_hash TEXT NOT NULL UNIQUE,refresh_hash TEXT NOT NULL UNIQUE,
                access_until INTEGER NOT NULL,refresh_until INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS entitlements(user_id TEXT PRIMARY KEY REFERENCES users(id),
                local_owned INTEGER NOT NULL DEFAULT 0 CHECK(local_owned IN (0,1)),cloud_until INTEGER);
            PRAGMA user_version=1;
            PRAGMA application_id=1397506371;
            COMMIT;
        ''')

    def challenge(self):
        nonce = secrets.token_urlsafe(32)
        with self.lock, self.conn:
            self.conn.execute('DELETE FROM challenges WHERE expires<=?',(int(self.clock()),))
            count = self.conn.execute('SELECT COUNT(*) FROM challenges').fetchone()[0]
            if count >= 4096:
                raise ValueError('Too many pending sign-ins')
            self.conn.execute('INSERT INTO challenges VALUES (?,?)',(digest(nonce),int(self.clock())+300))
        return nonce

    def login(self, claims):
        subject,nonce = claims.get('sub'),claims.get('nonce')
        if (not isinstance(subject,str) or not 1<=len(subject)<=255
                or not isinstance(nonce,str) or not 20<=len(nonce)<=128
                or claims.get('email_verified') is not True
                or not isinstance(claims.get('email'),str)):
            raise ValueError('Google identity is incomplete')
        email = claims['email']
        name = claims.get('name') or ''
        if len(email)>320 or not isinstance(name,str) or len(name)>160:
            raise ValueError('Google profile exceeds bounds')
        with self.lock, self.conn:
            consumed = self.conn.execute('DELETE FROM challenges WHERE hash=? AND expires>?',
                                        (digest(nonce),int(self.clock())))
            if consumed.rowcount != 1:
                raise ValueError('Expired or reused login challenge')
            row = self.conn.execute('SELECT user_id FROM identities WHERE subject=?',(subject,)).fetchone()
            uid = row[0] if row else uuid4().hex
            self.conn.execute('INSERT INTO users VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET email=excluded.email,name=excluded.name',
                              (uid,email,name))
            if row is None:
                self.conn.execute('INSERT INTO identities VALUES (?,?)',(subject,uid))
                self.conn.execute('INSERT INTO entitlements(user_id) VALUES (?)',(uid,))
            return self._session(uid)

    def _session(self, uid, sid=None, refresh_until=None):
        access,refresh = secrets.token_urlsafe(32),secrets.token_urlsafe(48)
        now = int(self.clock())
        until = refresh_until if refresh_until is not None else now+30*86400
        if sid:
            self.conn.execute('DELETE FROM sessions WHERE id=?',(sid,))
        self.conn.execute('DELETE FROM sessions WHERE refresh_until<=?',(now,))
        self.conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',
                          (sid or uuid4().hex,uid,digest(access),digest(refresh),now+900,until))
        return {'access_token':access,'refresh_token':refresh,'expires_at':now+900,'user_id':uid}

    def rotate(self, refresh):
        with self.lock, self.conn:
            row = self.conn.execute('SELECT * FROM sessions WHERE refresh_hash=? AND refresh_until>?',
                                    (digest(refresh),int(self.clock()))).fetchone()
            if not row:
                raise ValueError('Session expired')
            return self._session(row['user_id'],row['id'],row['refresh_until'])

    def authenticate(self, access):
        with self.lock:
            row = self.conn.execute('SELECT user_id FROM sessions WHERE access_hash=? AND access_until>?',
                                    (digest(access),int(self.clock()))).fetchone()
            if row is None:
                raise ValueError('Sign in again')
            return row[0]

    def logout(self, refresh):
        with self.lock, self.conn:
            self.conn.execute('DELETE FROM sessions WHERE refresh_hash=?',(digest(refresh),))

    def account(self, uid):
        with self.lock:
            user = self.conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            entitlement = self.conn.execute('SELECT * FROM entitlements WHERE user_id=?',(uid,)).fetchone()
            if not user or not entitlement:
                raise ValueError('Account unavailable')
            return AccountUser.model_validate(dict(user)),dict(entitlement)
