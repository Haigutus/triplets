"""XSD lexical forms shared by the SHACL engines.

``DATATYPES`` is the lexical space of each checked xsd type, as ``sh:datatype``
judges a VALUE string.
"""

# ── XSD lexical spaces ───────────────────────────────────────────────────────
# Per type: (valid lexical space, non-canonical subset reported as Warning).
_INTEGER = r"[+-]?[0-9]+"
_DECIMAL = rf"(?:{_INTEGER}|[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+))"
_FLOAT = rf"(?:{_DECIMAL}(?:[eE][+-]?[0-9]+)?|[+-]?INF|NaN)"
_DATE = r"-?[0-9]{4,}-[0-9]{2}-[0-9]{2}"
_ZONE = r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
_TIMEZONE = rf"{_ZONE}?"
_TIME = r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
_DATETIME = rf"{_DATE}T{_TIME}{_TIMEZONE}"
# PnYnMnDTnHnMnS: at least one component, no empty T — alternations, no lookahead
_SECONDS = r"[0-9]+(?:\.[0-9]+)?S"
_DURATION_TIME = rf"T(?:[0-9]+H(?:[0-9]+M)?(?:{_SECONDS})?|[0-9]+M(?:{_SECONDS})?|{_SECONDS})"
_DURATION_DATE = r"(?:[0-9]+Y(?:[0-9]+M)?(?:[0-9]+D)?|[0-9]+M(?:[0-9]+D)?|[0-9]+D)"
_DURATION = rf"-?P(?:{_DURATION_DATE}(?:{_DURATION_TIME})?|{_DURATION_TIME})"
# IRI reference (RFC 3987), relative included: no controls, space, DEL or <>"{}|^`\,
# and "%" only as a %XX escape. Non-ASCII is allowed (IRI, not URI).
_ANY_URI = r'(?:[^\x00-\x20\x7f<>"{}|^`\\%]|%[0-9A-Fa-f]{2})*'

DATATYPES = {
    "integer": (_INTEGER, None),
    "int": (_INTEGER, None),
    "long": (_INTEGER, None),
    "short": (_INTEGER, None),
    "byte": (_INTEGER, None),
    "nonNegativeInteger": (r"\+?[0-9]+", None),
    "positiveInteger": (r"\+?0*[1-9][0-9]*", None),
    "decimal": (_DECIMAL, _INTEGER),
    "float": (_FLOAT, _INTEGER),
    "double": (_FLOAT, _INTEGER),
    "boolean": (r"true|false|1|0", r"1|0"),
    "date": (_DATE + _TIMEZONE, None),
    "dateTime": (_DATETIME, None),
    "dateTimeStamp": (rf"{_DATE}T{_TIME}{_ZONE}", None),
    "time": (_TIME + _TIMEZONE, None),
    "gMonthDay": (r"--[0-9]{2}-[0-9]{2}" + _TIMEZONE, None),
    "duration": (_DURATION, None),
    "anyURI": (_ANY_URI, None),
    # string / unlisted types: every lexical form is valid — no check
}


def datatype_patterns(datatype, lexical=True):
    """(valid pattern, warn pattern or None) of an xsd datatype — None when
    the type is unchecked. ``lexical=False`` drops the triplets:lexicalForm level."""
    spec = DATATYPES.get(str(datatype).removeprefix("xsd:"))
    return None if spec is None else (spec[0], spec[1] if lexical else None)
