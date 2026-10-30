> Updated 2026-10-28: the native recorder and desktop app are implemented. Audio
> keys now use workspace-encrypted envelopes: one workspace unlock covers new and
> migrated recordings. See [current key management](../packaging/macos/DESKTOP.md#one-workspace-unlock).
> Earlier Phase 1 implementation evidence and keystore descriptions below are historical.

# Phase 1 secure storage foundation — 2026-10-22

Implemented: SQLCipher application database + native OS-keystore lifecycle,
with authenticated per-session encrypted PCM containers ready for the recorder.
**Actual meeting capture/upload remains disabled.** The native recorder, encrypted
container, session lifecycle, and UI still need to be connected as one flow.

## Database and startup

The CLI now defaults to `--storage encrypted`. Install the security extras:

```bash
pip install -e '.[dev,security,sqlcipher]'
.venv/bin/python -m scribeloop_core.api.server \
  --public-fixture data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav
```

The current local check used `sqlcipher3 0.6.4`, SQLCipher 4.19.0 community,
`keyring 25.7.0`, and `cryptography 50.0.2`. Prebuilt driver availability is
platform/Python dependent. Source builds require SQLCipher development headers
and libraries; the Mac has Homebrew SQLCipher under `/opt/homebrew/opt/sqlcipher`.
No frozen-distribution or Linux-host install claim follows from this check.

The default encrypted database is `sessions.sqlcipher` inside the platform's
ScribeLoop user-data directory (`platformdirs`). `--database PATH` overrides it.
SQLCipher 4+ must be detected before using the connection. A random 32-byte key is
retrieved from the OS keystore, then applied before schema access. Shared pragmas
retain foreign keys, secure deletion, and WAL; temporary SQL storage is in memory.
SQLCipher memory security is enabled. Raw key values never go into command-line
arguments, application logs, reports, or plaintext key files.

The keystore service is `ai.scribeloop.storage.v1`; database accounts are
`database:<SHA256 of canonical database path>`. An exclusive POSIX process lock is
held across key lookup/creation, connection use, and connection close, preventing
simultaneous recovery/key creation against the same app database. New DB files are
created with mode 0600. Both SQLCipher's and stdlib SQLite's error hierarchies feed
the runner's failure path.

There is **no plaintext fallback** if the binding, native keystore, key, or encrypted
file is unavailable. Existing files with missing keys are never assigned a replacement
key. A wrong key or a plaintext DB presented to encrypted mode fails rather than
converting or overwriting its contents. Moving/copying a DB to a different canonical
path needs an explicit future key-aware relocation/export flow. Losing its keystore
key makes the encrypted DB unavailable; no recovery password is currently implemented.

## Explicit development modes

```bash
# Existing non-sensitive demo DB, explicitly opting into plaintext storage:
.venv/bin/python -m scribeloop_core.api.server --storage public-demo \
  --database data/phase1/public-replays.sqlite3 \
  --public-fixture data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav
```

Old public demo databases are left untouched and are not migrated automatically.
`create_app()` defaults to memory. Programmatic disk use must explicitly select
`storage='encrypted'` or `storage='public-demo'`; supplying a path without a mode
is rejected. The authenticated snapshot reports the active mode, and the UI
identifies encrypted, unencrypted-public-demo, or temporary-memory storage.
Replay still accepts only startup-configured public fixtures of at most five minutes.

## Native keystore boundary

`NativeKeyStore` explicitly instantiates macOS Keychain or Linux Secret Service.
It does not use the keyring plugin auto-selector, a file-based backend, or an
environment-selected fallback. It verifies write/delete results and refuses to
replace existing keys. Locked/unavailable providers and malformed key values fail
closed with sanitized errors. Key lookup occurs during app startup, before capture.

The local native check passed on macOS. Linux Secret Service needs an unlocked
service/session and is not host-validated here. Windows secure mode is currently
unsupported; portable public-demo/memory replay remains available.

This unsigned Python development build uses the interpreter's Keychain identity;
it does not establish signed-app access isolation or responsible-app prompt
attribution. Encryption protects stored file contents, not a compromised unlocked
process or OS account. Python key/plaintext buffers are not guaranteed to be wiped;
SQLCipher's own memory setting does not change that. OS swap/crash dumps and key
backup/retention are outside this milestone's application-file guarantees.

## Encrypted audio container: SLPA v1

`SessionAudioStore` creates a separate random 256-bit key per session, stored under
`audio:<session UUID hex>`. Loopback and mic files share that session key but derive
independent file keys using HKDF-SHA256 and a fresh random 128-bit file ID.
Files are exclusive-create mode 0600 at `<root>/<session-id>/<stream>.slpa`.
There is no append/resume mode, preventing nonce reuse across reopened files.

The versioned header contains format, sample rate, session ID, stream, and file ID.
These IDs/format metadata and ciphertext length are visible; speech, PCM samples,
and frame timestamps are encrypted. Header, record type/length, and sequence are
authenticated as AES-256-GCM associated data. Each record uses its sequence as a
unique 96-bit nonce under the file-derived key.

Data records hold a recorder timestamp and canonical 16 kHz mono float32 samples;
gaps are preserved. The writer flushes ciphertext records and fsyncs the completed
file. A final authenticated record checks frame/sample totals and last timestamp.
The reader rejects wrong keys/identities, modified/reordered records, trailing
bytes, and missing/truncated footers. Consume through the footer before treating
any file as complete; an authenticated prefix alone is not a complete recording.
IO failure closes/poisons the writer. Application code writes no plaintext audio
file or decryption temporary file.

A crash/exception may leave an encrypted incomplete prefix. Explicit M5 recovery
now authenticates both tracks, preserves originals and writes fresh encrypted
containers with new file IDs/nonces; strict readers still reject unfinalized files.
Recorded/confirmed checkpoints, capture-free resume, and retryable key-first
session deletion/explicit retention cleanup are implemented. Details and limits:
[M5/M6](M5_M6.md). Recorder progress follows periodic fsync, and recovery fsyncs
new containers and directory entries. No interrupted recording is silently finalized
or reopened for capture. Existing readers and backed-up keys remain outside the
local deletion guarantee.

## Verification

- **122 local tests pass**, including real Python SQLCipher DB/WAL protection,
  close/reopen, wrong/missing keys, plaintext-driver refusal, exclusive-lock release,
  sqlite-vec coexistence, encrypted API persistence, and SQLCipher error handling.
- Audio tests cover exact PCM/timestamp recovery, file key separation, wrong keys,
  identity mismatch, tampering, reordered records, truncation/footer removal,
  trailing bytes, failed IO, and blocking new reads after session-key removal.
- Native Keychain smoke created/read/deleted fresh diagnostic keys only. SQLCipher
  reopened its DB; a five-minute synthetic signal (3,000 frames) round-tripped
  exactly. Container write + final fsync took 0.045 s; read/verification took 0.020 s
  on temporary local storage (cached IO, not a disk/ASR/capture benchmark).
  Encrypted audio size: 19,299,174 bytes. No captured audio was saved. Temporary
  diagnostic files and keys were removed. Aggregate report:
  `data/phase1/secure-storage-smoke.json` (gitignored).
- The actual Keychain-backed API also transcribed 20 s of public AMI audio,
  saved 7 confirmed segments, restored them after reconnect, and rejected a
  plaintext SQLite read of its DB. Brave displayed “Encrypted on this machine”
  alongside the restored transcript. Report: `data/phase1/encrypted-api-smoke.json`.
  The local encrypted preview uses `data/phase1/secure-preview.sqlcipher`; its
  application key is intentionally retained in Keychain for future restarts.
  This preview is separate from the removed temporary diagnostic entries.
- Portable CI installs `security` for crypto/keystore tests. Real SQLCipher tests
  skip if its optional binding is absent; native OS-keystore access is opt-in,
  not run by the ordinary test suite. No new remote CI or Linux-host result is claimed.

```bash
.venv/bin/pytest -q
node tests/frontend_replay.mjs
# Explicit local native-keystore check; choose a fresh report path:
.venv/bin/python scripts/check_secure_storage.py --native-keystore \
  --output data/phase1/secure-storage-smoke-new.json
```

Implementation references: [SQLCipher key/configuration API](https://www.zetetic.net/sqlcipher/sqlcipher-api/),
[native keyring backends](https://keyring.readthedocs.io/en/latest/), and
[authenticated encryption API](https://cryptography.io/en/latest/hazmat/primitives/aead/).
