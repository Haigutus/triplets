"""SPARQL performance engine — embedded qlever (C++), no server.

Uses qlever's official embedding facade (src/libqlever) through the
triplets.sparql._qlever Cython extension (build: setup_qlever.py).

Flavor-blind by construction: the input (pandas / polars DataFrame or a DuckDB
connection) carries everything as registered methods — ``content_hash`` keys
the engine state, ``export_to_arrow`` feeds the index build — so this module
never inspects input types. Scope is not a data operation: the scoped
instances' named graphs are passed to qlever as SPARQL-protocol dataset
clauses (``default-graph-uri``), so one index serves every scope and the
query text is never modified.

Both boundaries are zero-copy Arrow, following the read_rdf pattern — all
heavy lifting happens on the C++ side:

- in: the triplet columns go to the index builder as Arrow batches, feeding
  an injected parser (no N-Quads serialization or text re-parsing anywhere;
  the term mapping applies the same rules as the N-Quads export, from the
  same ``build_key_metadata`` schema interpretation).
- out: the wrapper decodes the query result straight into Arrow string
  buffers (unbound → null), the Cython layer wraps them zero-copy, and this
  module only maps conventions and finalizes the output flavor
  (``return_type``: "auto" matches the input — polars in, polars out;
  explicit "pandas" / "polars" / "arrow" also accepted). All values are
  strings (triplets are all-string; consumers cast); ASK stays a bool via
  the tiny JSON path.

Engine state = one on-disk qlever index per content key (data + rdf_map),
loaded engines cached in-process. The index directory is
``$TRIPLETS_QLEVER_DIR`` (point it at /dev/shm for RAM-backed indexes) or the
temp dir; loaded index files are memory-mapped, so hot pages live in the OS
page cache either way. The GIL is released during index building, queries and
decoding: Python threads parallelize, no fork needed.

Benchmarked 3.5–216x faster than the alternatives on CGMES data; index load
from disk ~4 ms.
"""
import os
import re
import json
import shutil
import logging
import tempfile

from pathlib import Path

import pandas
import pyarrow

from . import _qlever  # ImportError here → the registry falls back to rdflib
from .._caches import register_cache
from .._content_key import content_key
from .._engine_detect import flavor, to_pandas, to_return_type
from ..export.nquads_utils import CIM_NS, build_key_metadata
from ..parser.nquads import terms_to_triplets

logger = logging.getLogger(__name__)

# qlever writes its own INFO log to stdout — keep it quiet unless triplets
# debugging is on (re-enable per-process with _qlever.set_quiet(False))
_qlever.set_quiet(not logger.isEnabledFor(logging.DEBUG))

_INDEXES = register_cache({})    # content hash → loaded _qlever.QleverIndex

# SPARQL grammar: [comments +] prologue (PREFIX/BASE declarations), then the
# query form keyword — anchored so 'select' inside a comment, PREFIX IRI or
# string literal cannot misclassify the query.
_PROLOGUE = re.compile(r"^(?:\s*(?:#[^\n]*|PREFIX\s+\S*\s*<[^>]*>|BASE\s*<[^>]*>))*\s*",
                       re.IGNORECASE)
_QUERY_FORM = re.compile(r"(select|ask|construct|describe)\b", re.IGNORECASE)


def query(data, query_string, rdf_map=None, scope=None, return_type="auto", data_unchanged=False):
    """Execute query_string over data; shape the result by query type.

    Queries are executed exactly as given — the text is never modified.
    qlever's parser is strict; a rejected query raises ValueError carrying
    qlever's message plus the query text, so the failure is directly
    actionable (broken constraint queries belong upstream, see TODO.md).
    ``scope`` travels beside the query as SPARQL-protocol dataset clauses
    (``default-graph-uri``): the query runs against exactly the union of the
    scoped instances' named graphs on the one shared index, and per the
    protocol these take precedence over any FROM inside the query.
    """
    index = _index_for(data, rdf_map, data_unchanged)
    graphs = [f"urn:uuid:{instance}" for instance in scope] if scope is not None else None
    form = _query_form(query_string)
    if return_type == "auto":
        return_type = "polars" if flavor(data) == "polars" else "pandas"

    if form == "ask":
        return bool(json.loads(_run(index.query, query_string, "sparqljson", graphs))["boolean"])
    if form in ("construct", "describe"):
        return _finalize(_terms_to_triplets(_run(index.construct_arrow, query_string, graphs)),
                         return_type)
    return _finalize(_run(index.select_arrow, query_string, graphs), return_type)


