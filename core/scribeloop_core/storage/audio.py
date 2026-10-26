"""Authenticated encrypted PCM containers; plaintext is never written to disk.

SLPA v1: magic, random file ID, bounded JSON metadata, then AES-256-GCM records.
Each file derives its own key from a per-session keystore key. Sequence nonces
and an authenticated final record detect tampering, reordering, and truncation.
Readers must consume through the footer before treating a file as complete.
"""
from __future__ import annotations

import json
import os
import re
import struct
import time
from pathlib import Path

import numpy as np

from ..capture.source import SAMPLE_RATE, AudioFrame
from .keys import KeyStore, KeyUnavailable

MAGIC = b'SLPA\x01'
RECORD = struct.Struct('<BI')
FRAME = struct.Struct('<QI')
FOOTER = struct.Struct('<QQd')
MAX_SAMPLES = SAMPLE_RATE
MAX_CIPHERTEXT = FRAME.size + MAX_SAMPLES * 4 + 16
# Native packets are continuous in nanoseconds, but SLPA timestamps are rounded
# integer milliseconds. Adjacent rounding errors can differ by almost 1 ms.
TIMESTAMP_ROUNDING_MS = 1


class AudioIntegrityError(ValueError):
    pass


def _identity(session_id, stream):
    if not isinstance(session_id, str) or not re.fullmatch(r'[0-9a-f]{32}', session_id):
        raise ValueError('Expected a session UUID hex ID')
    if stream not in {'loopback', 'mic'}:
        raise ValueError('Unknown audio stream')


def _cipher(key, file_id):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError('Audio encryption requires a 32-byte session key')
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=file_id, info=MAGIC).derive(key)
    return AESGCM(derived)


def _read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise AudioIntegrityError('Incomplete encrypted audio')
    return data


