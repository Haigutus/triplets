"""pandas flavor of :mod:`triplets.iri` — Series in, Series out, same names.

Nulls propagate (None/NaN in → None/NaN out) except where a rule needs a
boolean, which treats null as "no" (``na=False``, never ``fillna`` — that
downcast is deprecated on object columns).
"""
import numpy
import pandas

from . import (EMPTY_TERMS, HTTP_FRAGMENT_RE, ID_PREFIX_RE, RDF_TYPE, TERM_PREFIX_RE,
               URI_PREFIXES, UUID_PREFIX, UUID_RE)

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


def _namespace(series, terms):
    if not terms.namespaces:
        return terms.default_ns
    return series.map(terms.namespaces).fillna(terms.default_ns)


def absolute_name(series, terms=None):
    terms = terms or EMPTY_TERMS
    return series.where(is_iri(series), _namespace(series, terms) + series)


def absolute_key(series, terms=None):
    return absolute_name(series, terms).mask(_eq(series, "Type"), RDF_TYPE)


def absolute_value(key, value, terms=None):
    """→ ``(kind, payload)`` Series: kind ∈ {"iri", "literal"}; payload is the
    IRI, or the xsd datatype IRI / null for literals. Same precedence as the
    scalar rule, applied as masks from lowest to highest — stays in the
    string dtype (no object arrays)."""
    terms = terms or EMPTY_TERMS
    false = pandas.Series(False, index=key.index)
    is_type = _eq(key, "Type")
    uri = is_iri(value)
    enum = key.isin(terms.enum_keys) if terms.enum_keys else false
    typed = key.isin(terms.datatypes) if terms.datatypes else false
    uuid = value.str.match(UUID_RE.pattern, na=False).astype(bool)

    payload = (key.map(terms.datatypes).astype(value.dtype) if terms.datatypes
               else pandas.Series(None, index=key.index, dtype=value.dtype))
    payload = payload.mask(~typed & uuid, UUID_PREFIX + value)
    payload = payload.mask(uri, value)
    payload = payload.mask(is_type | enum, absolute_name(value, terms))
    kind = numpy.where(is_type | uri | enum | (~typed & uuid), "iri", "literal")
    return pandas.Series(kind, index=key.index, dtype=value.dtype), payload
