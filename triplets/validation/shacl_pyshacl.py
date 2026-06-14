"""SHACL reference engine — pyshacl (spec-complete, rdflib-based).

Correctness-first reference; the data is loaded into an in-memory rdflib graph
via the N-Quads export. Faster experimental engines (pandas/polars/duckdb)
come later behind the same dispatcher.
"""
import logging

from .._rdflib_loader import load_dataset, scoped_graph
from .shacl_report import report_to_violations

logger = logging.getLogger(__name__)

_SHAPE_FORMATS = {".ttl": "turtle", ".rdf": "xml", ".xml": "xml", ".nt": "nt", ".jsonld": "json-ld"}


def validate(data, shapes, rdf_map=None, scope=None, inference="none",
             advanced=True, abort_on_first=False, return_type="pandas"):
    """Validate triplet data against SHACL shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    shapes : str | path | list of paths | rdflib.Graph
        SHACL shapes. File format auto-detected by extension (.ttl/.rdf/.xml).
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph (optional).
    scope : iterable of INSTANCE_ID, optional
        Validate only these instances (named graphs); all data stays loaded for
        reference resolution. None = full union (all profiles).
    inference, advanced, abort_on_first : passed to pyshacl.validate.
    """
    from pyshacl import validate as pyshacl_validate

    data_graph = scoped_graph(load_dataset(data, rdf_map=rdf_map), scope)
    shapes_graph = _load_shapes(shapes)

    conforms, report_graph, _report_text = pyshacl_validate(
        data_graph, shacl_graph=shapes_graph,
        inference=inference, advanced=advanced, abort_on_first=abort_on_first,
    )
    logger.debug("SHACL conforms=%s", conforms)
    return report_to_violations(report_graph)


def _load_shapes(shapes):
    """str/path | list of paths | rdflib.Graph → one rdflib.Graph of shapes."""
    import rdflib

    if isinstance(shapes, rdflib.Graph):
        return shapes

    paths = [shapes] if isinstance(shapes, (str, bytes)) or hasattr(shapes, "__fspath__") else list(shapes)
    graph = rdflib.Graph()
    for path in paths:
        suffix = str(path)[str(path).rfind("."):].lower()
        graph.parse(str(path), format=_SHAPE_FORMATS.get(suffix, "turtle"))
    return graph
