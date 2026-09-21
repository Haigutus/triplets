"""pandas flavor of :mod:`triplets.iri` — Series in, Series out, same names.

Nulls propagate (None/NaN in → None/NaN out) except where a rule needs a
boolean, which treats null as "no".
"""
import numpy
import pandas

from . import (EMPTY_TERMS, HTTP_FRAGMENT_RE, ID_PREFIX_RE, RDF_TYPE, TERM_PREFIX_RE,
               URI_PREFIX_RE, UUID_PREFIX, UUID_RE)

# Regex replaces only: they work the same on object and arrow-backed string
# Series (rsplit/list access does not).


def _eq(series, value):
    """Null-safe equality: arrow-backed strings compare to <NA>, which mask() reads as True."""
    return (series == value).fillna(False).astype(bool)


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
    return series.str.match(URI_PREFIX_RE.pattern).fillna(False).astype(bool)


def expand_id(series):
    return series.where(is_iri(series), UUID_PREFIX + series)


def _namespace(series, terms):
    if not terms.namespaces:
        return terms.default_ns
    return series.map(terms.namespaces).fillna(terms.default_ns)


def expand_name(series, terms=None):
    terms = terms or EMPTY_TERMS
    return series.where(is_iri(series), _namespace(series, terms) + series)


def expand_key(series, terms=None):
    return expand_name(series, terms).mask(_eq(series, "Type"), RDF_TYPE)


expand_class = expand_name


def expand_value(key, value, terms=None):
    """→ ``(kind, payload)`` Series: kind ∈ {"iri", "literal"}; payload is the
    IRI, or the xsd datatype IRI / None for literals. Same branch order as the
    scalar rule."""
    terms = terms or EMPTY_TERMS
    is_type = _eq(key, "Type").to_numpy()
    uri = is_iri(value).to_numpy()
    enum = key.isin(terms.enum_keys).to_numpy() if terms.enum_keys else numpy.zeros(len(key), bool)
    typed = key.isin(terms.datatypes).to_numpy() if terms.datatypes else numpy.zeros(len(key), bool)
    uuid = value.str.match(UUID_RE.pattern).fillna(False).to_numpy()

    named = expand_name(value, terms).to_numpy(dtype=object)
    conditions = [is_type, uri, enum, typed, uuid]
    kinds = numpy.select(conditions, ["iri", "iri", "iri", "literal", "iri"], default="literal")
    payloads = numpy.select(
        conditions,
        [named, value.to_numpy(dtype=object), named,
         key.map(terms.datatypes).to_numpy(dtype=object) if terms.datatypes else numpy.full(len(key), None),
         (UUID_PREFIX + value).to_numpy(dtype=object)],
        default=None)
    return (pandas.Series(kinds, index=key.index, dtype=object),
            pandas.Series(payloads, index=key.index, dtype=object))
