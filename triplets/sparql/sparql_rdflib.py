"""SPARQL reference engine — rdflib's built-in SPARQL 1.1.

Correctness-first reference; the data is loaded into an in-memory rdflib
Dataset via the N-Quads export. Faster engines (qlever) come later behind the
same dispatcher.
"""
import logging

import pandas

from .._rdflib_loader import load_dataset, scoped_graph
from ..export.nquads_utils import CIM_NS, RDF_TYPE

logger = logging.getLogger(__name__)

_UUID_PREFIX = "urn:uuid:"


def query(data, query_string, rdf_map=None, scope=None, return_type="pandas"):
    """Execute query_string over data; shape the result by query type."""
    dataset = load_dataset(data, rdf_map=rdf_map)
    graph = scoped_graph(dataset, scope)
    result = graph.query(query_string)

    if result.type == "ASK":
        return bool(result.askAnswer)
    if result.type in ("CONSTRUCT", "DESCRIBE"):
        return _graph_to_triplets(result.graph)
    return _select_to_dataframe(result)


def _select_to_dataframe(result):
    """SELECT result → DataFrame (columns = projected vars, python-typed cells)."""
    variables = list(result.vars)
    columns = [str(v) for v in variables]
    rows = [[_term_to_py(row[v]) for v in variables] for row in result]
    return pandas.DataFrame(rows, columns=columns)


def _term_to_py(term):
    """rdflib term → python value. Literals keep their xsd-mapped type; IRIs → str."""
    if term is None:
        return None
    if type(term).__name__ == "Literal":
        return term.toPython()        # int/float/datetime/str per xsd datatype
    return str(term)                  # URIRef / BNode


def _graph_to_triplets(graph):
    """CONSTRUCT/DESCRIBE result graph → triplet DataFrame (ID/KEY/VALUE).

    Inverse of the N-Quads export conventions: strips urn:uuid: from subjects,
    CIM namespace from predicates, maps rdf:type → 'Type'. INSTANCE_ID is empty
    (a constructed graph has no source instance).
    """
    rows = []
    for subject, predicate, obj in graph:
        rows.append({
            "ID": _strip_uuid(str(subject)),
            "KEY": _shorten_predicate(str(predicate)),
            "VALUE": _shorten_object(obj),
            "INSTANCE_ID": None,
        })
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
