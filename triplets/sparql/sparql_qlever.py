"""SPARQL performance engine — embedded qlever (C++), no server.

Uses qlever's official embedding facade (src/libqlever) through the
triplets.sparql._qlever Cython extension (build: setup_qlever.py). The data is
exported to N-Quads, indexed once on disk — cached by content hash, so
re-querying the same data (or re-running a validation) never rebuilds — and
queries run in-process returning standard SPARQL 1.1 JSON (SELECT/ASK) or
Turtle (CONSTRUCT/DESCRIBE). The GIL is released during queries: Python
threads parallelize, no fork needed.

Benchmarked 3.5–216x faster than the alternatives on CGMES data; index build
~2.3 s per 892k triples, index load from disk ~4 ms.
"""
import re
import logging
import hashlib
import tempfile

from pathlib import Path

import pandas

from . import _qlever  # ImportError here → the registry falls back to rdflib
from .._engine_detect import is_polars
from ..export import export_to_nquads

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
    """Load (or build) the on-disk index for this exact data + schema + scope.

    Content-hash cache: the N-Quads export is hashed, the index lives under
    the temp dir keyed by that hash, and loaded engines are reused in-process.
    """
    if scope is not None:
        data = _filter_scope(data, scope)
    nquads = _to_nquads(data, rdf_map)
    key = hashlib.sha256(nquads).hexdigest()[:24]

    if key in _INDEXES:
        return _INDEXES[key]

    index_dir = Path(tempfile.gettempdir()) / "triplets-qlever" / key
    basename = str(index_dir / "index")
    marker = index_dir / "build-complete"
    if not marker.exists():
        index_dir.mkdir(parents=True, exist_ok=True)
        source = index_dir / "data.nq"
        source.write_bytes(nquads)
        logger.debug("building qlever index %s (%d bytes of N-Quads)", key, len(nquads))
        _qlever.build_index(str(source), basename, filetype="nq")
        source.unlink()
        marker.touch()

    _INDEXES[key] = _qlever.QleverIndex(basename)
    return _INDEXES[key]


def _to_nquads(data, rdf_map):
    """Triplet data (any flavor) or an already-loaded rdflib graph → N-Quads bytes."""
    module = type(data).__module__
    if module.startswith("rdflib"):
        try:
            return data.serialize(format="nquads", encoding="utf-8")
        except Exception:                       # plain Graph — no quad contexts
            return data.serialize(format="nt", encoding="utf-8")
    from .._rdflib_loader import _to_loadable
    buffer = export_to_nquads(_to_loadable(data), rdf_map=rdf_map, export_to_memory=True)
    buffer.seek(0)
    return buffer.read()


def _filter_scope(data, scope):
    instances = [str(instance) for instance in scope]
    if is_polars(data):
        import polars
        return data.filter(polars.col("INSTANCE_ID").cast(polars.Utf8).is_in(instances))
    module = type(data).__module__
    if module.startswith("pyarrow") or module.startswith(("duckdb", "_duckdb")):
        from .._rdflib_loader import _to_loadable
        data = _to_loadable(data)
    return data[data["INSTANCE_ID"].astype(str).isin(instances)]


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
