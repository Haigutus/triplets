"""In-process engine-state caches — explicit lifecycle.

Engine state (loaded rdflib datasets, oxigraph stores, qlever index handles,
compiled SHACL shapes) is cached for the process lifetime, keyed by content.
Nothing evicts automatically — rebuilds dominate the cost and typical working
sets are small. Long-running processes that churn through many distinct
datasets manage the lifecycle explicitly:

    triplets.clear_caches()       # drop all in-memory engine state
    with triplets.cache_scope():  # entries created inside are dropped on exit
        ...

Only in-memory state is dropped: qlever's on-disk indexes stay (reloading one
is ~4 ms; delete $TRIPLETS_QLEVER_DIR to reclaim disk). Each cache dict
registers itself at module import via register_cache.
"""
from contextlib import contextmanager

_CACHES = []


def register_cache(cache: dict) -> dict:
    """Register a module-level cache dict with clear_caches/cache_scope."""
    _CACHES.append(cache)
    return cache


def clear_caches() -> None:
    """Drop all in-memory engine state (see module docstring)."""
    for cache in _CACHES:
        cache.clear()


@contextmanager
def cache_scope():
    """Bound engine-state lifetime to a block: entries created inside are
    dropped on exit; entries that existed before the block are kept."""
    snapshots = [dict(cache) for cache in _CACHES]
    try:
        yield
    finally:
        for index, cache in enumerate(_CACHES):   # caches registered inside the
            cache.clear()                         # block have no snapshot — fully dropped
            if index < len(snapshots):
                cache.update(snapshots[index])
