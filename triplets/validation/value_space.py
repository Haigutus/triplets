"""XSD lexical forms and value spaces shared by the SHACL engines.

``DATATYPES`` is the lexical space of each checked xsd type, as ``sh:datatype``
judges a VALUE string. ``SPACES`` maps an xsd type to its value space: the
types whose values compare with each other in ``sh:minInclusive`` …
``sh:maxExclusive`` (``number``, ``dateTime``, ``date``). A range bound fixes
the space its constraint compares in (``bound_space``); a value compares with a
``date`` or ``dateTime`` bound only in the bound's comparable form
(``comparable``), any other value violates.
"""
import datetime
import re

from ..iri import XSD_NS

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


# ── value spaces ─────────────────────────────────────────────────────────────
# every xsd type rdflib reads as a Python number
_NUMBERS = ("integer", "int", "long", "short", "byte", "nonNegativeInteger", "positiveInteger", "nonPositiveInteger",
            "negativeInteger", "unsignedLong", "unsignedInt", "unsignedShort", "unsignedByte", "decimal", "float", "double")
SPACES = {**dict.fromkeys(_NUMBERS, "number"), "date": "date", "dateTime": "dateTime", "dateTimeStamp": "dateTime"}

# Comparable forms: the lexical forms of DATATYPES within the field ranges of
# Python's datetime, which rdflib reads for pyshacl — outside them the engines'
# casts disagree (duckdb reads 24:00:00, polars 23:59:60 as the next day).
_YEAR = r"(?:[1-9][0-9]{3}|0[1-9][0-9]{2}|00[1-9][0-9]|000[1-9])"
_DAY = rf"{_YEAR}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
_CLOCK = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?"
_OFFSET = r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
# space → (form against an unzoned bound, form against a zoned one): a date ignores
# its zone (as pyshacl does); a dateTime compares only with one zoned like the bound
_FORMS = {"date": (f"{_DAY}{_OFFSET}?",) * 2,
          "dateTime": (f"{_DAY}T{_CLOCK}", f"{_DAY}T{_CLOCK}{_OFFSET}")}
_ZONED = re.compile(f"{_OFFSET}$")
# a dateTime text that ends with its clock (no zone): the engines append Z to read
# it as UTC — it only ever compares with an unzoned bound read the same way
UNZONED_END = r"(T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?)$"


def comparable(space, bound):
    """The regex a value fully matches to compare with the lexical *bound* in *space*."""
    return _FORMS[space][bool(_ZONED.search(bound))]


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
_MICROSECOND = datetime.timedelta(microseconds=1)


def _instant(text):
    moment = datetime.datetime.fromisoformat(text)
    return (moment.replace(tzinfo=moment.tzinfo or datetime.timezone.utc) - _EPOCH) // _MICROSECOND


# space → Python's reading of a comparable form (rdflib's, for pyshacl) as an integer
# ordered like the values: the day's ordinal; microseconds since the epoch, an
# unzoned dateTime read as UTC
_KEYS = {"date": lambda text: datetime.date.fromisoformat(text[:10]).toordinal(), "dateTime": _instant}


def key(space, text):
    """*text*, in a comparable form of *space*, as an integer ordered like the
    values; None for a day the calendar does not have (``2020-02-30``)."""
    try:
        return _KEYS[space](text)
    except ValueError:
        return None


def bound_space(literal):
    """(space, lexical form) of a range bound literal, checked on the text rdflib
    holds: under rdflib's default literal normalisation an ill-typed literal rdflib
    can read arrives as its value's text (``"2020-06-01T12:00:00Z"^^xsd:date`` as
    ``2020-06-01``) and is valid. None when the literal is in no space or its text
    is not a valid value of its datatype: a number rdflib cannot read, a date or
    dateTime outside its lexical and comparable form or the calendar."""
    name = str(literal.datatype).removeprefix(XSD_NS)
    space, lexical = SPACES.get(name), str(literal)
    if space == "number":
        valid = literal.value is not None
    else:
        valid = (space is not None and re.fullmatch(DATATYPES[name][0], lexical)
                 and re.fullmatch(comparable(space, lexical), lexical) and key(space, lexical) is not None)
    return (space, lexical) if valid else None


def range_bound(params):
    """(space, bound) of a range constraint's IR params: a float is a number bound,
    a date or dateTime bound is the (space, lexical form) pair."""
    return params if isinstance(params, tuple) else ("number", params)
