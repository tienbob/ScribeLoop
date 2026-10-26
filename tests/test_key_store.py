import base64

import pytest
from scribeloop_core.storage.keys import SERVICE, KeyUnavailable, NativeKeyStore


class Backend:
    def __init__(self): self.items={}
    def get_password(self,service,account): return self.items.get((service,account))
    def set_password(self,service,account,value): self.items[service,account]=value
    def delete_password(self,service,account): del self.items[service,account]


def vault(backend):
    store=NativeKeyStore.__new__(NativeKeyStore)
    store._backend=backend
    return store


def test_key_lifecycle_readback_and_refusal_to_replace_existing_key():
    backend=Backend()
    store=vault(backend)
    assert store.get('test') is None
    key=store.create('test')
    assert len(key)==32 and store.get('test')==key
    with pytest.raises(KeyUnavailable,match='replace'): store.create('test')
    assert store.get('test')==key
    store.delete('test')
    assert store.get('test') is None


@pytest.mark.parametrize('value',['invalid-base64!',base64.b64encode(b'short').decode()])
def test_corrupt_keys_fail_closed(value):
    backend=Backend()
    backend.items[SERVICE,'test']=value
    with pytest.raises(KeyUnavailable): vault(backend).get('test')


def test_provider_failures_are_sanitized_without_credential_fallback():
    class Denied(Backend):
        def get_password(self,*args): raise RuntimeError('private provider detail')
    with pytest.raises(KeyUnavailable) as exc: vault(Denied()).create('test')
    assert 'private provider' not in str(exc.value)
    assert exc.value.__suppress_context__


def test_write_and_delete_must_be_verified():
    class LostWrite(Backend):
        def set_password(self,*args): pass
    with pytest.raises(KeyUnavailable): vault(LostWrite()).create('test')

    class LostDelete(Backend):
        def delete_password(self,*args): pass
    backend=LostDelete()
    store=vault(backend)
    store.create('test')
    with pytest.raises(KeyUnavailable): store.delete('test')


def test_unsupported_platform_has_no_plugin_or_plaintext_fallback(monkeypatch):
    monkeypatch.setattr('scribeloop_core.storage.keys.sys.platform','unsupported')
    with pytest.raises(KeyUnavailable): NativeKeyStore()
