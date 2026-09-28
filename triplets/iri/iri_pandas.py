"""pandas flavor of :mod:`triplets.iri` — Series in, Series out, same names.

Nulls propagate (None/NaN in → None/NaN out) except where a rule needs a
boolean, which treats null as "no" (``na=False``, never ``fillna`` — that
downcast is deprecated on object columns).
"""
import numpy
import pandas

from . import (HTTP_FRAGMENT_RE, ID_PREFIX_RE, RDF_TYPE, TERM_PREFIX_RE, TRIPLETS_NS, URI_PREFIXES,
               UUID_PREFIX, UUID_RE)

# Regex replaces only: they work the same on object and arrow-backed string
# Series (rsplit/list access does not).


def _eq(series, value):
    """Null-safe equality: arrow-backed strings compare to <NA>, which mask() reads as True."""
    return (series == value).where(series.notna(), False).astype(bool)


def _fragment_after_hash(series):
    return series.str.replace(HTTP_FRAGMENT_RE.pattern, "", regex=True)


def local_id(series):
    return series.str.replace(ID_PREFIX_RE.pattern, "", regex=True)


def local_value(series):
    return _fragment_after_hash(local_id(series))


def local_key(series):
    return _fragment_after_hash(series).mask(_eq(series, RDF_TYPE), "Type")


def local_term(series):
    return series.str.replace(TERM_PREFIX_RE.pattern, "", regex=True)


def is_iri(series):
    return series.str.startswith(URI_PREFIXES, na=False).astype(bool)   # ~3x faster than a regex match on arrow strings


def absolute_id(series):
    return series.where(is_iri(series), UUID_PREFIX + series)


def absolute_name(series, namespaces=None, undefined_namespace=TRIPLETS_NS):
    namespace = series.map(namespaces).fillna(undefined_namespace) if namespaces else undefined_namespace
    return series.where(is_iri(series), namespace + series)


def absolute_key(series, namespaces=None, undefined_namespace=TRIPLETS_NS):
    return absolute_name(series, namespaces, undefined_namespace).mask(_eq(series, "Type"), RDF_TYPE)


def _schema(key, value_types):
    return key.map(value_types) if value_types else pandas.Series(None, index=key.index, dtype=object)


def defined(key, value, namespaces=None, value_types=None):
    """Boolean mask of rows the schema accounts for — see the scalar rule."""
    schema, known = _schema(key, value_types), value.isin(namespaces or ())
    return (_eq(key, "Type") & known) | (schema.notna() & (~_eq(schema, "enum") | is_iri(value) | known))


def absolute_value(key, value, namespaces=None, value_types=None, datatypes=None, undefined_namespace=TRIPLETS_NS):
    """→ ``(kind, payload)`` Series: kind ∈ {"iri", "literal"}; payload is the
    IRI, or the xsd datatype IRI / null for literals. Same precedence as the
    scalar rule, applied as masks from lowest to highest — stays in the
    string dtype (no object arrays)."""
    schema = _schema(key, value_types)
    is_type = _eq(key, "Type")
    enum, reference, typed = _eq(schema, "enum"), _eq(schema, "reference"), _eq(schema, "literal")
    undefined_ref = schema.isna() & (is_iri(value) | value.str.match(UUID_RE.pattern, na=False).astype(bool))

    payload = (key.map(datatypes).astype(value.dtype) if datatypes
               else pandas.Series(None, index=key.index, dtype=value.dtype)).where(typed)
    payload = payload.mask(reference | undefined_ref, absolute_id(value))
    payload = payload.mask(is_type | enum, absolute_name(value, namespaces, undefined_namespace))
    kind = numpy.where(is_type | enum | reference | undefined_ref, "iri", "literal")
    return pandas.Series(kind, index=key.index, dtype=value.dtype), payload
