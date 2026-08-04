"""N-Quads export using polars — lazy expression plan, fully vectorized."""

import logging
from io import BytesIO

import polars as pl

from .nquads_utils import CIM_NS, RDF_TYPE, UUID_RE, build_key_metadata

logger = logging.getLogger(__name__)

URI_PREFIXES = ("http://", "https://", "urn:")


def _iri_or_uuid(column):
    """<urn:uuid:x> unless the value is already a URI."""
    starts_uri = pl.any_horizontal(*[pl.col(column).str.starts_with(p) for p in URI_PREFIXES])
    return (pl.when(starts_uri)
            .then(pl.format("<{}>", pl.col(column)))
            .otherwise(pl.format("<urn:uuid:{}>", pl.col(column))))


def _quads(data, enum_keys, key_namespaces, key_datatypes):
    """Triplets frame → collected quads frame [s, p, o, g, end].

    The shared formatting core: the whole-frame export and the per-batch
    streaming writer run the same expression plan. Every output line depends
    only on its own row plus the static schema metadata, which is what makes
    N-Quads chunkable."""
    is_type = pl.col("KEY") == "Type"
    val_is_uri = pl.any_horizontal(*[pl.col("VALUE").str.starts_with(p) for p in URI_PREFIXES])
    val_is_uuid = pl.col("VALUE").str.contains(UUID_RE.pattern)
    is_enum = pl.col("KEY").is_in(list(enum_keys)) if enum_keys else pl.lit(False)
    is_literal_by_schema = pl.col("KEY").is_in(list(key_datatypes)) if key_datatypes else pl.lit(False)

    namespace = (pl.col("KEY").replace_strict(key_namespaces, default=CIM_NS, return_dtype=pl.Utf8)
                 if key_namespaces else pl.lit(CIM_NS))
    # split typed (xsd URI) from plain-string (None) schema literals
    typed_map = {k: v for k, v in key_datatypes.items() if v}
    datatype = (pl.col("KEY").replace_strict(typed_map, default=None, return_dtype=pl.Utf8)
                if typed_map else pl.lit(None, dtype=pl.Utf8))

    escaped = (pl.col("VALUE")
               .str.replace_all("\\", "\\\\", literal=True)
               .str.replace_all('"', '\\"', literal=True)
               .str.replace_all("\n", "\\n", literal=True)
               .str.replace_all("\r", "\\r", literal=True))
    plain_literal = pl.format('"{}"', escaped)

    subject = _iri_or_uuid("ID")
    graph = _iri_or_uuid("INSTANCE_ID")
    predicate = (pl.when(is_type).then(pl.lit(f"<{RDF_TYPE}>"))
                 .when(pl.col("KEY").str.starts_with("http://") | pl.col("KEY").str.starts_with("https://"))
                 .then(pl.format("<{}>", pl.col("KEY")))
                 .otherwise(pl.format("<{}{}>", namespace, pl.col("KEY"))))
    objects = (pl.when(is_type & val_is_uri).then(pl.format("<{}>", pl.col("VALUE")))
               .when(is_type).then(pl.format("<{}{}>", pl.lit(CIM_NS), pl.col("VALUE")))
               .when(val_is_uri).then(pl.format("<{}>", pl.col("VALUE")))
               .when(is_enum).then(pl.format("<{}{}>", pl.lit(CIM_NS), pl.col("VALUE")))
               .when(is_literal_by_schema & datatype.is_not_null())
               .then(pl.format('"{}"^^<{}>', escaped, datatype))
               .when(is_literal_by_schema).then(plain_literal)   # xsd:string — plain
               .when(val_is_uuid).then(pl.format("<urn:uuid:{}>", pl.col("VALUE")))
               .otherwise(plain_literal))

    if "context" in data.columns:
        # context frames (parser_rdfxml): the captured term metadata replaces
        # the CIM heuristics wherever it is present; null context rows (meta
        # rows, mixed frames) fall through to the plans above
        ctx = pl.col("context")
        id_prefix = ctx.struct.field("ID_PREFIX")
        key_prefix = ctx.struct.field("KEY_PREFIX")
        value_prefix = ctx.struct.field("VALUE_PREFIX")
        kind = ctx.struct.field("rdf_value_kind")
        language = ctx.struct.field("rdf_language")
        value_datatype = ctx.struct.field("rdf_datatype")
        subject = (pl.when(id_prefix == "_:").then(pl.format("_:{}", pl.col("ID")))
                   .when(id_prefix.is_not_null())
                   .then(pl.format("<{}{}>", id_prefix, pl.col("ID")))
                   .otherwise(subject))
        predicate = (pl.when(is_type).then(pl.lit(f"<{RDF_TYPE}>"))
                     .when(key_prefix.is_not_null())
                     .then(pl.format("<{}{}>", key_prefix, pl.col("KEY")))
                     .otherwise(predicate))
        objects = (pl.when(kind == "blank").then(pl.format("_:{}", pl.col("VALUE")))
                   .when((kind == "iri") & value_prefix.is_not_null())
                   .then(pl.format("<{}{}>", value_prefix, pl.col("VALUE")))
                   .when(kind == "iri").then(pl.format("<{}>", pl.col("VALUE")))
                   .when((kind == "literal") & language.is_not_null())
                   .then(pl.format('"{}"@{}', escaped, language))
                   .when((kind == "literal") & value_datatype.is_not_null())
                   .then(pl.format('"{}"^^<{}>', escaped, value_datatype))
                   .when(kind == "literal").then(plain_literal)
                   .otherwise(objects))

    # one lazy plan: stringify (KEY/INSTANCE_ID may be Categorical), filter
    # null VALUE rows, build the four quad terms + the "." terminator as
    # separate columns, collect once. The aliases are required (not cosmetic):
    # several terms auto-name to "literal" and collide in select otherwise.
    return (data.lazy()
            .with_columns(pl.col("ID", "KEY", "VALUE", "INSTANCE_ID").cast(pl.Utf8))
            .filter(pl.col("VALUE").is_not_null())
            .select(subject.alias("s"), predicate.alias("p"), objects.alias("o"),
                    graph.alias("g"), pl.lit(".").alias("end"))
            .collect())


def _key_metadata(rdf_map):
    return build_key_metadata(rdf_map) if rdf_map else (set(), {}, {})


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
    quads = _quads(data, *_key_metadata(rdf_map))

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
    metadata = _key_metadata(rdf_map)
    for batch in reader:
        _quads(pl.from_arrow(batch), *metadata).write_csv(
            handle, include_header=False, quote_style="never", separator=" ")
