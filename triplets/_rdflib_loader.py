"""Shared rdflib loading for the SPARQL and SHACL reference engines.

Both engines turn triplet data into an in-memory rdflib graph by going through
the existing N-Quads export (datatype-annotated, INSTANCE_ID as named graph).
No temp files: the export is taken in memory as a BytesIO and parsed directly.

Loaded datasets are cached in-process keyed by content (same logic as the
qlever engine's index cache): the export/parse runs only on a cache miss.
Consumers must treat the returned dataset as read-only — scope is applied
after loading (``scoped_graph``), so one cached dataset serves all scopes.
"""
import logging

from ._caches import register_cache
from ._content_key import content_key
from ._engine_detect import flavor

logger = logging.getLogger(__name__)

_DATASETS = register_cache({})   # content key → loaded rdflib.Dataset


def load_dataset(data, rdf_map=None, data_unchanged=False, store="memory"):
    """Triplet data (any flavor) → rdflib.Dataset with named graphs per INSTANCE_ID.

    Parameters
    ----------
    data : pandas/polars DataFrame, pyarrow Table/RecordBatch, or DuckDB connection
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    rdf_map : dict or str, optional
        Export schema — enables correct xsd datatypes / enum namespaces in the
        loaded graph. Optional (schema-optional principle): works without it.
    data_unchanged : bool, default False
        Assert the data object is unmutated since last hashed — reuses the
        stored content digest for this exact object (see _content_key).
    store : str, default "memory"
        rdflib store backend: "memory" (default rdflib Memory store),
        "oxigraph" (the SPARQL engine's cached in-memory pyoxigraph store
        wrapped via oxrdflib — Rust N-Quads parse, shared with the oxigraph
        engine so one bulk_load serves both), or "auto" (oxigraph when
        pyoxigraph + oxrdflib are importable, else memory).

    Returns
    -------
    rdflib.Dataset
        Queries/validation see the deduplicated union across the
        per-INSTANCE_ID named graphs (memory: default_union=True; oxigraph:
        the store's projected default graph). Treat as read-only (cached,
        shared — the oxigraph store is also the SPARQL engine's).
    """
    import rdflib
    from .export import export_to_nquads

    if isinstance(data, rdflib.Graph):  # incl. Dataset — already loaded, reuse as-is
        return data

    if not hasattr(data, "content_hash"):  # pyarrow — no registered methods
        data = _to_loadable(data)
    backend = _resolve_store(store)
    key = backend + ":" + content_key(data, rdf_map, b"triplets-rdflib-1", data_unchanged)
    if key in _DATASETS:
        return _DATASETS[key]

    if backend == "oxigraph":
        from oxrdflib import OxigraphStore
        from .sparql.sparql_oxigraph import _store_for
        # The engine's shared store already carries the deduplicated union of
        # the named graphs in its *default graph* (the load-time projection),
        # so the wrapper must NOT union again (default_union=True would count
        # each triple once per graph — oxigraph union keeps per-graph
        # solutions). default_union=False exposes exactly the projected
        # union, with the named graphs still reachable for scoped_graph.
        dataset = rdflib.Dataset(store=OxigraphStore(store=_store_for(data, rdf_map, data_unchanged)),
                                 default_union=False)
    else:
        buffer = export_to_nquads(_to_loadable(data), rdf_map=rdf_map, export_to_memory=True)
        buffer.seek(0)
        dataset = rdflib.Dataset(default_union=True)
        dataset.parse(source=buffer, format="nquads")
    logger.debug("loaded rdflib Dataset (%s): %d quads", backend, len(dataset))
    _DATASETS[key] = dataset
    return dataset


def _resolve_store(store):
    if store == "auto":
        from importlib.util import find_spec
        available = find_spec("oxrdflib") is not None and find_spec("pyoxigraph") is not None
        return "oxigraph" if available else "memory"
    if store not in ("memory", "oxigraph"):
        raise ValueError(f"Unknown rdflib store backend: {store}. Known: memory, oxigraph, auto")
    return store


def _to_loadable(data):
    """export_to_nquads handles pandas/polars; convert arrow/duckdb to pandas first."""
    kind = flavor(data)
    if kind == "pyarrow":
        import pandas
        return data.to_pandas(types_mapper=pandas.ArrowDtype)
    if kind == "duckdb":
        return data.execute("SELECT * FROM triplets").df()
    return data  # pandas / polars — export_to_nquads takes these directly


def scoped_graph(dataset, scope=None):
    """Return the graph to query/validate: full union, or just the scoped instances.

    Parameters
    ----------
    dataset : rdflib.Dataset
    scope : iterable of INSTANCE_ID (str), optional
        When given, return a concrete Graph holding the union of those
        instances' named graphs (the quad's graph component does the
        filtering). A concrete Graph — not a view — is required because
        pyshacl clones/iterates the data graph and does not read a
        ReadOnlyGraphAggregate. The copy is only the reduced scope, which is
        the point of scoping. When None, the full default-union dataset is used.
    """
    if scope is None:
        return dataset

    import rdflib

    graph = rdflib.Graph()
    for instance_id in scope:
        graph += dataset.get_context(rdflib.URIRef(f"urn:uuid:{instance_id}"))
    return graph
