"""SPARQL performance engine — embedded qlever (C++), no server.

Uses qlever's official embedding facade (src/libqlever) through the
triplets.sparql._qlever Cython extension (build: setup_qlever.py).

Flavor-blind by construction: the input (pandas / polars DataFrame or a DuckDB
connection) carries everything as registered methods — ``content_hash`` keys
the engine state, ``filter_triplets`` applies scope, ``export_to_nquads``
feeds the index build — so this module never inspects input types.

Engine state = one on-disk qlever index per content key (data + rdf_map),
loaded engines cached in-process; queries then run in-process returning
standard SPARQL 1.1 JSON (SELECT/ASK) or Turtle (CONSTRUCT/DESCRIBE). The
N-Quads export runs only when an index must actually be built, and streams
through a memfd on Linux (no filesystem round-trip). The index directory is
``$TRIPLETS_QLEVER_DIR`` (point it at /dev/shm for RAM-backed indexes) or the
temp dir; loaded index files are memory-mapped, so hot pages live in the OS
page cache either way. The GIL is released during queries: Python threads
parallelize, no fork needed.

Benchmarked 3.5–216x faster than the alternatives on CGMES data; index build
~2.3 s per 892k triples, index load from disk ~4 ms.
"""
import os
import re
import json
import logging
import hashlib
import tempfile

from pathlib import Path

import pandas

from . import _qlever  # ImportError here → the registry falls back to rdflib

logger = logging.getLogger(__name__)

# qlever writes its own INFO log to stdout — keep it quiet unless triplets
# debugging is on (re-enable per-process with _qlever.set_quiet(False))
_qlever.set_quiet(not logger.isEnabledFor(logging.DEBUG))

_INDEXES = {}    # content hash → loaded _qlever.QleverIndex
_XSD = "http://www.w3.org/2001/XMLSchema#"
_NUMERIC = {f"{_XSD}{name}" for name in ("integer", "int", "long", "short", "byte",
                                         "decimal", "float", "double",
                                         "nonNegativeInteger", "positiveInteger")}
_QUERY_FORM = re.compile(r"\b(select|ask|construct|describe)\b", re.IGNORECASE)


def query(data, query_string, rdf_map=None, scope=None, return_type="pandas"):
    """Execute query_string over data; shape the result by query type
    (same shapes as the rdflib engine).

    Queries are executed exactly as given — no fixing/rewriting. qlever's
    parser is strict; a rejected query raises ValueError carrying qlever's
    message plus the query text, so the failure is directly actionable
    (broken constraint queries belong upstream, see TODO.md).
    """
    index = _index_for(data, rdf_map, scope)
    form = _query_form(query_string)

    if form in ("construct", "describe"):
        return _turtle_to_triplets(_run(index, query_string, "turtle"))

    import json
    result = json.loads(_run(index, query_string, "sparqljson"))
    if form == "ask":
        return bool(result["boolean"])
    return _select_to_dataframe(result)


def _run(index, query_string, media_type):
    try:
        return index.query(query_string, media_type)
    except RuntimeError as error:                          # qlever parse/execution error
        raise ValueError(f"qlever rejected the query: {error}\n"
                         f"--- query ---\n{query_string.strip()[:2000]}") from error


def _index_for(data, rdf_map, scope):
    """Load (or build) the engine state for this exact data + schema + scope.

    Everything goes through the flavor-registered methods: scope via
    filter_triplets (semi-join), identity via content_hash, index input via
    export_to_nquads — executed only on a cache miss.
    """
    if scope is not None:
        data = data.filter_triplets(INSTANCE_ID=list(scope))
        if hasattr(data, "df"):        # duckdb relation — materialize to hash/export
            data = data.df()
    key = _content_key(data, rdf_map)

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


def _content_key(data, rdf_map):
    """content_hash of exactly what gets indexed (all four columns, nothing
    ignored — the key must match the indexed content, or queries against
    ignored triples would answer from another dataset's index), mixed with
    the export schema and a format-version salt."""
    content = data.content_hash(ignore_types=(), columns=("ID", "KEY", "VALUE", "INSTANCE_ID"))
    if isinstance(rdf_map, (str, os.PathLike)):
        with open(rdf_map, "rb") as file:
            schema = file.read()
    else:
        schema = json.dumps(rdf_map, sort_keys=True, default=str).encode() if rdf_map else b""
    return hashlib.sha256(b"triplets-qlever-1" + content.encode() + schema).hexdigest()[:24]


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


def _select_to_dataframe(result):
    """SPARQL 1.1 JSON SELECT result → DataFrame (columns = projected vars)."""
    variables = result.get("head", {}).get("vars", [])
    rows = [[_term_to_py(binding.get(variable)) for variable in variables]
            for binding in result.get("results", {}).get("bindings", [])]
    return pandas.DataFrame(rows, columns=variables)


def _term_to_py(term):
    """SPARQL JSON term → python value; typed literals keep their xsd-mapped type."""
    if term is None:
        return None
    value = term.get("value")
    if term.get("type") != "literal":
        return str(value)                      # uri / bnode
    datatype = term.get("datatype")
    if datatype in _NUMERIC:
        number = float(value)
        return int(number) if number.is_integer() and "float" not in datatype \
            and "double" not in datatype and "decimal" not in datatype else number
    if datatype == f"{_XSD}boolean":
        return value == "true"
    return value


def _turtle_to_triplets(turtle):
    """CONSTRUCT/DESCRIBE Turtle output → triplet DataFrame, via the shared
    rdflib conventions (urn:uuid: stripped, CIM shortened, rdf:type → Type)."""
    import rdflib

    from .sparql_rdflib import _graph_to_triplets

    graph = rdflib.Graph()
    graph.parse(data=turtle, format="turtle")
    return _graph_to_triplets(graph)
