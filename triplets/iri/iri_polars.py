"""polars flavor of :mod:`triplets.iri` — column name or Expr in, Expr out, same names.

Pure expressions, no Python UDFs. Columns are expected as Utf8 (cast
Categorical first, as the N-Quads exporter does).
"""
import polars

from . import (CIM_NS, HTTP_FRAGMENT_RE, ID_PREFIX_RE, RDF_TYPE, TERM_PREFIX_RE, URI_PREFIX_RE,
               UUID_PREFIX, UUID_RE)


def _col(column):
    return polars.col(column) if isinstance(column, str) else column


def _fragment_after_hash(expr):
    return expr.str.replace(HTTP_FRAGMENT_RE.pattern, "")


def local_id(column):
    return _col(column).str.replace(ID_PREFIX_RE.pattern, "")


def local_value(column):
    return _fragment_after_hash(local_id(column))


def local_key(column):
    expr = _col(column)
    return polars.when(expr == RDF_TYPE).then(polars.lit("Type")).otherwise(_fragment_after_hash(expr))


def local_term(column):
    return _col(column).str.replace(TERM_PREFIX_RE.pattern, "")


def is_iri(column):
    return _col(column).str.contains(URI_PREFIX_RE.pattern).fill_null(False)   # null → False, as the scalar; regex measured as fast as starts_with


def absolute_id(column):
    expr = _col(column)
    return polars.when(is_iri(expr)).then(expr).otherwise(polars.lit(UUID_PREFIX) + expr)


def _lookup(expr, mapping, default=None):
    """Expr → mapped Utf8 Expr; an empty mapping is the default for every row."""
    if not mapping:
        return polars.lit(default, dtype=polars.Utf8)
    return expr.replace_strict(mapping, default=default, return_dtype=polars.Utf8)


def absolute_name(column, namespaces=None):
    expr = _col(column)
    return polars.when(is_iri(expr)).then(expr).otherwise(_lookup(expr, namespaces, CIM_NS) + expr)


def absolute_key(column, namespaces=None):
    expr = _col(column)
    return polars.when(expr == "Type").then(polars.lit(RDF_TYPE)).otherwise(absolute_name(expr, namespaces))


def absolute_value(key, value, namespaces=None, value_types=None, datatypes=None):
    """→ ``(kind, payload)`` Exprs; same branch order as the scalar rule."""
    key, value = _col(key), _col(value)
    schema = _lookup(key, value_types)
    is_type, uri = key == "Type", is_iri(value)
    enum, reference, typed = schema == "enum", schema == "reference", schema == "literal"
    uuid = schema.is_null() & value.str.contains(UUID_RE.pattern)
    datatype = _lookup(key, {name: datatype for name, datatype in (datatypes or {}).items() if datatype})

    kind = (polars.when(is_type | uri | enum | reference | uuid).then(polars.lit("iri"))
            .otherwise(polars.lit("literal")))
    payload = (polars.when(is_type | enum).then(absolute_name(value, namespaces))
               .when(uri).then(value)
               .when(reference | uuid).then(polars.lit(UUID_PREFIX) + value)
               .when(typed).then(datatype)
               .otherwise(polars.lit(None, dtype=polars.Utf8)))
    return kind, payload
