"""SPARQL performance engine — embedded qlever (C++), no server.

Uses qlever's official embedding facade (src/libqlever) through the
triplets.sparql._qlever Cython extension (build: setup_qlever.py).

Flavor-blind by construction: the input (pandas / polars DataFrame or a DuckDB
connection) carries everything as registered methods — ``content_hash`` keys
the engine state, ``export_to_nquads`` feeds the index build — so this module
never inspects input types. Scope is not a data operation: it becomes SPARQL
dataset (FROM) clauses over the per-INSTANCE_ID named graphs, so one index
serves every scope.

Data results follow the read_rdf pattern: all heavy lifting happens on the
C++ side — the wrapper decodes the query result straight into Arrow string
buffers (unbound → null), the Cython layer wraps them zero-copy, and this
module only maps conventions and finalizes the output flavor (``return_type``:
"auto" matches the input — polars in, polars out; explicit "pandas" /
"polars" / "arrow" also accepted). All values are strings (triplets are
all-string; consumers cast); ASK stays a bool via the tiny JSON path.

Engine state = one on-disk qlever index per content key (data + rdf_map),
loaded engines cached in-process. The N-Quads export runs only when an index
must actually be built, and streams through a memfd on Linux (no filesystem
round-trip). The index directory is ``$TRIPLETS_QLEVER_DIR`` (point it at
/dev/shm for RAM-backed indexes) or the temp dir; loaded index files are
memory-mapped, so hot pages live in the OS page cache either way. The GIL is
released during queries and decoding: Python threads parallelize, no fork
needed.

Benchmarked 3.5–216x faster than the alternatives on CGMES data; index build
~2.3 s per 892k triples, index load from disk ~4 ms.
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
from . import content_key
from .._engine_detect import is_polars
from ..export.nquads_utils import CIM_NS, RDF_TYPE

logger = logging.getLogger(__name__)

_UUID_PREFIX = "urn:uuid:"

# qlever writes its own INFO log to stdout — keep it quiet unless triplets
# debugging is on (re-enable per-process with _qlever.set_quiet(False))
_qlever.set_quiet(not logger.isEnabledFor(logging.DEBUG))

_INDEXES = {}    # content hash → loaded _qlever.QleverIndex
_QUERY_FORM = re.compile(r"\b(select|ask|construct|describe)\b", re.IGNORECASE)


def query(data, query_string, rdf_map=None, scope=None, return_type="auto"):
    """Execute query_string over data; shape the result by query type.

    Queries are executed exactly as given — no fixing/rewriting. qlever's
    parser is strict; a rejected query raises ValueError carrying qlever's
    message plus the query text, so the failure is directly actionable
    (broken constraint queries belong upstream, see TODO.md). The one
    exception is ``scope``, which by definition adjusts the query: it becomes
    SPARQL dataset (FROM) clauses, so the one index serves every scope.
    """
    index = _index_for(data, rdf_map)
    query_string = _scoped(query_string, scope)
    form = _query_form(query_string)
    if return_type == "auto":
        return_type = "polars" if is_polars(data) else "pandas"

    if form == "ask":
        return bool(json.loads(_run(index.query, query_string, "sparqljson"))["boolean"])
    if form in ("construct", "describe"):
        return _finalize(_terms_to_triplets(_run(index.construct_arrow, query_string)), return_type)
    return _finalize(_run(index.select_arrow, query_string), return_type)


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


def _scoped(query_string, scope):
    """Scope as SPARQL dataset clauses: qlever's default graph is the union of
    all graphs and FROM restricts it to the scoped instances' named graphs —
    the same union semantics as rdflib's scoped_graph, on the one shared index
    (no per-scope index builds). Dataset clauses belong between the query form
    and its pattern, so they are injected before WHERE (or the pattern's
    opening brace when WHERE is omitted). A query carrying its own FROM is
    refused: SPARQL unions dataset clauses, so adding more would silently
    broaden the scope instead of narrowing it."""
    if scope is None:
        return query_string
    if re.search(r"\bFROM\b", query_string, re.IGNORECASE):
        raise ValueError("scope cannot be applied to a query that has its own FROM clause:\n"
                         f"--- query ---\n{query_string.strip()[:2000]}")
    clauses = " ".join(f"FROM <urn:uuid:{instance}>" for instance in scope)
    anchor = re.search(r"\bWHERE\b", query_string, re.IGNORECASE) or re.search(r"\{", query_string)
    position = anchor.start() if anchor else len(query_string)
    return f"{query_string[:position]}{clauses} {query_string[position:]}"


def _index_for(data, rdf_map):
    """Load (or build) the engine state for this exact data + schema.

    Everything goes through the flavor-registered methods: identity via
    content_hash, index input via export_to_nquads — executed only on a
    cache miss. Scope does not key the state (see _scoped).
    """
    key = content_key(data, rdf_map, b"triplets-qlever-1")

    if key in _INDEXES:
        return _INDEXES[key]

    index_dir = Path(os.environ.get("TRIPLETS_QLEVER_DIR", tempfile.gettempdir())) \
        / "triplets-qlever" / key
    basename = str(index_dir / "index")
    if not (index_dir / "build-complete").exists():
        index_dir.mkdir(parents=True, exist_ok=True)
        buffer = data.export_to_nquads(rdf_map=rdf_map, export_to_memory=True)
        buffer.seek(0)
        _build_index(buffer.read(), basename, index_dir)
        (index_dir / "build-complete").touch()

    _INDEXES[key] = _qlever.QleverIndex(basename)
    return _INDEXES[key]


def _build_index(nquads, basename, index_dir):
    """Feed the N-Quads to qlever's index builder — via an in-memory file
    (memfd, Linux) when possible, else a transient file in the index dir."""
    try:
        fd = os.memfd_create("triplets-qlever.nq")
    except (AttributeError, OSError):
        source = index_dir / "data.nq"
        source.write_bytes(nquads)
        logger.debug("building qlever index in %s (%d bytes via temp file)", index_dir, len(nquads))
        _qlever.build_index(str(source), basename, filetype="nq")
        source.unlink()
        return
    try:
        os.write(fd, nquads)
        logger.debug("building qlever index in %s (%d bytes via memfd)", index_dir, len(nquads))
        _qlever.build_index(f"/proc/self/fd/{fd}", basename, filetype="nq")
    finally:
        os.close(fd)


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
