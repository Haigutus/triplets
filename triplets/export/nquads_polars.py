"""N-Quads export using polars — lazy expression plan, fully vectorized."""

import logging
from io import BytesIO

import polars as pl

from ..iri import SchemaTerms, iri_polars

logger = logging.getLogger(__name__)


def _quads(data, terms):
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

    kind, payload = iri_polars.expand_value("KEY", "VALUE", terms)
    subject = pl.format("<{}>", iri_polars.expand_id("ID"))
    predicate = pl.format("<{}>", iri_polars.expand_key("KEY", terms))
    objects = (pl.when(pl.col("_kind") == "iri").then(pl.format("<{}>", pl.col("_payload")))
               .when(pl.col("_payload").is_not_null())
               .then(pl.format('"{}"^^<{}>', escaped, pl.col("_payload")))
               .otherwise(plain_literal))
    graph = pl.format("<{}>", iri_polars.expand_id("INSTANCE_ID"))

    # one lazy plan: stringify (KEY/INSTANCE_ID may be Categorical), filter
    # null VALUE rows, materialize the value classification once (its
    # conditions would otherwise be re-evaluated per when-branch), build the
    # four quad terms + the "." terminator as separate columns, collect once.
    # The aliases are required (not cosmetic): several terms auto-name to
    # "literal" and collide in select otherwise.
    return (data.lazy()
            .with_columns(pl.col("ID", "KEY", "VALUE", "INSTANCE_ID").cast(pl.Utf8))
            .filter(pl.col("VALUE").is_not_null())
            .with_columns(kind.alias("_kind"), payload.alias("_payload"))
            .select(subject.alias("s"), predicate.alias("p"), objects.alias("o"),
                    graph.alias("g"), pl.lit(".").alias("end"))
            .collect())


def export_to_nquads(data, path=None, rdf_map=None, export_to_memory=False):
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
    """
    quads = _quads(data, SchemaTerms.from_rdf_map(rdf_map))

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


def write_nquads_batches(reader, handle, rdf_map=None):
    """Stream a ``pyarrow.RecordBatchReader`` into an open binary handle.

    One batch is formatted and written at a time, so memory stays bounded by
    a single batch regardless of the total size — the out-of-core export
    counterpart of ``parse_batches``. The schema metadata is resolved once.
    """
    terms = SchemaTerms.from_rdf_map(rdf_map)
    for batch in reader:
        _quads(pl.from_arrow(batch), terms).write_csv(
            handle, include_header=False, quote_style="never", separator=" ")
