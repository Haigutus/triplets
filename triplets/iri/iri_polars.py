"""polars flavor of :mod:`triplets.iri` — column name or Expr in, Expr out, same names.

Pure expressions, no Python UDFs. Columns are expected as Utf8 (cast
Categorical first, as the N-Quads exporter does).
"""
import polars

from . import (EMPTY_TERMS, HTTP_FRAGMENT_RE, ID_PREFIX_RE, RDF_TYPE, TERM_PREFIX_RE,
               URI_PREFIX_RE, UUID_PREFIX, UUID_RE)


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
    return (polars.when(expr == RDF_TYPE).then(polars.lit("Type"))
            .otherwise(_fragment_after_hash(expr)))


def local_term(column):
    return _col(column).str.replace(TERM_PREFIX_RE.pattern, "")


def is_iri(column):
    return _col(column).str.contains(URI_PREFIX_RE.pattern)


def absolute_id(column):
    expr = _col(column)
    return polars.when(is_iri(expr)).then(expr).otherwise(polars.lit(UUID_PREFIX) + expr)


def _namespace(expr, terms):
    if not terms.namespaces:
        return polars.lit(terms.default_ns)
    return expr.replace_strict(terms.namespaces, default=terms.default_ns, return_dtype=polars.Utf8)


def absolute_name(column, terms=None):
    terms = terms or EMPTY_TERMS
    expr = _col(column)
    return polars.when(is_iri(expr)).then(expr).otherwise(_namespace(expr, terms) + expr)


def absolute_key(column, terms=None):
    expr = _col(column)
    return (polars.when(expr == "Type").then(polars.lit(RDF_TYPE))
            .otherwise(absolute_name(expr, terms)))


def absolute_value(key, value, terms=None):
    """→ ``(kind, payload)`` Exprs; same branch order as the scalar rule."""
    terms = terms or EMPTY_TERMS
    key, value = _col(key), _col(value)
    is_type = key == "Type"
    uri = is_iri(value)
    enum = key.is_in(list(terms.enum_keys)) if terms.enum_keys else polars.lit(False)
    typed = key.is_in(list(terms.datatypes)) if terms.datatypes else polars.lit(False)
    uuid = value.str.contains(UUID_RE.pattern)
    typed_map = {name: datatype for name, datatype in terms.datatypes.items() if datatype}
    datatype = (key.replace_strict(typed_map, default=None, return_dtype=polars.Utf8)
                if typed_map else polars.lit(None, dtype=polars.Utf8))

    kind = (polars.when(is_type | uri | enum).then(polars.lit("iri"))
            .when(typed).then(polars.lit("literal"))
            .when(uuid).then(polars.lit("iri"))
            .otherwise(polars.lit("literal")))
    payload = (polars.when(is_type | enum).then(absolute_name(value, terms))
               .when(uri).then(value)
               .when(typed).then(datatype)
               .when(uuid).then(polars.lit(UUID_PREFIX) + value)
               .otherwise(polars.lit(None, dtype=polars.Utf8)))
    return kind, payload
