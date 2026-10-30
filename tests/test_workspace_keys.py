import secrets

import numpy as np
import pytest
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.keys import KeyUnavailable
from scribeloop_core.storage.workspace_keys import WorkspaceAudioKeys


class Legacy:
    def __init__(self):
        self.items = {}
        self.reads = []
        self.denied = False

    def _exists(self, account):
        return account in self.items

    def get(self, account):
        self.reads.append(account)
        if self.denied:
            raise KeyUnavailable('Locked')
        return self.items.get(account)

    def delete_if_present(self, account):
        if self.denied:
            raise KeyUnavailable('Denied deletion')
        self.items.pop(account, None)


def test_multiple_recordings_and_restart_need_no_individual_keychain_reads(tmp_path):
    master = secrets.token_bytes(32)
    legacy = Legacy()
    vault = WorkspaceAudioKeys(tmp_path/'keys', master, legacy)
    audio = SessionAudioStore(tmp_path/'audio', vault)
    ids = ['a'*32, 'b'*32]
    for sid in ids:
        audio.create_session(sid)
        with audio.writer(sid, 'mic') as writer:
            writer.write(AudioFrame(np.ones(160, dtype='float32')*.1, 'mic', 0, 0))
    assert not legacy.reads and not legacy.items
    original = [audio._key(sid) for sid in ids]
    assert original[0] != original[1]
    for file in vault.root.iterdir():
        assert all(key not in file.read_bytes() for key in original)
        assert file.stat().st_mode & 0o777 == 0o600
    audio.clear_unlocked_keys()
    vault.close()
    legacy.denied = True
    restarted = SessionAudioStore(audio.root, WorkspaceAudioKeys(vault.root, master, legacy))
    for sid in ids * 3:
        assert len(list(restarted.frames(sid, 'mic'))) == 1
    assert not legacy.reads


def test_legacy_import_is_once_and_does_not_replace_original_or_touch_audio(tmp_path):
    legacy = Legacy()
    account = 'audio:'+'a'*32
    key = legacy.items[account] = secrets.token_bytes(32)
    master = secrets.token_bytes(32)
    vault = WorkspaceAudioKeys(tmp_path/'keys', master, legacy)
    assert vault.get(account) == key
    assert legacy.items[account] == key
    legacy.denied = True
    restarted = WorkspaceAudioKeys(vault.root, master, legacy)
    assert restarted.get(account) == key
    assert legacy.reads == [account]
    with pytest.raises(KeyUnavailable): restarted.create(account)


@pytest.mark.parametrize('mutation', ['wrong_master', 'corrupt', 'swap', 'symlink'])
def test_damaged_or_redirected_keys_fail_closed_without_legacy_fallback(tmp_path, mutation):
    master = secrets.token_bytes(32)
    legacy = Legacy()
    vault = WorkspaceAudioKeys(tmp_path/'keys', master, legacy)
    a, b = 'audio:'+'a'*32, 'audio:'+'b'*32
    vault.create(a)
    vault.create(b)
    path = vault.root/('a'*32+'.key')
    if mutation == 'wrong_master':
        vault = WorkspaceAudioKeys(vault.root, secrets.token_bytes(32), legacy)
    elif mutation == 'corrupt':
        path.write_bytes(path.read_bytes()[:-1])
    elif mutation == 'swap':
        path.write_bytes((vault.root/('b'*32+'.key')).read_bytes())
    else:
        path.unlink()
        path.symlink_to(vault.root/('b'*32+'.key'))
    with pytest.raises(KeyUnavailable): vault.get(a)
    assert not legacy.reads


def test_import_write_failure_preserves_legacy_and_does_not_return_unpersisted_key(tmp_path, monkeypatch):
    legacy = Legacy()
    account = 'audio:'+'a'*32
    key = legacy.items[account] = secrets.token_bytes(32)
    vault = WorkspaceAudioKeys(tmp_path/'keys', secrets.token_bytes(32), legacy)
    def fail(*args):
        raise OSError('private path')
    monkeypatch.setattr(vault, '_write', fail)
    with pytest.raises(KeyUnavailable) as error: vault.get(account)
    assert 'private path' not in str(error.value)
    assert legacy.items[account] == key
    assert not list(vault.root.iterdir())


def test_deletion_removes_both_keys_before_ciphertext_and_retries_denial(tmp_path):
    legacy = Legacy()
    sid = 'a'*32
    account = 'audio:'+sid
    legacy.items[account] = secrets.token_bytes(32)
    vault = WorkspaceAudioKeys(tmp_path/'keys', secrets.token_bytes(32), legacy)
    audio = SessionAudioStore(tmp_path/'audio', vault)
    with audio.writer(sid, 'mic') as writer:
        writer.write(AudioFrame(np.zeros(160, dtype='float32'), 'mic', 0, 0))
    legacy.denied = True
    with pytest.raises(KeyUnavailable): audio.delete_session(sid)
    assert audio.path(sid, 'mic').exists()
    assert vault.get(account) == legacy.items[account]
    legacy.denied = False
    audio.delete_session(sid)
    audio.delete_session(sid)
    assert not audio.path(sid, 'mic').exists()
    assert not list(vault.root.iterdir()) and not legacy.items
    assert vault.get(account) is None


def test_closed_workspace_does_not_access_legacy_keys(tmp_path):
    legacy = Legacy()
    vault = WorkspaceAudioKeys(tmp_path/'keys', secrets.token_bytes(32), legacy)
    vault.close()
    with pytest.raises(KeyUnavailable): vault.get('audio:'+'a'*32)
    assert not legacy.reads
