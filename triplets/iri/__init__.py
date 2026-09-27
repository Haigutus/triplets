"""The triplet ↔ IRI contract — one definition for ID, KEY and VALUE.

Instance data in a triplet frame is stored *local*: bare IDs, ``Class.attr``
KEYs, local-name VALUEs (W3C: *local name*). RDF serializations (N-Quads,
SPARQL stores, SHACL reports) are *absolute*: absolute IRIs. This package owns
both directions.

Which function for which column
-------------------------------
=====================================  ===============  ==================
column / context                       local            absolute
=====================================  ===============  ==================
``ID``, ``INSTANCE_ID``, focus, graph  ``local_id``     ``absolute_id``
``KEY``                                ``local_key``    ``absolute_key``
``VALUE`` (Type, reference, enum)      ``local_value``  ``absolute_value``
SHACL / RDFS vocabulary terms          ``local_term``   —
=====================================  ===============  ==================

Local rules are schema-free (a parse has no schema). Absolute rules take the
flat maps built from the export schema — :func:`namespaces`, :func:`value_types`,
:func:`datatypes`, one dict each, only the ones a call site needs; without them
CIM100 is the namespace for everything and only canonical UUIDs are references.

Flavors: :mod:`triplets.iri.iri_pandas`, :mod:`triplets.iri.iri_polars` and
:mod:`triplets.iri.iri_duckdb` implement the same names over Series / Expr /
SQL text. The cython parser and the qlever C++ ingest mirror the same rules
natively. All of them are held to one case
table in ``tests/test_iri.py`` — the scalar functions here are the readable
definition, the table is the contract.

This module imports only the standard library: the parser imports it on
every file.
"""
import hashlib
import json
import os
import re

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
URI_PREFIX_RE = re.compile("^(?:" + "|".join(map(re.escape, URI_PREFIXES)) + ")")
HTTP_FRAGMENT_RE = re.compile(r"^http.*#")   # greedy: everything up to the LAST "#"
TERM_PREFIX_RE = re.compile(r"^.*[#/]")      # greedy: up to the last "#" or "/"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
"""Canonical lowercase mRID — what the exporters turn into ``urn:uuid:``."""
REFERENCE_LIKE = re.compile(
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\w+://\S+|urn:\S+|[A-Za-z]\w*\.\w+)$")
"""Loose "looks like a reference" heuristic for SHACL nodeKind when the schema
says nothing about a key. Not the export rule — see :data:`UUID_RE`."""


# ── local (schema-free) ──────────────────────────────────────────────────────

def local_id(text):
    """``urn:uuid:x`` / ``#_x`` / ``_x`` → ``x``. One prefix, longest first. None → None."""
    if text is None:
        return None
    text = str(text)
    for prefix in ID_PREFIXES:          # a startswith loop beats ID_PREFIX_RE.sub per call
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


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


# ── flat maps from the export schema ──────────────────────────────────────────

def load_rdf_map(rdf_map):
    """Export schema: dict or JSON path → dict. ``None`` → ``{}``."""
    if rdf_map is None:
        return {}
    if isinstance(rdf_map, dict):
        return rdf_map
    with open(rdf_map) as file:
        return json.load(file)


def load_schema(rdf_map):
    """dict or path → (schema dict, content digest, source name) — for compile caches keyed on content."""
    if isinstance(rdf_map, dict):
        return rdf_map, hashlib.sha256(json.dumps(rdf_map, sort_keys=True, default=str).encode()).hexdigest(), "rdf_map"
    with open(rdf_map, "rb") as file:
        content = file.read()
    return json.loads(content), hashlib.sha256(content).hexdigest(), os.path.basename(str(rdf_map))


def rdf_map_entries(rdf_map):
    """Every ``(name, entry)`` across profile sections, LAST profile first — a dict
    comprehension over it keeps the first occurrence (a dict keeps the last write)."""
    return [(name, entry) for profile in load_rdf_map(rdf_map).values() if isinstance(profile, dict)
            for name, entry in profile.items() if isinstance(entry, dict)][::-1]


def namespaces(rdf_map):
    """name → namespace IRI, every entry (classes, keys, enum values, datatypes)."""
    return {name: entry["namespace"] for name, entry in rdf_map_entries(rdf_map) if entry.get("namespace")}


def key_types(rdf_map):
    """name → schema entry type (Class | Attribute | Association | Enumeration | EnumerationValue | …)."""
    return {name: entry["type"] for name, entry in rdf_map_entries(rdf_map) if entry.get("type")}


def _xsd(entry):
    """Entry's xsd datatype name (``float``, ``string``, …) or None; ``xsd:anyURI`` is not a datatype here."""
    xsd = str(entry.get("xsd:type", ""))
    return xsd.removeprefix("xsd:") if xsd.startswith("xsd:") and xsd != "xsd:anyURI" else None


def datatypes(rdf_map):
    """KEY → xsd datatype IRI; ``None`` = ``xsd:string`` (no annotation). ``xsd:anyURI`` absent: references keep IRI handling."""
    return {name: None if xsd == "string" else XSD_NS + xsd
            for name, entry in rdf_map_entries(rdf_map) if (xsd := _xsd(entry))}


_VALUE_TYPES = {"Enumeration": "enum", "Association": "reference"}


def value_types(rdf_map):
    """KEY → ``"enum"`` | ``"reference"`` | ``"literal"``; absent = the schema is silent."""
    return {name: kind for name, entry in rdf_map_entries(rdf_map)
            if (kind := _VALUE_TYPES.get(entry.get("type")) or ("literal" if _xsd(entry) else None))}


# ── absolute (schema-driven) ───────────────────────────────────────────────────

def absolute_id(text):
    """Bare ID / INSTANCE_ID → ``urn:uuid:…``; an absolute IRI passes through. None → None."""
    return text if text is None or is_iri(text) else UUID_PREFIX + text


def absolute_name(name, namespaces=None):
    """Local schema name (class, enum value, key) → its namespace + name; IRIs / None pass through."""
    if name is None or is_iri(name):
        return name
    return (namespaces or {}).get(name, CIM_NS) + name


def absolute_key(key, namespaces=None):
    """KEY → predicate IRI: ``Type`` → ``rdf:type``, else :func:`absolute_name`."""
    return RDF_TYPE if key == "Type" else absolute_name(key, namespaces)


def absolute_value(key, value, namespaces=None, value_types=None, datatypes=None):
    """VALUE → ``("iri", iri)`` or ``("literal", xsd_datatype_or_None)``.

    Type → class IRI; absolute IRI → itself; then the schema decides by KEY:
    enum → enum IRI, reference → ``urn:uuid:`` + value (as :func:`absolute_id`
    on the ID column), literal → datatype (schema wins over the UUID look of an
    mRID); schema silent → canonical UUID is a reference, anything else a plain
    literal. Quoting and escaping belong to the serializer, not here.
    """
    if value is None:                    # no term — same answer as the flavors' null rows
        return "literal", None
    if key == "Type":
        return "iri", absolute_name(value, namespaces)
    if is_iri(value):
        return "iri", value
    kind = (value_types or {}).get(key)
    if kind == "enum":
        return "iri", absolute_name(value, namespaces)
    if kind == "reference":
        return "iri", UUID_PREFIX + value
    if kind == "literal":
        return "literal", (datatypes or {}).get(key)
    if UUID_RE.match(value):
        return "iri", UUID_PREFIX + value
    return "literal", None


def node_kind(key, value_types, expected):
    """``sh:nodeKind`` by schema: ``"iri"`` / ``"literal"`` when the schema decides the
    whole path, ``None`` → check the value form. A reference key decides only against
    ``Literal`` (every value violates); against ``IRI`` the value must still look like one."""
    kind = value_types.get(key)
    if kind == "reference":
        return "iri" if expected == "Literal" else None
    return {"enum": "iri", "literal": "literal"}.get(kind)
