import numpy as np
import pytest
from scribeloop_core.storage.memory_cache import DEFAULT_CACHE_BYTES, MemoryCache, retained_size


def test_default_budget_lru_and_oversized_entries():
    assert DEFAULT_CACHE_BYTES == 256*1024*1024
    cache=MemoryCache(16000)
    a,b,c=(('pcm',str(i)) for i in range(3))
    assert cache.put(a,b'a'*5000,owner='a')
    assert cache.put(b,b'b'*5000,owner='b')
    assert cache.get(a)==b'a'*5000
    assert cache.put(c,b'c'*5000,owner='c')
    assert cache.get(b) is None and cache.get(a) and cache.get(c)
    assert cache.stats()['evictions']==1 and cache.stats()['used_bytes']<=16000
    assert not cache.put(('pcm','large'),b'x'*20000)
    cache.invalidate('a')
    assert cache.get(a) is None and cache.get(c)
    cache.clear()
    assert cache.stats()['used_bytes']==0


def test_views_share_one_budget_and_include_array_bases():
    cache=MemoryCache(10000)
    metadata=cache.namespace('metadata')
    metadata['session']=b'm'*6000
    assert cache.put(('pcm','session'),np.zeros(1600,dtype='float32'),owner='session')
    assert metadata.get('session') is None
    large=np.zeros(4096,dtype='float32')
    assert retained_size(large[:1])>=large.nbytes
    assert not cache.put(('pcm','view'),large[:1])
    assert cache.stats()['used_bytes']<=10000


def test_concurrent_eviction_and_session_invalidation_stay_bounded():
    from concurrent.futures import ThreadPoolExecutor
    cache=MemoryCache(64000)
    def work(worker):
        for i in range(100):
            owner=str(worker)
            key=('fixture',(owner,i%10))
            cache.put(key,b'x'*2000,owner=owner)
            cache.get(key)
            if i%7==0: cache.invalidate(owner)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work,range(4)))
    assert 0<=cache.stats()['used_bytes']<=64000


def test_zero_budget_is_a_valid_no_cache_mode():
    cache=MemoryCache(0)
    view=cache.namespace('fixture')
    view['x']='value'
    assert view.get('x') is None and len(view)==0 and cache.stats()['used_bytes']==0
    with pytest.raises(ValueError): MemoryCache(-1)


def test_transcript_pages_copy_values_and_invalidate_changes(tmp_path):
    from scribeloop_core.storage.db import connect, migrate
    from scribeloop_core.storage.sessions import SessionStore
    conn=migrate(connect(':memory:'))
    store=SessionStore(conn)
    sid=store.create_recording('Synthetic',{})['id']
    row=store.append(sid,{'text':'Original','start_ms':0,'end_ms':100,'is_final':True})
    page=store.segments(sid,limit=100)
    page[0]['text']='Caller mutation'
    assert store.segments(sid,limit=100)[0]['text']=='Original'
    with conn:
        conn.execute('UPDATE segments SET text=? WHERE id=?',('Edited',row['id']))
    assert store.segments(sid,limit=100)[0]['text']=='Edited'
    store.finish_delete(sid)
    assert store.segments(sid,limit=100)==[]
    conn.close()
