"""polars flavor of :mod:`triplets.iri` — column name or Expr in, Expr out, same names.

Pure expressions, no Python UDFs. Columns are expected as Utf8 (cast
Categorical first, as the N-Quads exporter does).
"""
import polars

from . import (GUESS_NAME_RE, ID_PREFIX_RE, IRI_ESCAPES, PREFIXED_RE, RDF_TYPE, SPLIT_RE, TRIPLETS_NS, TYPE_KEY,
               URI_PREFIX_RE, UUID_PREFIX, UUID_RE)


def _col(column):
    return polars.col(column) if isinstance(column, str) else column


def _local(expr):
    """split_iri's local name only — one regex replace (the hot path of local_key / local_value)."""
    return expr.str.replace(SPLIT_RE.pattern, "")


def split_iri(column):
    """→ (namespace, local) Exprs; namespace "" where there is no delimiter."""
    expr = _col(column)
    return expr.str.extract(f"({SPLIT_RE.pattern})", 1).fill_null(""), _local(expr)


def local_id(column):
    return _col(column).str.replace(ID_PREFIX_RE.pattern, "")


def local_key(column, type_key=TYPE_KEY):
    expr = _col(column)
    return polars.when(expr == RDF_TYPE).then(polars.lit(type_key)).otherwise(_local(expr))


def local_value(column, kind=None):
    """*kind*: None (guess), one of ``VALUE_KINDS`` for every row, or an Expr of them per row."""
    expr = _col(column)
    if isinstance(kind, str):
        return _local(expr) if kind in ("class", "enum") else local_id(expr)
    guessed = local_id(expr).str.replace(GUESS_NAME_RE.pattern, "")
    if kind is None:
        return guessed
    return (polars.when(kind.is_in(["class", "enum"])).then(_local(expr))
            .when(kind == "reference").then(local_id(expr))
            .otherwise(guessed))


def is_iri(column):
    return _col(column).str.contains(URI_PREFIX_RE.pattern).fill_null(False)   # null → False, as the scalar; regex measured as fast as starts_with


def encode_iri(column):
    return _col(column).str.replace_many(list(IRI_ESCAPES), list(IRI_ESCAPES.values()))   # one Aho-Corasick pass


def decode_iri(column):
    return _col(column).str.replace_many(list(IRI_ESCAPES.values()), list(IRI_ESCAPES))


def absolute_id(column):
    expr = _col(column)
    return encode_iri(polars.when(is_iri(expr)).then(expr).otherwise(polars.lit(UUID_PREFIX) + expr))


def _lookup(expr, mapping, default=None):
    """Expr → mapped Utf8 Expr; an empty mapping is the default for every row."""
    if not mapping:
        return polars.lit(default, dtype=polars.Utf8)
    return expr.replace_strict(mapping, default=default, return_dtype=polars.Utf8)


def absolute_name(column, namespaces=None, undefined_namespace=TRIPLETS_NS):
    expr = _col(column)
    return encode_iri(polars.when(is_iri(expr)).then(expr)
                      .otherwise(_lookup(expr, namespaces, undefined_namespace) + expr))


def absolute_key(column, namespaces=None, undefined_namespace=TRIPLETS_NS):
    expr = _col(column)
    return (polars.when(expr == TYPE_KEY).then(polars.lit(RDF_TYPE))
            .otherwise(absolute_name(expr, namespaces, undefined_namespace)))


def defined(key, value, namespaces=None, value_types=None):
    """Boolean Expr of rows the schema accounts for — see the scalar rule."""
    key, value = _col(key), _col(value)
    schema, known = _lookup(key, value_types), value.is_in(list(namespaces or ()))
    return ((key == TYPE_KEY) & known) | (schema.is_not_null() & ((schema != "enum") | is_iri(value) | known))