class EncryptedAudioWriter:
    def __init__(self, path: Path, session_id: str, stream: str, key: bytes):
        _identity(session_id, stream)
        metadata = json.dumps({'session_id':session_id, 'stream':stream,
            'sample_rate':SAMPLE_RATE, 'format':'f32le'}, separators=(',',':')).encode('utf-8')
        file_id = os.urandom(16)
        self.header = MAGIC + file_id + struct.pack('<H', len(metadata)) + metadata
        self.cipher = _cipher(key, file_id)
        self.stream = stream
        self.sequence = self.samples = 0
        self.last_end = 0.0
        self.closed = False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.file = os.fdopen(fd, 'wb')
        try:
            self.file.write(self.header)
            self.file.flush()
            os.fsync(self.file.fileno())
            if os.name == 'posix':
                # Persist both the session-directory entry and the new file entry.
                for directory in (path.parent.parent,path.parent):
                    fd = os.open(directory,os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        except BaseException:
            self.file.close()
            raise

    def _record(self, kind, plaintext):
        prefix = RECORD.pack(kind, len(plaintext) + 16)
        counter = self.sequence.to_bytes(12, 'big')
        encrypted = self.cipher.encrypt(counter, plaintext, self.header + counter + prefix)
        try:
            self.file.write(prefix + encrypted)
            self.file.flush()
        except BaseException:
            self.abort()
            raise
        self.sequence += 1

    def write(self, frame: AudioFrame):
        if self.closed:
            raise ValueError('Audio writer is closed')
        pcm = np.asarray(frame.pcm)
        if (frame.stream != self.stream or pcm.ndim != 1 or not 0 < pcm.size <= MAX_SAMPLES
                or pcm.dtype != np.dtype(np.float32) or not np.isfinite(pcm).all()
                or not isinstance(frame.t_ms, (int, np.integer)) or isinstance(frame.t_ms, bool)
                or not 0 <= frame.t_ms <= 2**63 - 1 or frame.t_ms < self.last_end - TIMESTAMP_ROUNDING_MS):
            raise ValueError('Invalid PCM frame or recorder timestamp')
        self._record(0, FRAME.pack(frame.t_ms, pcm.size) + pcm.astype('<f4').tobytes())
        self.samples += pcm.size
        self.last_end = frame.t_ms + pcm.size * 1000 / SAMPLE_RATE

    def close(self):
        if self.closed:
            return
        try:
            self._record(1, FOOTER.pack(self.samples, self.sequence, self.last_end))
            os.fsync(self.file.fileno())
        finally:
            self.file.close()
            self.closed = True

    def checkpoint(self):
        """Make preceding authenticated records durable before publishing progress."""
        if self.closed:
            raise ValueError('Audio writer is closed')
        self.file.flush()
        os.fsync(self.file.fileno())

    def abort(self):
        """Keep the encrypted prefix, without falsely marking it complete."""
        self.file.close()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.close()
        else:
            self.abort()


def read_audio(path: Path, session_id: str, stream: str, key: bytes, *, _prefix=None):
    """Yield authenticated frames. Missing/altered footer raises, even after valid frames."""
    from cryptography.exceptions import InvalidTag

    _identity(session_id, stream)
    with Path(path).open('rb') as source:
        fixed = _read_exact(source, len(MAGIC) + 18)
        if not fixed.startswith(MAGIC):
            raise AudioIntegrityError('Unsupported encrypted audio format')
        file_id = fixed[len(MAGIC):len(MAGIC)+16]
        size = struct.unpack('<H', fixed[-2:])[0]
        if not 0 < size <= 1024:
            raise AudioIntegrityError('Invalid encrypted audio metadata size')
        metadata = _read_exact(source, size)
        expected = {'session_id':session_id, 'stream':stream, 'sample_rate':SAMPLE_RATE, 'format':'f32le'}
        try:
            if json.loads(metadata) != expected:
                raise AudioIntegrityError('Encrypted audio identity mismatch')
        except (UnicodeError, json.JSONDecodeError):
            raise AudioIntegrityError('Invalid encrypted audio metadata') from None
        header = fixed + metadata
        cipher = _cipher(key, file_id)
        sequence = samples = 0
        last_end = 0.0
        # Snapshot the byte boundary: a concurrent writer cannot make this read
        # unbounded. Prefix mode tolerates only a missing/incomplete final record;
        # malformed records and failed authentication remain fatal.
        boundary = os.fstat(source.fileno()).st_size
        while True:
            record_start = source.tell()
            if _prefix is not None and boundary - record_start < RECORD.size:
                _prefix.update(complete=False, samples=samples, end_ms=last_end,
                               authenticated_bytes=record_start, tail_bytes=boundary-record_start)
                return
            prefix = _read_exact(source, RECORD.size)
            kind, length = RECORD.unpack(prefix)
            if kind not in (0, 1) or not 16 <= length <= MAX_CIPHERTEXT:
                raise AudioIntegrityError('Invalid encrypted audio record')
            if _prefix is not None and boundary - source.tell() < length:
                _prefix.update(complete=False, samples=samples, end_ms=last_end,
                               authenticated_bytes=record_start, tail_bytes=boundary-record_start)
                return
            counter = sequence.to_bytes(12, 'big')
            try:
                plaintext = cipher.decrypt(counter, _read_exact(source, length), header + counter + prefix)
            except InvalidTag:
                raise AudioIntegrityError('Encrypted audio authentication failed') from None
            if kind == 1:
                if len(plaintext) != FOOTER.size or FOOTER.unpack(plaintext) != (samples, sequence, last_end):
                    raise AudioIntegrityError('Invalid encrypted audio footer')
                if source.read(1):
                    raise AudioIntegrityError('Unexpected data after encrypted audio footer')
                if _prefix is not None:
                    _prefix.update(complete=True, samples=samples, end_ms=last_end,
                                   authenticated_bytes=source.tell(), tail_bytes=0)
                return
            if len(plaintext) < FRAME.size:
                raise AudioIntegrityError('Invalid encrypted PCM frame')
            t_ms, count = FRAME.unpack(plaintext[:FRAME.size])
            if not 0 < count <= MAX_SAMPLES or len(plaintext) != FRAME.size + count * 4:
                raise AudioIntegrityError('Invalid encrypted PCM frame size')
            pcm = np.frombuffer(plaintext[FRAME.size:], dtype='<f4').astype(np.float32)
            if not np.isfinite(pcm).all() or t_ms > 2**63 - 1 or t_ms < last_end - TIMESTAMP_ROUNDING_MS:
                raise AudioIntegrityError('Invalid encrypted PCM samples or timestamps')
            samples += count
            last_end = t_ms + count * 1000 / SAMPLE_RATE
            sequence += 1
            yield AudioFrame(pcm, stream, t_ms, time.monotonic())


class SessionAudioStore:
    def __init__(self, root: Path, keys: KeyStore):
        self.root = Path(root)
        self.keys = keys

    def create_session(self, session_id: str):
        _identity(session_id, 'loopback')
        self.keys.create('audio:' + session_id)

    def _key(self, session_id):
        key = self.keys.get('audio:' + session_id)
        if key is None:
            raise KeyUnavailable('Session audio key is missing')
        return key

    def original_path(self, session_id, stream):
        _identity(session_id, stream)
        return self.root / session_id / (stream + '.slpa')

    def path(self, session_id, stream):
        original = self.original_path(session_id, stream)
        recovered = original.with_name(stream + '.recovered.slpa')
        return recovered if recovered.exists() else original

    def writer(self, session_id, stream):
        return EncryptedAudioWriter(self.original_path(session_id, stream), session_id, stream, self._key(session_id))

    def frames(self, session_id, stream):
        return read_audio(self.path(session_id, stream), session_id, stream, self._key(session_id))

    def forget_session_key(self, session_id):
        """Irreversible crypto-shred; invoke only from an authorized deletion flow.

        Existing unlocked readers already hold keys in memory and must be stopped
        before deletion. Backed-up keys/ciphertext require a separate retention policy.
        """
        _identity(session_id, 'loopback')
        self.keys.delete('audio:' + session_id)


    def prefix(self, session_id, stream, *, durable=False, original=False):
        """Bounded authenticated snapshot; an unfinalized tail is never speech."""
        path = self.original_path(session_id,stream) if original else self.path(session_id,stream)
        report = {}
        frames = []
        samples = 0
        for frame in read_audio(path, session_id, stream,
                                self._key(session_id), _prefix=report):
            samples += frame.pcm.size
            if samples > 305 * SAMPLE_RATE or frame.t_ms > 305000:
                raise AudioIntegrityError('Recording exceeds development bounds')
            frames.append(frame)
        if durable:
            with path.open('rb') as source:
                os.fsync(source.fileno())
        return frames, report

    def recover_session(self, session_id, *, streams=('mic','loopback')):
        """Explicit idle-session operation. Originals remain unchanged.

        Authenticate both tracks before changing either selected path. Each new
        container gets fresh random file-key salt/nonces; never append to a prefix.
        A crash between renames is retryable: already complete recovered tracks
        are checked again and the remaining original is recovered on the retry.
        """
        from uuid import uuid4
        streams = tuple(streams)
        if streams not in {('mic',),('mic','loopback')}:
            raise ValueError('Invalid recovery stream selection')
        snapshots = {stream:self.prefix(session_id, stream, original=True) for stream in ('mic','loopback')}
        if any(not snapshots[stream][0] for stream in streams):
            raise AudioIntegrityError('No authenticated audio in a selected stream')
        if streams == ('mic',) and snapshots['loopback'][0]:
            raise AudioIntegrityError('Unexpected output audio in a microphone-only recording')
        staged = []
        try:
            for stream, (frames, report) in snapshots.items():
                if report['complete']:
                    continue
                target = self.original_path(session_id, stream).with_name(stream + '.recovered.slpa')
                temporary = target.with_name('.' + stream + '-' + uuid4().hex + '.slpa')
                staged.append((temporary, target))
                with EncryptedAudioWriter(temporary, session_id, stream, self._key(session_id)) as writer:
                    for frame in frames:
                        writer.write(frame)
            for temporary, target in staged:
                os.replace(temporary, target)
            directory = os.open(self.root / session_id, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
        return {stream:report for stream, (_, report) in snapshots.items()}

    def delete_session(self, session_id):
        """Retryable key-first removal, only after readers/writers are stopped."""
        _identity(session_id, 'loopback')
        directory = self.root / session_id
        if directory.is_symlink():
            raise OSError('Refuse redirected recording directory')
        if self.keys.get('audio:' + session_id) is not None:
            self.forget_session_key(session_id)
        if directory.exists():
            # Only application-owned encrypted artifacts; no arbitrary paths.
            for path in directory.iterdir():
                if path.is_file() and path.suffix == '.slpa':
                    path.unlink()
            directory.rmdir()
