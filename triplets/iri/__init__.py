"""The triplet ↔ IRI contract — one definition for ID, KEY and VALUE.

Instance data in a triplet frame is stored *short*: bare IDs, ``Class.attr``
KEYs, local-name VALUEs. RDF serializations (N-Quads, SPARQL stores, SHACL
reports) are *long*: absolute IRIs. This package owns both directions.

Which function for which column
-------------------------------
=====================================  ==============  ==============
column / context                       shorten         expand
=====================================  ==============  ==============
``ID``, ``INSTANCE_ID``, focus, graph  ``local_id``    ``expand_id``
``KEY``                                ``local_key``   ``expand_key``
``VALUE`` (Type, reference, enum)      ``local_value`` ``expand_value``
SHACL / RDFS vocabulary terms          ``local_term``  —
=====================================  ==============  ==============

Shorten rules are schema-free (a parse has no schema). Expand rules take a
:class:`SchemaTerms` built from the export schema; without one, CIM100 is the
namespace for everything.

Flavors: :mod:`triplets.iri.iri_pandas` and :mod:`triplets.iri.iri_polars`
implement the same names over Series / Expr. The cython parser and the qlever
C++ ingest mirror the same rules natively. All of them are held to one case
table in ``tests/test_iri.py`` — the scalar functions here are the readable
definition, the table is the contract.

This module imports only the standard library: the parser imports it on
every file.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass

from .._caches import register_cache

CIM_NS = "http://iec.ch/TC57/CIM100#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDF_TYPE = RDF_NS + "type"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD_NS = "http://www.w3.org/2001/XMLSchema#"
SH_NS = "http://www.w3.org/ns/shacl#"
PROV_NS = "http://www.w3.org/ns/prov#"
DCTERMS_NS = "http://purl.org/dc/terms/"
SCHEMA_ORG_NS = "https://schema.org/"
TRIPLETS_NS = "http://triplets#"

UUID_PREFIX = "urn:uuid:"
ID_PREFIXES = ("urn:uuid:", "#_", "_")           # longest first; exactly one is stripped
URI_PREFIXES = ("http://", "https://", "urn:")

# Flavors consume ``.pattern`` — keep case and anchors inside the pattern text,
# never in flags, so pandas / polars / duckdb / C++ see the same regex.
ID_PREFIX_RE = re.compile("^(?:" + "|".join(map(re.escape, ID_PREFIXES)) + ")")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
"""Canonical lowercase mRID — what the exporters turn into ``urn:uuid:``."""
REFERENCE_LIKE = re.compile(
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\w+://\S+|urn:\S+|[A-Za-z]\w*\.\w+)$")
"""Loose "looks like a reference" heuristic for SHACL nodeKind when the schema
says nothing about a key. Not the export rule — see :data:`UUID_RE`."""

SQL_LOCAL_TERM = "list_extract(string_split(list_extract(string_split({col}, '#'), -1), '/'), -1)"
"""``local_term`` as a duckdb expression template (``{col}`` = column)."""


# ── shorten (schema-free) ──────────────────────────────────────────────────────

def local_id(text):
    """``urn:uuid:x`` / ``#_x`` / ``_x`` → ``x``. One prefix, longest first. None → None."""
    if text is None:
        return None
    return ID_PREFIX_RE.sub("", str(text), count=1)


def local_value(text):
    """Reference VALUE: :func:`local_id`, then ``http(s)…#frag`` → ``frag``.

    Only ``#`` splits — a ``/``-only IRI stays whole (``shorten_resources``
    contract). Enumerations come out as ``Kind.value``, classes as ``Breaker``.
    """
    if text is None:
        return None
    text = local_id(text)
    if text.startswith("http") and "#" in text:
        return text.rsplit("#", 1)[-1]
    return text


def local_key(iri):
    """Predicate → KEY: ``rdf:type`` → ``Type``, ``http(s)…#frag`` → ``frag``, else unchanged."""
    if iri is None:
        return None
    iri = str(iri)
    if iri == RDF_TYPE:
        return "Type"
    if iri.startswith("http") and "#" in iri:
        return iri.rsplit("#", 1)[-1]
    return iri


def local_term(iri):
    """Vocabulary term (SHACL, RDFS, SARIF) → local name: after the last ``#``, else last ``/``.

    Not for instance VALUEs — those never split on ``/``.
    """
    if iri is None:
        return None
    return str(iri).lstrip("#").rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def is_iri(text):
    """Absolute IRI already (``http://``, ``https://``, ``urn:``)."""
    return text is not None and str(text).startswith(URI_PREFIXES)


