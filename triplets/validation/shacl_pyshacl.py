"""SHACL reference engine — pyshacl (spec-complete, rdflib-based).

Correctness-first reference; the data is loaded into an in-memory rdflib graph
via the N-Quads export. Consumes ``CompiledShapes.graph`` (shapes are parsed
once by the IR compiler); the constraint table is for the vectorized engines.
"""
import logging

from .._rdflib_loader import load_dataset, scoped_graph
from .shacl_report import report_to_violations

logger = logging.getLogger(__name__)


def validate(data, compiled, rdf_map=None, scope=None, inference="none",
             advanced=True, abort_on_first=False):
    """Validate triplet data against compiled shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    compiled : CompiledShapes
        From ``triplets.validation.compile`` — this engine uses the shapes graph.
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph (optional).
    scope : iterable of INSTANCE_ID, optional
        Validate only these instances (named graphs); all data stays loaded for
        reference resolution. None = full union (all profiles).
    inference, advanced, abort_on_first : passed to pyshacl.validate.
    """
    from pyshacl import validate as pyshacl_validate

    data_graph = scoped_graph(load_dataset(data, rdf_map=rdf_map), scope)

    conforms, report_graph, _report_text = pyshacl_validate(
        data_graph, shacl_graph=compiled.graph,
        inference=inference, advanced=advanced, abort_on_first=abort_on_first,
    )
    logger.debug("SHACL conforms=%s", conforms)
    return report_to_violations(report_graph)