def absolute_value(key, value, namespaces=None, value_types=None, datatypes=None, undefined_namespace=TRIPLETS_NS):
    """→ ``(kind, payload)`` Exprs; same branch order as the scalar rule."""
    key, value = _col(key), _col(value)
    schema = _lookup(key, value_types)
    present = value.is_not_null()                        # a null VALUE is no term: ("literal", null)
    is_type = (key == TYPE_KEY) & present
    enum, reference, typed = (schema == "enum") & present, (schema == "reference") & present, (schema == "literal") & present
    undefined_ref = schema.is_null() & (is_iri(value) | value.str.contains(UUID_RE.pattern))
    datatype = _lookup(key, {name: datatype for name, datatype in (datatypes or {}).items() if datatype})

    kind = (polars.when(is_type | enum | reference | undefined_ref).then(polars.lit("iri"))
            .otherwise(polars.lit("literal")))
    payload = (polars.when(is_type | enum).then(absolute_name(value, namespaces, undefined_namespace))
               .when(reference | undefined_ref).then(absolute_id(value))
               .when(typed).then(datatype)
               .otherwise(polars.lit(None, dtype=polars.Utf8)))
    return kind, payload


# ── any iri_form → the form the exporters consume (see iri_pandas.to_schema_form) ──

def to_schema_form(frame, namespaces, value_types=None, prefixes=None):
    """polars flavor of ``iri_pandas.to_schema_form``: the same steps, each a join on the
    distinct (INSTANCE_ID, value) pairs that need work — a local frame returns as is."""
    from . import DESCRIPTION_IRI, DESCRIPTION_TYPE, expand_iri, is_named, schema_name, warn_namespace_mismatch
    frame = frame.with_columns(polars.col("ID", "KEY", "VALUE", "INSTANCE_ID").cast(polars.Utf8))
    if not prefixes and not any(is_named(key) for key in frame["KEY"].unique().drop_nulls().to_list()):
        return frame
    value_types = value_types or {}
    map_ids = frame.filter((polars.col("KEY") == TYPE_KEY) & (polars.col("VALUE") == "NamespaceMap"))["ID"].to_list()
    maps = {}
    for row in frame.filter(polars.col("ID").is_in(map_ids) & ~polars.col("KEY").is_in([TYPE_KEY, "xml_base", ""])).iter_rows(named=True):
        maps.setdefault(row["INSTANCE_ID"], {})[row["KEY"]] = row["VALUE"]

    def expand(frame, column, rows):
        pairs = frame.filter(rows & polars.col(column).str.contains(PREFIXED_RE.pattern)).select("INSTANCE_ID", column).unique()
        if pairs.height == 0:
            return frame
        expanded = [expand_iri(value, {**maps.get(instance, {}), **(prefixes or {})})
                    for instance, value in pairs.iter_rows()]
        pairs = pairs.with_columns(polars.Series("_expanded", expanded, dtype=polars.Utf8))
        return (frame.join(pairs, on=["INSTANCE_ID", column], how="left", nulls_equal=True, maintain_order="left")
                .with_columns(polars.coalesce("_expanded", column).alias(column)).drop("_expanded"))

    def localize(values):
        return {value: schema_name(value, namespaces) or value for value in values if value is not None}

    frame = expand(frame, "KEY", polars.lit(True))
    keys = localize(frame["KEY"].unique().to_list())
    keys[TYPE_KEY] = TYPE_KEY
    frame = frame.with_columns(polars.col("KEY").replace(keys))
    warn_namespace_mismatch(list(keys.values()), namespaces)
    kind = polars.col("KEY").replace_strict(value_types, default=None, return_dtype=polars.Utf8) if value_types \
        else polars.lit(None, dtype=polars.Utf8)
    frame = expand(frame, "ID", polars.lit(True))
    frame = expand(frame, "VALUE", (kind != "literal").fill_null(True))
    names = (polars.col("KEY") == TYPE_KEY) | (kind == "enum").fill_null(False)
    distinct = localize(frame.filter(names)["VALUE"].unique().to_list())
    distinct = {value: DESCRIPTION_TYPE if value == DESCRIPTION_IRI else local for value, local in distinct.items()}
    return frame.with_columns(polars.when(names).then(polars.col("VALUE").replace(distinct)).otherwise(polars.col("VALUE")))