# ── schema table ───────────────────────────────────────────────────────────────

def load_schema(rdf_map):
    """dict or path → (schema dict, content digest, source name)."""
    if isinstance(rdf_map, dict):
        digest = hashlib.sha256(
            json.dumps(rdf_map, sort_keys=True, default=str).encode()).hexdigest()
        return rdf_map, digest, "rdf_map"
    with open(rdf_map, "rb") as file:
        content = file.read()
    return (json.loads(content), hashlib.sha256(content).hexdigest(),
            os.path.basename(str(rdf_map)))


_TERMS_CACHE: dict = register_cache({})  # schema digest → SchemaTerms


@dataclass(frozen=True)
class SchemaTerms:
    """What the export schema says about names — the expand-side lookup table.

    ``namespaces`` covers every schema entry (classes, attributes, associations,
    enumerations, enumeration values, datatypes) — one dict, first occurrence
    wins across profiles. A name that is absent (CIM abstracts such as
    ``Equipment``, or no schema at all) resolves to ``default_ns``.
    ``enum_keys`` / ``datatypes`` classify a KEY's values: enumeration → IRI,
    schema datatype → literal (``None`` = ``xsd:string``, no annotation),
    ``xsd:anyURI`` is absent so references keep IRI handling.
    """

    namespaces: dict
    enum_keys: frozenset
    datatypes: dict
    default_ns: str = CIM_NS

    @classmethod
    def from_rdf_map(cls, rdf_map):
        """Build once per schema content; ``None`` → the empty table."""
        if rdf_map is None:
            return EMPTY_TERMS
        if isinstance(rdf_map, SchemaTerms):
            return rdf_map
        schema, digest, _ = load_schema(rdf_map)
        if digest not in _TERMS_CACHE:
            _TERMS_CACHE[digest] = cls._build(schema)
        return _TERMS_CACHE[digest]

    @classmethod
    def _build(cls, schema):
        namespaces, enum_keys, datatypes = {}, set(), {}
        for entries in schema.values():
            if not isinstance(entries, dict):
                continue
            for name, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                if entry.get("namespace"):
                    namespaces.setdefault(name, entry["namespace"])
                if entry.get("type") == "Enumeration":
                    enum_keys.add(name)
                xsd_type = entry.get("xsd:type") or ""
                if xsd_type.startswith("xsd:") and xsd_type != "xsd:anyURI":
                    datatype = xsd_type.removeprefix("xsd:")
                    datatypes.setdefault(name, None if datatype == "string" else XSD_NS + datatype)
        return cls(namespaces, frozenset(enum_keys), datatypes)

    def namespace(self, name):
        return self.namespaces.get(name, self.default_ns)

    def key_kind(self, key):
        """``"iri"`` / ``"literal"`` / ``None`` — what values at *key* are, by schema."""
        if key in self.enum_keys:
            return "iri"
        if key in self.datatypes:
            return "literal"
        return None


EMPTY_TERMS = SchemaTerms({}, frozenset(), {})


# ── expand (schema-driven) ─────────────────────────────────────────────────────

def expand_id(text):
    """Bare ID / INSTANCE_ID → ``urn:uuid:…``; an absolute IRI passes through."""
    return text if is_iri(text) else UUID_PREFIX + text


def expand_name(name, terms=None):
    """Short schema name (class, enum value, key) → its namespace + name; IRIs pass through."""
    if is_iri(name):
        return name
    return (terms or EMPTY_TERMS).namespace(name) + name


def expand_key(key, terms=None):
    """KEY → predicate IRI: ``Type`` → ``rdf:type``, else :func:`expand_name`."""
    return RDF_TYPE if key == "Type" else expand_name(key, terms)


expand_class = expand_name


def expand_value(key, value, terms=None):
    """VALUE → ``("iri", iri)`` or ``("literal", xsd_datatype_or_None)``.

    Type → class IRI; absolute IRI → itself; enumeration key → enum IRI;
    key with a schema datatype → literal (schema wins over the UUID look of an
    mRID string); canonical UUID → ``urn:uuid:``; anything else → plain literal.
    Quoting and escaping belong to the serializer, not here.
    """
    terms = terms or EMPTY_TERMS
    if key == "Type":
        return "iri", expand_name(value, terms)
    if is_iri(value):
        return "iri", value
    if key in terms.enum_keys:
        return "iri", expand_name(value, terms)
    if key in terms.datatypes:
        return "literal", terms.datatypes[key]
    if UUID_RE.match(value):
        return "iri", UUID_PREFIX + value
    return "literal", None
