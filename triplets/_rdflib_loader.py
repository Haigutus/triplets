"""Shared rdflib loading for the SPARQL and SHACL reference engines.

Both engines turn triplet data into an in-memory rdflib graph by going through
the existing N-Quads export (datatype-annotated, INSTANCE_ID as named graph).
No temp files: the export is taken in memory as a BytesIO and parsed directly.
"""
import logging

logger = logging.getLogger(__name__)


def load_dataset(data, rdf_map=None):
    """Triplet data (any flavor) → rdflib.Dataset with named graphs per INSTANCE_ID.

    Parameters
    ----------
    data : pandas/polars DataFrame, pyarrow Table/RecordBatch, or DuckDB connection
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    rdf_map : dict or str, optional
        Export schema — enables correct xsd datatypes / enum namespaces in the
        loaded graph. Optional (schema-optional principle): works without it.

    Returns
    -------
    rdflib.Dataset
        default_union=True so queries/validation see the union across the
        per-INSTANCE_ID named graphs.
    """
    import rdflib
    from .export import export_to_nquads

    data = _to_loadable(data)
    buffer = export_to_nquads(data, rdf_map=rdf_map, export_to_memory=True)
    buffer.seek(0)

    dataset = rdflib.Dataset(default_union=True)
    dataset.parse(source=buffer, format="nquads")
    logger.debug("loaded rdflib Dataset: %d quads", len(dataset))
    return dataset


def _to_loadable(data):
    """export_to_nquads handles pandas/polars; convert arrow/duckdb to pandas first."""
    module = type(data).__module__
    if module.startswith("pyarrow"):
        return data.to_pandas(types_mapper=__import__("pandas").ArrowDtype)
    if module.startswith(("duckdb", "_duckdb")):
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