def _run(call, query_string, *args):
    try:
        return call(query_string, *args)
    except RuntimeError as error:                          # qlever parse/execution error
        raise ValueError(f"qlever rejected the query: {error}\n"
                         f"--- query ---\n{query_string.strip()[:2000]}") from error


def _finalize(result, return_type):
    """Canonical result (RecordBatch from the C++ decode, or the pandas
    triplet frame from _terms_to_triplets) → requested flavor. Arrow is the
    hub: pandas/polars conversions are zero-copy buffer wraps."""
    if isinstance(result, pyarrow.RecordBatch):
        if return_type == "polars":
            import polars
            return polars.from_arrow(result)
        if return_type == "arrow":
            return pyarrow.Table.from_batches([result])
        return result.to_pandas(types_mapper=pandas.ArrowDtype)
    return to_return_type(result, return_type)


def _index_for(data, rdf_map, data_unchanged=False):
    """Load (or build) the engine state for this exact data + schema.

    Everything goes through the flavor-registered methods: identity via
    content_hash, index input via export_to_arrow — executed only on a
    cache miss. Scope does not key the state (it travels with each query
    as dataset clauses).
    """
    if not hasattr(data, "content_hash"):  # pyarrow — no registered methods
        data = to_pandas(data)
    key = content_key(data, rdf_map, b"triplets-qlever-2", data_unchanged)

    if key in _INDEXES:
        cached = _INDEXES[key]
        if isinstance(cached, Exception):   # this exact data+schema already failed to build
            raise cached
        return cached

    index_dir = Path(os.environ.get("TRIPLETS_QLEVER_DIR", tempfile.gettempdir())) \
        / "triplets-qlever" / key
    if not (index_dir / "build-complete").exists():
        # Concurrency-safe publish: build into a private directory, then rename
        # it into place — atomic, so no process/thread ever sees a half-built
        # index. build-complete marks the directory as a finished build.
        index_dir.parent.mkdir(parents=True, exist_ok=True)
        build_dir = Path(tempfile.mkdtemp(prefix=f"{key}.build-", dir=index_dir.parent))
        try:
            _build_index(data.export_to_arrow(), rdf_map, str(build_dir / "index"))
        except Exception as error:
            # Cache the failure: the build is deterministic for this content
            # hash, so every retry would pay the full build just to fail again.
            shutil.rmtree(build_dir, ignore_errors=True)
            _INDEXES[key] = error
            raise
        (build_dir / "build-complete").touch()
        try:
            os.rename(build_dir, index_dir)
        except OSError:
            if not (index_dir / "build-complete").exists():  # leftover of a crashed build — replace it
                shutil.rmtree(index_dir, ignore_errors=True)
                os.rename(build_dir, index_dir)
            else:                                            # a concurrent build won — use its index
                shutil.rmtree(build_dir)

    _INDEXES[key] = _qlever.QleverIndex(str(index_dir / "index"))
    return _INDEXES[key]


def _build_index(table, rdf_map, basename):
    """Feed the triplet columns to qlever's index builder directly — zero-copy
    Arrow batches into an injected parser, no RDF text round-trip. The term
    mapping is the N-Quads export rules, applied on the C++ side from the
    flattened schema maps (build_key_metadata stays the single source of
    truth for rdf_map interpretation)."""
    enum_keys, key_namespaces, key_datatypes = \
        build_key_metadata(rdf_map) if rdf_map else (set(), {}, {})
    logger.debug("building qlever index from %d arrow rows", table.num_rows)
    _qlever.build_index_from_arrow(table.to_batches(), basename,
                                   enum_keys, key_namespaces, key_datatypes, CIM_NS)


def _query_form(query_string):
    match = _QUERY_FORM.match(_PROLOGUE.sub("", query_string, count=1))
    return match.group(1).lower() if match else "select"


def _terms_to_triplets(batch):
    """CONSTRUCT/DESCRIBE term columns (N-Triples-form, decoded on the C++
    side) → triplet DataFrame via the shared term conversion (the inverse of
    the N-Quads export conventions, see parser.nquads.terms_to_triplets)."""
    frame = batch.to_pandas(types_mapper=pandas.ArrowDtype)
    frame.columns = ["ID", "KEY", "VALUE"]
    return terms_to_triplets(frame)
