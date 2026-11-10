"""A small thread-safe LRU map with hit/miss/eviction counters.

Handlers run in the threadpool (see the concurrency model in routing.py), and a
few read the caches without the studio lock (/preview, /debug/stats), so
every operation takes the cache's own lock. An OrderedDict mutated by two
threads at once can corrupt its linked list, not just miscount.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable


class LRU[V]:
    """`max_bytes` (with `sizeof`) bounds memory as well as entry count: WAV
    results range from a 0.1 s drum hit to a 10 s song section, so a count
    alone bounds neither memory nor how many small items fit. The newest
    item is never evicted by its own insert, even when it alone is over the
    byte budget — the request that rendered it still has to serve it."""

    def __init__(
        self,
        max_entries: int,
        max_bytes: int | None = None,
        sizeof: Callable[[V], int] | None = None,
    ) -> None:
        self._max = max_entries
        self.max_bytes = max_bytes
        self._sizeof = sizeof or (lambda _v: 0)
        self._items: OrderedDict[str, V] = OrderedDict()
        self._sizes: dict[str, int] = {}
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = self.misses = self.evictions = 0

    def get(self, key: str) -> V | None:
        with self._lock:
            value = self._items.get(key)
            if value is None:
                self.misses += 1
                return None
            self.hits += 1
            self._items.move_to_end(key)
            return value

    def by_sig(self, key: str, sig, build: Callable[[], object]):
        """What `build()` made for `sig` under `key`, built again only when
        the sig has moved — a store's listing kept by its signature
        (`files.dir_sig`). Held as `(sig, value)`."""
        hit = self.get(key)
        if hit is None or hit[0] != sig:
            hit = (sig, build())
            self.put(key, hit)
        return hit[1]

    def peek(self, key: str) -> V | None:
        """`get` without the bookkeeping: no hit or miss counted, no move
        to the fresh end (a second look by a caller that already missed)."""
        with self._lock:
            return self._items.get(key)

    def put(self, key: str, value: V) -> None:
        with self._lock:
            self._bytes -= self._sizes.pop(key, 0)
            size = self._sizeof(value) if self.max_bytes else 0
            self._items[key] = value
            self._items.move_to_end(key)
            self._sizes[key] = size
            self._bytes += size
            while len(self._items) > 1 and (
                len(self._items) > self._max
                or (self.max_bytes and self._bytes > self.max_bytes)
            ):
                old, _ = self._items.popitem(last=False)
                self._bytes -= self._sizes.pop(old, 0)
                self.evictions += 1

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def stats(self) -> dict:
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "entries": len(self._items),
            } | ({"bytes": self._bytes} if self.max_bytes else {})
