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
import logging
import tempfile

from pathlib import Path

import pandas
import pyarrow

from . import _qlever  # ImportError here → the registry falls back to rdflib
from .._content_key import content_key
from .._engine_detect import is_polars
from ..export.nquads_utils import CIM_NS, RDF_TYPE, build_key_metadata

logger = logging.getLogger(__name__)

_UUID_PREFIX = "urn:uuid:"

# qlever writes its own INFO log to stdout — keep it quiet unless triplets
# debugging is on (re-enable per-process with _qlever.set_quiet(False))
_qlever.set_quiet(not logger.isEnabledFor(logging.DEBUG))

_INDEXES = {}    # content hash → loaded _qlever.QleverIndex
_QUERY_FORM = re.compile(r"\b(select|ask|construct|describe)\b", re.IGNORECASE)


def query(data, query_string, rdf_map=None, scope=None, return_type="auto",data_unchanged=False):
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
        return_type = "polars" if is_polars(data) else "pandas"

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
    if return_type == "polars":
        import polars
        return polars.from_pandas(result)
    if return_type == "arrow":
        return pyarrow.Table.from_pandas(result)
    return result


def _index_for(data, rdf_map, data_unchanged=False):
    """Load (or build) the engine state for this exact data + schema.

    Everything goes through the flavor-registered methods: identity via
    content_hash, index input via export_to_arrow — executed only on a
    cache miss. Scope does not key the state (it travels with each query
    as dataset clauses).
    """
    key = content_key(data, rdf_map, b"triplets-qlever-2", data_unchanged)

    if key in _INDEXES:
        return _INDEXES[key]

    index_dir = Path(os.environ.get("TRIPLETS_QLEVER_DIR", tempfile.gettempdir())) \
        / "triplets-qlever" / key
    basename = str(index_dir / "index")
    if not (index_dir / "build-complete").exists():
        index_dir.mkdir(parents=True, exist_ok=True)
        _build_index(data.export_to_arrow(), rdf_map, basename)
        (index_dir / "build-complete").touch()

    _INDEXES[key] = _qlever.QleverIndex(basename)
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
    match = _QUERY_FORM.search(query_string)
    return match.group(1).lower() if match else "select"


def _terms_to_triplets(batch):
    """CONSTRUCT/DESCRIBE term columns (N-Triples-form, decoded on the C++
    side) → triplet DataFrame, converted with vectorized string ops. Same
    conventions as the rdflib engine's inverse of the N-Quads export:
    urn:uuid: stripped, CIM namespace shortened, rdf:type → 'Type';
    INSTANCE_ID is empty (a constructed graph has no source instance).
    Term shapes: ``<iri>``, ``_:bnode``, ``"literal"`` (raw value between the
    outer quotes, optionally with a ``^^<datatype>`` / ``@lang`` suffix —
    dropped, the value keeps its lexical form), or bare turtle-shorthand
    numbers/booleans."""
    frame = batch.to_pandas(types_mapper=pandas.ArrowDtype)
    frame.columns = ["ID", "KEY", "VALUE"]

    def iri(column):
        return (column.str.replace(r"^<(.*)>$", r"\1", regex=True)
                .str.removeprefix("_:").str.removeprefix(_UUID_PREFIX).str.removeprefix(CIM_NS))

    rdf_type = frame["KEY"] == f"<{RDF_TYPE}>"
    frame["ID"] = iri(frame["ID"])
    frame["KEY"] = iri(frame["KEY"]).mask(rdf_type, "Type")
    unquoted = frame["VALUE"].str.replace(r'(?s)^"(.*)"(\^\^<[^>]*>|@[\w-]+)?$', r"\1", regex=True)
    frame["VALUE"] = unquoted.where(frame["VALUE"].str.startswith('"'), iri(frame["VALUE"]))
    frame["INSTANCE_ID"] = None
    return frame
