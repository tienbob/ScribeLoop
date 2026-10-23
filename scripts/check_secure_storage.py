"""Opt-in native-keystore/SQLCipher/audio smoke. Creates and deletes ONLY fresh test keys.

Synthetic audio only; no recording, credentials, or decrypted audio are saved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
from scribeloop_core.capture.source import SAMPLE_RATE, AudioFrame
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.encrypted import database_account, encrypted_database
from scribeloop_core.storage.keys import KeyUnavailable, NativeKeyStore
from scribeloop_core.storage.sessions import SessionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-keystore', action='store_true', required=True,
                        help='authorize creation/read/deletion of fresh diagnostic keystore entries')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new report path; existing reports are preserved')
    vault = NativeKeyStore()
    with tempfile.TemporaryDirectory(prefix='scribeloop-secure-smoke-') as temp:
        path = Path(temp) / 'diagnostic.sqlcipher'
        sid = uuid4().hex
        accounts = [database_account(path), 'audio:' + sid]
        if any(vault.get(account) is not None for account in accounts):
            raise RuntimeError('Refusing to reuse existing keystore entries')
        report = {}
        try:
            with encrypted_database(path, vault) as conn:
                version = conn.execute('PRAGMA cipher_version').fetchone()[0]
                session = SessionStore(conn).create('synthetic', 'diagnostic-private-sentinel', '/synthetic')
            with encrypted_database(path, vault) as conn:
                assert SessionStore(conn).get(session['id'])['title'] == 'diagnostic-private-sentinel'
            assert b'diagnostic-private-sentinel' not in path.read_bytes()
            audio = SessionAudioStore(Path(temp) / 'audio', vault)
            audio.create_session(sid)
            pcm = (.1 * np.sin(2*np.pi*440*np.arange(1600)/SAMPLE_RATE)).astype(np.float32)
            expected = hashlib.sha256()
            started = time.perf_counter()
            with audio.writer(sid, 'loopback') as writer:
                for i in range(3000):
                    writer.write(AudioFrame(pcm,'loopback',i*100,0))
                    expected.update(pcm.tobytes())
            write_s = time.perf_counter() - started
            started = time.perf_counter()
            actual = hashlib.sha256()
            count = 0
            for frame in audio.frames(sid,'loopback'):
                assert frame.t_ms == count*100
                actual.update(frame.pcm.tobytes())
                count += 1
            read_s = time.perf_counter() - started
            assert count == 3000 and actual.digest() == expected.digest()
            size = audio.path(sid,'loopback').stat().st_size
            audio.forget_session_key(sid)
            try:
                audio.frames(sid,'loopback')
            except KeyUnavailable:
                pass
            else:
                raise AssertionError('Deleted audio key still available')
            report = {'native_backend':type(vault._backend).__module__, 'sqlcipher':version,
                'database_reopened':True, 'database_sentinel_hidden':True,
                'synthetic_audio_s':300, 'frames_verified':count, 'pcm_round_trip_exact':True,
                'write_s':write_s,'read_s':read_s,'encrypted_audio_bytes':size,
                'new_reads_blocked_after_key_delete':True,'captured_audio_saved':False}
        finally:
            for account in accounts:
                if vault.get(account) is not None:
                    vault.delete(account)
        report['temporary_keys_removed'] = True
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as output:
        json.dump(report,output,indent=2,allow_nan=False)
        output.write('\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
