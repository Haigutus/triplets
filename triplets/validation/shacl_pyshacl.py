"""SHACL reference engine — pyshacl (spec-complete, rdflib-based).

Correctness-first reference; the data is loaded into an in-memory rdflib graph
via the N-Quads export. Consumes ``CompiledShapes.graph`` (shapes are parsed
once by the IR compiler); the constraint table is for the vectorized engines.
"""
import logging

from importlib.util import find_spec

from .._rdflib_loader import load_dataset, scoped_graph
from .shacl_report import report_to_violations

if find_spec("pyshacl") is None:  # registry contract: an unavailable engine fails at import
    raise ImportError("pyshacl is not installed")

logger = logging.getLogger(__name__)


def validate(data, compiled, rdf_map=None, scope=None, inference="none",
             advanced=True, abort_on_first=False, store="memory", **kwargs):
    """Validate triplet data against compiled shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    compiled : CompiledShapes
        From ``triplets.validation.compile`` — this engine uses the shapes graph.
    rdf_map : dict or str, optional
        Export schema — xsd-typed literals in the data graph (optional).
    scope : iterable of INSTANCE_ID, optional
        Validate only these instances' named graphs — data outside the scope
        is not loaded, so references into unscoped instances count as absent.
        Include dependency instances in the scope (or validate the full
        union, scope=None) for cross-instance checks.
    inference, advanced, abort_on_first : passed to pyshacl.validate.
    **kwargs : other engines' options (components, max_workers, table_name) —
        accepted and ignored, per the shared engine contract.
    store : str, default "memory"
        rdflib store backend for the data graph (see ``load_dataset``):
        "oxigraph" loads through the oxigraph engine's cached store
        (Rust N-Quads parse instead of rdflib's Python parser); "auto" picks
        it when pyoxigraph + oxrdflib are installed. Measured (2026-07-12):
        results are identical, but memory stays the default — pyshacl
        force-clones the data graph into rdflib Memory regardless
        (advanced=True), and the clone through the oxrdflib wrapper costs
        more than the Rust parse saves (95k warm: 1.8 s vs 1.2 s; 1.14M
        load+clone: 40 s vs 33 s). Opt in when the store is already loaded
        for SPARQL anyway — then the load leg is free.
    """
    if compiled.graph is None:
        raise ValueError("schema-compiled (rdfs) shapes carry no SHACL graph — "
                         "the pyshacl engine cannot run them; use polars/pandas/duckdb")

    from pyshacl import validate as pyshacl_validate

    data_graph = scoped_graph(load_dataset(data, rdf_map=rdf_map, store=store), scope)

    conforms, report_graph, _report_text = pyshacl_validate(
        data_graph, shacl_graph=compiled.graph,
        inference=inference, advanced=advanced, abort_on_first=abort_on_first,
    )
    logger.debug("SHACL conforms=%s", conforms)
    return report_to_violations(report_graph)
