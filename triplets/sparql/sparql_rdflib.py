"""SPARQL reference engine — rdflib's built-in SPARQL 1.1.

Correctness-first reference; the data is loaded into an in-memory rdflib
Dataset via the N-Quads export — cached in-process by content key (see
_rdflib_loader), so the export/parse runs only on a cache miss, same logic
as the qlever engine's index cache. Scope is applied after loading (named
graphs), so one cached dataset serves all scopes.

Results follow the shared engine contract — all SELECT values are lexical
strings (triplets are all-string; consumers cast), decoded through rdflib's
SPARQL-CSV result serializer (measured ~1.5x faster than iterating the
result's term objects, and the same decode the oxigraph engine uses).
rdf_map still matters: typed literals in the loaded graph drive
comparisons/ORDER BY *inside* the query — only the returned representation
is string.
"""
import io
import logging

from importlib.util import find_spec

import pandas

from .._engine_detect import flavor, to_return_type
from .._rdflib_loader import load_dataset, scoped_graph
from ..export.nquads_utils import CIM_NS, RDF_TYPE

if find_spec("rdflib") is None:  # registry contract: an unavailable engine fails at import
    raise ImportError("rdflib is not installed")

logger = logging.getLogger(__name__)

_UUID_PREFIX = "urn:uuid:"


def query(data, query_string, rdf_map=None, scope=None, return_type="auto", data_unchanged=False):
    """Execute query_string over data; shape the result by query type."""
    dataset = load_dataset(data, rdf_map=rdf_map, data_unchanged=data_unchanged)
    graph = scoped_graph(dataset, scope)
    result = graph.query(query_string)
    if return_type == "auto":
        return_type = "polars" if flavor(data) == "polars" else "pandas"

    if result.type == "ASK":
        return bool(result.askAnswer)
    if result.type in ("CONSTRUCT", "DESCRIBE"):
        return to_return_type(_graph_to_triplets(result.graph), return_type)
    return to_return_type(_select_to_dataframe(result), return_type)


def _select_to_dataframe(result):
    """SELECT result → DataFrame (columns = projected vars, lexical strings).

    The SPARQL-CSV serializer streams lexical forms (IRIs bare, literals
    unquoted; unbound → empty field → null) — the shared all-strings
    convention, same decode as the oxigraph engine."""
    payload = result.serialize(format="csv")
    return pandas.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, na_values=[""])


def _graph_to_triplets(graph):
    """CONSTRUCT/DESCRIBE result graph → triplet DataFrame (ID/KEY/VALUE).

    Inverse of the N-Quads export conventions: strips urn:uuid: from subjects,
    CIM namespace from predicates, maps rdf:type → 'Type'. INSTANCE_ID is empty
    (a constructed graph has no source instance).

    Measured: serialize(format="nt") + read_nquads loses to this loop (~9%
    slower per 84k triples — rdflib's NT serializer overhead exceeds the
    vectorized decode gain), so the direct iteration stays.
    """
    rows = []
    for subject, predicate, obj in graph:

        # Tuples are faster than dicts to convert to dataframe
        rows.append(
            (
                _strip_uuid(str(subject)),              # ID
                _shorten_predicate(str(predicate)),     # KEY
                _shorten_object(obj),                   # VALUE
                None,                                   # INSTANCE_ID — constructed graph has no source instance
            )
        )

    # return_type conversion happens in _finalize
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


def _strip_uuid(value):
    return value[len(_UUID_PREFIX):] if value.startswith(_UUID_PREFIX) else value


def _shorten_predicate(predicate):
    if predicate == RDF_TYPE:
        return "Type"
    if predicate.startswith(CIM_NS):
        return predicate[len(CIM_NS):]
    return predicate


def _shorten_object(obj):
    if type(obj).__name__ == "Literal":
        return str(obj)
    value = str(obj)
    if value.startswith(_UUID_PREFIX):
        return value[len(_UUID_PREFIX):]
    if value.startswith(CIM_NS):
        return value[len(CIM_NS):]
    return value
