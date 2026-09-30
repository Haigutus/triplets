"""N-Quads export using polars — lazy expression plan, fully vectorized."""

import logging
from io import BytesIO

import polars as pl

from ..iri import DESCRIPTION_TYPE, TRIPLETS_NS, TYPE_KEY, datatypes, iri_polars, load_rdf_map, namespaces, value_types

logger = logging.getLogger(__name__)


def _quads(data, maps, export_undefined=True, undefined_namespace=TRIPLETS_NS):
    """Triplets frame → collected quads frame [s, p, o, g, end].

    The shared formatting core: the whole-frame export and the per-batch
    streaming writer run the same expression plan. Every output line depends
    only on its own row plus the static schema metadata, which is what makes
    N-Quads chunkable. Term rules come from ``triplets.iri``; only the
    ``<>`` / literal quoting happens here."""
    escaped = (pl.col("VALUE")
               .str.replace_all("\\", "\\\\", literal=True)
               .str.replace_all('"', '\\"', literal=True)
               .str.replace_all("\n", "\\n", literal=True)
               .str.replace_all("\r", "\\r", literal=True))
    plain_literal = pl.format('"{}"', escaped)

    kind, payload = iri_polars.absolute_value("KEY", "VALUE", undefined_namespace=undefined_namespace, **maps)
    subject = pl.format("<{}>", iri_polars.absolute_id("ID"))
    predicate = pl.format("<{}>", iri_polars.absolute_key("KEY", maps["namespaces"], undefined_namespace))
    objects = (pl.when(pl.col("_kind") == "iri").then(pl.format("<{}>", pl.col("_payload")))
               .when(pl.col("_payload").is_not_null())
               .then(pl.format('"{}"^^<{}>', escaped, pl.col("_payload")))
               .otherwise(plain_literal))
    # a null INSTANCE_ID writes no graph term (an N-Triples line), same as the pandas writer
    graph = (pl.when(pl.col("INSTANCE_ID").is_null()).then(pl.lit("."))
             .otherwise(pl.format("<{}> .", iri_polars.absolute_id("INSTANCE_ID"))))

    # one lazy plan: stringify (KEY/INSTANCE_ID may be Categorical), filter
    # null VALUE rows, materialize the value classification once (its
    # conditions would otherwise be re-evaluated per when-branch), build the
    # four quad terms (the graph column carries the "." terminator) as
    # separate columns, collect once.
    # The aliases are required (not cosmetic): several terms auto-name to
    # "literal" and collide in select otherwise.
    return (data.lazy()
            .with_columns(pl.col("ID", "KEY", "VALUE", "INSTANCE_ID").cast(pl.Utf8))
            .filter(pl.col("VALUE").is_not_null())
            # Type "Description" is an rdf:Description element — not a class, so no rdf:type
            .filter(~((pl.col("KEY") == TYPE_KEY) & (pl.col("VALUE") == DESCRIPTION_TYPE)))
            .filter(pl.lit(True) if export_undefined
                    else iri_polars.defined("KEY", "VALUE", maps["namespaces"], maps["value_types"]))
            .with_columns(kind.alias("_kind"), payload.alias("_payload"))
            .select(subject.alias("s"), predicate.alias("p"), objects.alias("o"),
                    graph.alias("g"))
            .collect())


def _maps(rdf_map):
    """The three flat schema maps the term rules take, built once per export."""
    rdf_map = load_rdf_map(rdf_map)
    return dict(namespaces=namespaces(rdf_map), value_types=value_types(rdf_map), datatypes=datatypes(rdf_map))


def export_to_nquads(data, path=None, rdf_map=None, export_to_memory=False, export_undefined=True,
                     undefined_namespace=TRIPLETS_NS, prefixes=None):
    """Export triplet DataFrame to N-Quads file.

    Parameters
    ----------
    data : polars.DataFrame
        Triplet dataset with columns [ID, KEY, VALUE, INSTANCE_ID].
    path : str, optional
        Output file path (.nq). Ignored when export_to_memory=True.
    rdf_map : dict or str, optional
        Export schema for proper enum/association detection and literal
        datatype annotations ("400"^^<...XMLSchema#float>).
    export_to_memory : bool, default False
        If True, return an in-memory BytesIO (with .name) instead of writing to disk.
    export_undefined : bool, default True
        Keep rows the schema does not account for (unknown class, KEY or enum
        value — every row without a schema); False drops them.
    undefined_namespace : str, default "http://triplets#"
        Namespace those names are written in.
    """
    maps = _maps(rdf_map)
    data = iri_polars.to_schema_form(data, maps["namespaces"], maps["value_types"], prefixes)   # any iri_form
    quads = _quads(data, maps, export_undefined, undefined_namespace)

    # write straight from Rust with a space separator — the CSV writer joins
    # the columns into "<s> <p> <o> <g> ." per row. No Python string
    # materialization, no outer pl.format (~2.3x faster than collect → to_list
    # → "\n".join). quote_style="never" keeps each term verbatim (literals
    # carry their own quotes / internal spaces, no CSV escaping wanted).
    if export_to_memory:
        buffer = BytesIO()
        quads.write_csv(buffer, include_header=False, quote_style="never", separator=" ")
        buffer.name = "export.nq"
        buffer.seek(0)
        return buffer

    quads.write_csv(path, include_header=False, quote_style="never", separator=" ")


def write_nquads_batches(reader, handle, rdf_map=None, export_undefined=True, undefined_namespace=TRIPLETS_NS,
                         prefixes=None):
    """Stream a ``pyarrow.RecordBatchReader`` into an open binary handle.

    One batch is formatted and written at a time, so memory stays bounded by
    a single batch regardless of the total size — the out-of-core export
    counterpart of ``parse_batches``. The schema metadata is resolved once.
    """
    maps = _maps(rdf_map)
    for batch in reader:
        frame = iri_polars.to_schema_form(pl.from_arrow(batch), maps["namespaces"], maps["value_types"], prefixes)
        _quads(frame, maps, export_undefined, undefined_namespace).write_csv(
            handle, include_header=False, quote_style="never", separator=" ")
