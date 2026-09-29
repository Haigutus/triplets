"""pandas flavor of :mod:`triplets.iri` — Series in, Series out, same names.

Nulls propagate (None/NaN in → None/NaN out) except where a rule needs a
boolean, which treats null as "no" (``na=False``, never ``fillna`` — that
downcast is deprecated on object columns).
"""
import numpy
import pandas

from . import (HTTP_FRAGMENT_RE, HTTP_NAME_PREFIX_RE, ID_PREFIX_RE, IRI_ESCAPE_RE, IRI_UNSAFE_RE, RDF_TYPE, TERM_PREFIX_RE,
               TRIPLETS_NS, URI_PREFIXES, UUID_PREFIX, UUID_RE)
from . import decode_iri as _decode_iri, encode_iri as _encode_iri

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


def local_name(series):
    return series.str.replace(HTTP_NAME_PREFIX_RE.pattern, "", regex=True)


def local_key(series):
    return local_name(series).mask(_eq(series, RDF_TYPE), "Type")


def local_term(series):
    return series.str.replace(TERM_PREFIX_RE.pattern, "", regex=True)


def is_iri(series):
    return series.str.startswith(URI_PREFIXES, na=False).astype(bool)   # ~3x faster than a regex match on arrow strings


def _rows_matching(series, pattern, scalar):
    """Apply the scalar rule only to rows matching *pattern* — rare, and a callable
    replacement does not run on arrow-backed strings."""
    hit = series.str.contains(pattern, regex=True, na=False).astype(bool)
    if not hit.any():
        return series
    series = series.copy()
    series[hit] = series[hit].astype(object).map(scalar)
    return series


def encode_iri(series):
    return _rows_matching(series, IRI_UNSAFE_RE.pattern, _encode_iri)


def decode_iri(series):
    return _rows_matching(series, IRI_ESCAPE_RE.pattern, _decode_iri)


# encode_iri runs on the input, not the joined IRI: prefixes and namespaces are
# IRI-safe, and the input keeps its arrow string dtype (the join is object).

def absolute_id(series):
    encoded = encode_iri(series)
    return encoded.where(is_iri(series), UUID_PREFIX + encoded)


def absolute_name(series, namespaces=None, undefined_namespace=TRIPLETS_NS):
    namespace = series.map(namespaces).fillna(undefined_namespace) if namespaces else undefined_namespace
    encoded = encode_iri(series)
    return encoded.where(is_iri(series), namespace + encoded)


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
    present = value.notna().astype(bool)                 # a null VALUE is no term: ("literal", null)
    is_type = _eq(key, "Type") & present
    enum, reference = _eq(schema, "enum") & present, _eq(schema, "reference") & present
    typed = _eq(schema, "literal") & present
    undefined_ref = schema.isna() & (is_iri(value) | value.str.match(UUID_RE.pattern, na=False).astype(bool))

    payload = (key.map(datatypes).astype(value.dtype) if datatypes
               else pandas.Series(None, index=key.index, dtype=value.dtype)).where(typed)
    references, names = reference | undefined_ref, is_type | enum
    # IRI rules on their own rows only: a literal with a space must not reach encode_iri
    payload = payload.mask(references, absolute_id(value.where(references)))
    payload = payload.mask(names, absolute_name(value.where(names), namespaces, undefined_namespace))
    kind = numpy.where(references | names, "iri", "literal")
    return pandas.Series(kind, index=key.index, dtype=value.dtype), payload
