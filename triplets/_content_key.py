"""Content-based identity for engine state caches.

Every SPARQL/SHACL engine keeps built state (qlever on-disk indexes, rdflib
in-memory datasets) keyed by exactly what it loaded: a content_hash over all
four triplet columns (nothing ignored — the key must match the loaded
content, or queries against ignored triples would answer from another
dataset's state), mixed with the export schema and an engine/format-version
salt. This module is engine-neutral so no engine imports another's package.

``data_unchanged=True`` lets the caller assert the data object has not been
mutated since it was last hashed: the stored digest for that exact object is
reused and the (potentially expensive) content_hash is skipped — pandas pays
~0.26 s per 1M rows, so this is the difference between ~25 ms and sub-ms
warm queries. Digests are remembered per object identity with a weakref
eviction callback: when the object is garbage-collected its entry vanishes,
so a new object reusing the same id() can never inherit a stale digest.
"""
import os
import json
import hashlib
import weakref

_HASHES = {}   # id(obj) → (weakref.ref with evict callback, content_hash digest)


def content_key(data, rdf_map, salt, data_unchanged=False):
    """Digest identifying exactly what an engine loads from (data, rdf_map)."""
    content = _content_hash(data, data_unchanged)
    if isinstance(rdf_map, (str, os.PathLike)):
        with open(rdf_map, "rb") as file:
            schema = file.read()
    else:
        schema = json.dumps(rdf_map, sort_keys=True, default=str).encode() if rdf_map else b""
    return hashlib.sha256(salt + content.encode() + schema).hexdigest()[:24]


def _content_hash(data, data_unchanged):
    entry = _HASHES.get(id(data))
    if data_unchanged and entry is not None and entry[0]() is data:
        return entry[1]
    digest = data.content_hash(ignore_types=(), columns=("ID", "KEY", "VALUE", "INSTANCE_ID"))
    oid = id(data)
    _HASHES[oid] = (weakref.ref(data, lambda _: _HASHES.pop(oid, None)), digest)
    return digest
