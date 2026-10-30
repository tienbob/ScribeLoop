"""Shared, lazy byte-budgeted LRU. Process memory only; never writes to disk."""
from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from collections.abc import MutableMapping

import numpy as np

DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
_MISSING = object()


def retained_size(value, seen=None):
    """Conservative retained Python/NumPy size, including referenced array bases."""
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, np.ndarray):
        if value.base is not None:
            size += retained_size(value.base, seen)
    elif isinstance(value, dict):
        size += sum(retained_size(k, seen)+retained_size(v, seen) for k,v in value.items())
    elif isinstance(value, (tuple,list,set,frozenset)):
        size += sum(retained_size(v, seen) for v in value)
    elif isinstance(value, memoryview):
        size += retained_size(value.obj, seen)
    return size


class MemoryCache:
    def __init__(self, max_bytes=DEFAULT_CACHE_BYTES):
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError('Invalid memory cache budget')
        self.max_bytes = max_bytes
        self.used_bytes = 0
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self.hits = self.misses = self.evictions = 0

    def get(self, key, default=None):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return default
            self.hits += 1
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, key, value, owner=None):
        # Additional allowance covers OrderedDict links and entry bookkeeping.
        size = retained_size((key,value,owner))+256
        with self._lock:
            self._remove(key)
            if size > self.max_bytes:
                return False
            while self.used_bytes+size > self.max_bytes:
                old = next(iter(self._entries))
                self._remove(old)
                self.evictions += 1
            self._entries[key] = (value,size,owner)
            self.used_bytes += size
            return True

    def _remove(self, key):
        entry = self._entries.pop(key,None)
        if entry is not None:
            self.used_bytes -= entry[1]

    def discard(self, key):
        with self._lock:
            self._remove(key)

    def invalidate(self, owner, namespaces=None):
        with self._lock:
            for key, entry in list(self._entries.items()):
                if entry[2] == owner and (namespaces is None or key[0] in namespaces):
                    self._remove(key)

    def clear(self):
        with self._lock:
            self._entries.clear()
            self.used_bytes = 0

    def stats(self):
        with self._lock:
            return {'max_bytes':self.max_bytes,'used_bytes':self.used_bytes,'entries':len(self._entries),
                    'hits':self.hits,'misses':self.misses,'evictions':self.evictions}

    def namespace(self, name, owner=lambda key:key):
        return CacheView(self,name,owner)


class CacheView(MutableMapping):
    def __init__(self, cache, name, owner):
        self.cache, self.name, self.owner = cache, name, owner

    def __getitem__(self, key):
        value = self.cache.get((self.name,key),_MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        self.cache.put((self.name,key),value,self.owner(key))

    def __delitem__(self, key):
        self.cache.discard((self.name,key))

    def __iter__(self):
        with self.cache._lock:
            return iter([key[1] for key in self.cache._entries if key[0] == self.name])

    def __len__(self):
        with self.cache._lock:
            return sum(key[0] == self.name for key in self.cache._entries)

    def move_to_end(self, key):
        with self.cache._lock:
            full = (self.name,key)
            if full in self.cache._entries:
                self.cache._entries.move_to_end(full)
