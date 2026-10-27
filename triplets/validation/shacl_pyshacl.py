"""SHACL reference engine — pyshacl (spec-complete, rdflib-based).

Correctness-first reference; the data is loaded into an in-memory rdflib graph
via the N-Quads export. Consumes ``CompiledShapes.graph`` (shapes are parsed
once by the IR compiler); the constraint table is for the vectorized engines.
"""
import logging

from importlib.util import find_spec

from ..iri import CIM_NS
from .._rdflib_loader import load_dataset
from .context import _scoped
from .shacl_ir import _mark_switched_off
from .shacl_report import report_to_violations

if find_spec("pyshacl") is None:  # registry contract: an unavailable engine fails at import
    raise ImportError("pyshacl is not installed")

logger = logging.getLogger(__name__)


def validate(data, compiled, rdf_map=None, data_scope=None, inference="none",
             advanced=True, abort_on_first=False, store="memory", undefined_namespace=CIM_NS,
             ancestors=None, **kwargs):
    """Validate triplet data against compiled shapes; return a violations DataFrame.

    Parameters
    ----------
    data : triplet DataFrame (pandas/polars), arrow, or DuckDB connection
    compiled : CompiledShapes
        From ``triplets.validation.compile`` — this engine uses the shapes graph.
    rdf_map : dict, optional
        Scoped export schema (``schema_scope.scoped_rdf_map``) — xsd-typed
        literals and class IRIs in the data graph.
    ancestors : dict, optional
        ``schema_scope.class_ancestors`` map, given to pyshacl as ``rdfs:subClassOf``
        in ``ont_graph`` (already closed, so pyshacl's own closure adds nothing):
        ``sh:targetClass`` / ``sh:class`` see SHACL instances of ancestors.
        None passes no ``ont_graph``.
    data_scope : iterable of INSTANCE_ID, optional
        Validate only these instances' named graphs — data outside the scope
        is not loaded, so references into unscoped instances count as absent.
        Include dependency instances in the scope (or validate the full
        union, data_scope=None) for cross-instance checks.
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
        for SPARQL anyway — then the load leg is free (unscoped runs only:
        a data_scope run loads its own store of the scoped rows).
    """
    if compiled.graph is None:
        raise ValueError("schema-compiled (rdfs) shapes carry no SHACL graph — "
                         "the pyshacl engine cannot run them; use polars/pandas/duckdb")

    from pyshacl import validate as pyshacl_validate

    if data_scope is not None:   # filter before the export — data outside the scope is never loaded
        from . import _table_ref
        data = _scoped(data, data_scope, _table_ref(data, **kwargs))
    data_graph = load_dataset(data, rdf_map=rdf_map, store=store, undefined_namespace=undefined_namespace)
    ont_graph = _ontology_graph(ancestors, rdf_map, undefined_namespace) if ancestors else None

    shapes_graph = _switched_off_graph(compiled)
    conforms, report_graph, _report_text = pyshacl_validate(
        data_graph, shacl_graph=shapes_graph, ont_graph=ont_graph,
        inference=inference, advanced=advanced, abort_on_first=abort_on_first,
    )
    logger.debug("SHACL conforms=%s", conforms)
    return report_to_violations(report_graph, shapes_graph=shapes_graph)


def _switched_off_graph(compiled):
    """The shapes graph pyshacl validates with: the compiled graph with exactly
    the run's switched-off set marked ``sh:deactivated true``. A copy (prefixes
    included — pyshacl renders its messages with them) only when that set
    differs from the declared one — pyshacl (advanced=True) may mutate the
    graph it is given."""
    import rdflib

    if compiled.deactivated == (compiled.base or compiled).deactivated:
        return compiled.graph
    graph = rdflib.Graph()
    for prefix, namespace in compiled.graph.namespaces():
        graph.bind(prefix, namespace, override=True, replace=True)
    graph += compiled.graph
    return _mark_switched_off(graph, compiled)


def _ontology_graph(ancestors, rdf_map, undefined_namespace):
    """``rdfs:subClassOf`` triples of the ancestor map, as a pyshacl ont_graph.

    Same IRIs as the data graph's ``rdf:type``: one *undefined_namespace* for both.
    The instance data graph is not mutated.
    """
    import rdflib
    from .schema_scope import ancestor_triples

    graph = rdflib.Graph()
    for child, parent in ancestor_triples(ancestors, rdf_map, undefined_namespace):
        graph.add((rdflib.URIRef(child), rdflib.RDFS.subClassOf, rdflib.URIRef(parent)))
    return graph
