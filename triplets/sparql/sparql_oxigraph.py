"""SPARQL engine — pyoxigraph (embedded Rust oxigraph, in-memory store).

The portable performance engine: a plain pip wheel, no compiled extension,
Rust-speed queries. Sits between qlever (fastest, needs the compiled
extension) and rdflib (pure-Python reference) in auto preference.

Data is loaded through the N-Quads export (datatype-annotated, INSTANCE_ID as
named graph) via Store.bulk_load — loaded stores are cached in-process by
content key, same logic as the qlever engine's index cache. After loading,
the deduplicated union of the named graphs is projected into the store's
default graph, so unscoped queries match rdflib's default_union set semantics
(oxigraph's own union of named graphs keeps one solution per graph).

Scope is not a data operation: the scoped instances' named graphs are passed
to Store.query as SPARQL-protocol default graphs, so one store serves every
scope and the query text is never modified. Caveat: a multi-instance scope is
a protocol dataset union — a triple present in several scoped instances
yields one solution per instance (DISTINCT dedupes; rdflib and the unscoped
default graph deduplicate by construction).

Results follow the engine contract shared with qlever — all values are
lexical strings (triplets are all-string; consumers cast):

- SELECT decodes via oxigraph's SPARQL-CSV serializer (lexical forms: IRIs
  bare, literals unquoted; unbound → null, indistinguishable from an
  empty-string literal — the W3C CSV-results tradeoff) straight into
  pandas/polars, no per-term Python loop.
- CONSTRUCT/DESCRIBE serializes to N-Quads (Rust-side) and comes back
  through read_nquads — the same round-trip that loaded the data.
- ASK stays a bool.
"""
import io
import logging

import pandas
import pyoxigraph  # ImportError here → the registry falls back to rdflib

from pyoxigraph import NamedNode, QueryResultsFormat, RdfFormat

from .._content_key import content_key
from .._engine_detect import is_polars
from .._rdflib_loader import _to_loadable
from ..export import export_to_nquads
from ..parser.nquads import read_nquads

logger = logging.getLogger(__name__)

_STORES = {}    # content hash → loaded in-memory pyoxigraph.Store


def query(data, query_string, rdf_map=None, scope=None, return_type="auto", data_unchanged=False):
    """Execute query_string over data; shape the result by query type.

    Queries are executed exactly as given — the text is never modified.
    A rejected query raises ValueError carrying oxigraph's message plus the
    query text, so the failure is directly actionable. ``scope`` travels
    beside the query as SPARQL-protocol default graphs: the query runs
    against exactly the union of the scoped instances' named graphs on the
    one shared store, and per the protocol these take precedence over any
    FROM inside the query. None → the store's default graph (the
    deduplicated union of all named graphs).
    """
    store = _store_for(data, rdf_map, data_unchanged)
    graphs = [NamedNode(f"urn:uuid:{instance}") for instance in scope] if scope is not None else None
    if return_type == "auto":
        return_type = "polars" if is_polars(data) else "pandas"

    result = _run(store, query_string, graphs)
    if isinstance(result, pyoxigraph.QueryBoolean):
        return bool(result)
    if isinstance(result, pyoxigraph.QueryTriples):
        return read_nquads(result.serialize(format=RdfFormat.N_QUADS), return_type=return_type)
    return _select_frame(result.serialize(format=QueryResultsFormat.CSV), return_type)


def _run(store, query_string, default_graph):
    try:
        return store.query(query_string, default_graph=default_graph)
    except (SyntaxError, ValueError, OSError) as error:   # oxigraph parse/execution error
        raise ValueError(f"oxigraph rejected the query: {error}\n"
                         f"--- query ---\n{query_string.strip()[:2000]}") from error


def _store_for(data, rdf_map, data_unchanged=False):
    """Load (or build) the engine state for this exact data + schema.

    Identity via content_hash, load via the N-Quads export — executed only
    on a cache miss. Scope does not key the state (it travels with each
    query as protocol default graphs).
    """
    if not hasattr(data, "content_hash"):  # pyarrow — no registered methods
        data = _to_loadable(data)
    key = content_key(data, rdf_map, b"triplets-oxigraph-1", data_unchanged)
    if key in _STORES:
        return _STORES[key]

    buffer = export_to_nquads(_to_loadable(data), rdf_map=rdf_map, export_to_memory=True)
    buffer.seek(0)
    store = pyoxigraph.Store()   # in-memory
    store.bulk_load(buffer, format=RdfFormat.N_QUADS)
    # default graph := deduplicated union of the named graphs (rdflib
    # default_union set-semantics parity for unscoped queries)
    store.update("INSERT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } }")
    logger.debug("loaded oxigraph store: %d quads", len(store))
    _STORES[key] = store
    return store


def _select_frame(csv_bytes, return_type):
    """SPARQL-CSV → DataFrame, decoded vectorized (no per-term Python loop).
    Lexical forms are the shared all-strings convention; the empty CSV field
    (unbound or empty-string literal) → null."""
    if return_type == "polars":
        import polars
        return polars.read_csv(io.BytesIO(csv_bytes), infer_schema_length=0)
    frame = pandas.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False, na_values=[""])
    if return_type == "arrow":
        import pyarrow
        return pyarrow.Table.from_pandas(frame, preserve_index=False)
    return frame
