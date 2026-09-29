"""polars flavor of :mod:`triplets.iri` — column name or Expr in, Expr out, same names.

Pure expressions, no Python UDFs. Columns are expected as Utf8 (cast
Categorical first, as the N-Quads exporter does).
"""
import polars

from . import (HTTP_FRAGMENT_RE, HTTP_KEY_PREFIX_RE, ID_PREFIX_RE, IRI_ESCAPES, RDF_TYPE, TERM_PREFIX_RE, TRIPLETS_NS,
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
            .otherwise(expr.str.replace(HTTP_KEY_PREFIX_RE.pattern, "")))


def local_term(column):
    return _col(column).str.replace(TERM_PREFIX_RE.pattern, "")


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
    return (polars.when(expr == "Type").then(polars.lit(RDF_TYPE))
            .otherwise(absolute_name(expr, namespaces, undefined_namespace)))


def defined(key, value, namespaces=None, value_types=None):
    """Boolean Expr of rows the schema accounts for — see the scalar rule."""
    key, value = _col(key), _col(value)
    schema, known = _lookup(key, value_types), value.is_in(list(namespaces or ()))
    return ((key == "Type") & known) | (schema.is_not_null() & ((schema != "enum") | is_iri(value) | known))


def absolute_value(key, value, namespaces=None, value_types=None, datatypes=None, undefined_namespace=TRIPLETS_NS):
    """→ ``(kind, payload)`` Exprs; same branch order as the scalar rule."""
    key, value = _col(key), _col(value)
    schema = _lookup(key, value_types)
    present = value.is_not_null()                        # a null VALUE is no term: ("literal", null)
    is_type = (key == "Type") & present
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
