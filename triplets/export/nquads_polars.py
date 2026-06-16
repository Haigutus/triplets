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
    enum_keys, key_namespaces, key_datatypes = build_key_metadata(rdf_map) if rdf_map else (set(), {}, {})

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
               .str.replace_all("\n", "\\n", literal=True))
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

    # one lazy plan: stringify (KEY/INSTANCE_ID may be Categorical), filter
    # null VALUE rows, build the four quad terms + the "." terminator as
    # separate columns, collect once. The aliases are required (not cosmetic):
    # several terms auto-name to "literal" and collide in select otherwise.
    quads = (data.lazy()
             .with_columns(pl.col("ID", "KEY", "VALUE", "INSTANCE_ID").cast(pl.Utf8))
             .filter(pl.col("VALUE").is_not_null())
             .select(subject.alias("s"), predicate.alias("p"), objects.alias("o"),
                     graph.alias("g"), pl.lit(".").alias("end"))
             .collect())

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
