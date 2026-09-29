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
``VALUE`` of ``Type`` (class)          ``local_name``   ``absolute_value``
``VALUE`` (reference, enum)            ``local_value``  ``absolute_value``
RDF term read back (N-Quads, SPARQL)   ``local_node`` / ``local_object`` — the readers' entry points
SHACL / RDFS vocabulary terms          ``local_term``   —
=====================================  ===============  ==================

Local rules are schema-free (a parse has no schema). Absolute rules take the
flat maps built from the export schema — :func:`namespaces`, :func:`value_types`,
:func:`datatypes`, one dict each, only the ones a call site needs. The schema
entry type decides the serialisation form (Attribute → literal, Association →
reference, Enumeration → enum IRI); ``xsd:type`` only annotates literals. A name
the schema does not declare — every name without a schema — takes
``undefined_namespace`` (``http://triplets#`` unless the caller says otherwise).

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
HTTP_NAME_PREFIX_RE = re.compile(r"^http.*[#/]")   # greedy: up to the last "#" or "/"
TERM_PREFIX_RE = re.compile(r"^.*[#/]")      # greedy: up to the last "#" or "/"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
"""Canonical lowercase mRID — what the exporters turn into ``urn:uuid:``."""
REFERENCE_LIKE = re.compile(
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\w+://\S+|urn:\S+|[A-Za-z]\w*\.\w+)$")
"""Loose "looks like a reference" heuristic for SHACL nodeKind when the schema
says nothing about a key. Not the export rule — see :data:`UUID_RE`."""

IRI_UNSAFE = "".join(map(chr, range(0x21))) + '<>"{}|^`\\'
"""Characters an N-Triples / SPARQL IRIREF may not hold (controls, space, ``<>"{}|^`\\``)."""
IRI_UNSAFE_RE = re.compile(r'[\x00-\x20<>"{}|^`\\]')
IRI_ESCAPES = {char: f"%{ord(char):02X}" for char in IRI_UNSAFE}
IRI_ESCAPE_RE = re.compile("|".join(IRI_ESCAPES.values()))


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

    Only ``#`` splits — a ``/``-only IRI stays whole (``local_resources``
    contract). Enumerations come out as ``Kind.value``, classes as ``Breaker``.
    """
    if text is None:
        return None
    text = local_id(text)
    if text.startswith("http") and "#" in text:
        return text.rsplit("#", 1)[-1]
    return text


def local_name(iri):
    """Element IRI (predicate, class) → XML local name: ``http(s)…`` → after the last ``#``
    or ``/``, else unchanged.

    The CIM XML parsers read KEY and ``Type`` VALUE from element tags, where XML already
    separates namespace and local name (lxml ``{ns}local``, pugixml ``prefix:local``) —
    they split the QName natively, no IRI string exists. This is the same split for an
    IRI string: an XML local name holds neither ``#`` nor ``/``, so ``dcterms:issued``
    → ``issued`` exactly as the parser gives it. References never split on ``/``: see
    :func:`local_value`.
    """
    if iri is None:
        return None
    iri = str(iri)
    return HTTP_NAME_PREFIX_RE.sub("", iri) if iri.startswith("http") else iri


def local_key(iri):
    """Predicate → KEY: ``rdf:type`` → ``Type``, else :func:`local_name`."""
    if iri is None:
        return None
    return "Type" if str(iri) == RDF_TYPE else local_name(iri)


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


def encode_iri(text):
    """Percent-encode the characters an IRI may not hold, so a schema reference / enum
    value stays an IRI whatever its text (``a b`` → ``a%20b``). None → None."""
    if text is None or not IRI_UNSAFE_RE.search(text):
        return text
    return IRI_UNSAFE_RE.sub(lambda match: IRI_ESCAPES[match.group()], text)


def decode_iri(text):
    """Inverse of :func:`encode_iri` for IRI terms read back from RDF — only those escapes.
    None → None."""
    if text is None or "%" not in text:
        return text
    return IRI_ESCAPE_RE.sub(lambda match: chr(int(match.group()[1:], 16)), text)


# ── RDF terms read back (N-Quads, CONSTRUCT, SHACL results) ───────────────────

def local_node(iri):
    """Subject / focus node / graph IRI read from RDF → triplet ID: :func:`decode_iri`, then
    :func:`local_id`. None → None."""
    return None if iri is None else local_id(decode_iri(str(iri)))


def local_object(iri, is_type=False, subjects=()):
    """Object IRI read from RDF → triplet VALUE. *subjects*: the subject IRIs of the same
    data, compared as read (before decoding).

    :func:`decode_iri`, then: a class (``is_type``, the object of ``rdf:type``) →
    :func:`local_name`, like its KEY; an IRI that is also a subject → :func:`local_id`, so
    the reference still joins its ID; else :func:`local_value`. None → None.
    """
    if iri is None:
        return None
    iri = str(iri)
    rule = local_name if is_type else local_id if iri in subjects else local_value
    return rule(decode_iri(iri))


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
    """Every ``(name, entry)`` across profile sections, reversed so a dict comprehension
    over it keeps the FIRST occurrence in schema order (a dict keeps the last write)."""
    return [(name, entry) for profile in load_rdf_map(rdf_map).values() if isinstance(profile, dict)
            for name, entry in profile.items() if isinstance(entry, dict)][::-1]


def namespaces(rdf_map):
    """name → namespace IRI, every entry (classes, keys, enum values, datatypes)."""
    return {name: entry["namespace"] for name, entry in rdf_map_entries(rdf_map) if entry.get("namespace")}


def key_types(rdf_map):
    """name → schema entry type (Class | Attribute | Association | Enumeration | EnumerationValue | …)."""
    return {name: entry["type"] for name, entry in rdf_map_entries(rdf_map) if entry.get("type")}


def _xsd(entry):
    """Entry's xsd datatype name (``float``, ``string``, ``anyURI``, …) or None."""
    xsd = str(entry.get("xsd:type", ""))
    return xsd.removeprefix("xsd:") if xsd.startswith("xsd:") else None


def datatypes(rdf_map):
    """KEY → xsd datatype IRI for Attribute entries; ``None`` = ``xsd:string`` (no annotation).
    Annotation / validation only — it never decides whether a value is a literal or an IRI."""
    return {name: None if xsd == "string" else XSD_NS + xsd for name, entry in rdf_map_entries(rdf_map)
            if entry.get("type") == "Attribute" and (xsd := _xsd(entry))}


_VALUE_TYPES = {"Attribute": "literal", "Association": "reference", "Enumeration": "enum"}


def value_types(rdf_map):
    """KEY → ``"literal"`` | ``"reference"`` | ``"enum"`` by schema entry type; absent = undefined KEY."""
    return {name: _VALUE_TYPES[kind] for name, entry in rdf_map_entries(rdf_map)
            if (kind := entry.get("type")) in _VALUE_TYPES}


# ── absolute (schema-driven) ───────────────────────────────────────────────────

def absolute_id(text):
    """Bare ID / INSTANCE_ID → ``urn:uuid:…``; an absolute IRI passes through; then
    :func:`encode_iri`. None → None."""
    if text is None:
        return None
    return encode_iri(text if is_iri(text) else UUID_PREFIX + text)


def absolute_name(name, namespaces=None, undefined_namespace=TRIPLETS_NS):
    """Local schema name (class, enum value, key) → its namespace + name; an undeclared name takes
    *undefined_namespace*; IRIs / None pass through."""
    if name is None:
        return None
    return encode_iri(name if is_iri(name) else (namespaces or {}).get(name, undefined_namespace) + name)


def absolute_key(key, namespaces=None, undefined_namespace=TRIPLETS_NS):
    """KEY → predicate IRI: ``Type`` → ``rdf:type``, else :func:`absolute_name`."""
    return RDF_TYPE if key == "Type" else absolute_name(key, namespaces, undefined_namespace)


def defined(key, value, namespaces=None, value_types=None):
    """Does the schema account for this row? ``Type`` → the class is declared; else the KEY is
    declared and, for an enum KEY, so is the value. Without a schema nothing is defined."""
    namespaces, value_types = namespaces or {}, value_types or {}
    if key == "Type":
        return value in namespaces
    return key in value_types and (value_types[key] != "enum" or is_iri(value) or value in namespaces)


def absolute_value(key, value, namespaces=None, value_types=None, datatypes=None, undefined_namespace=TRIPLETS_NS):
    """VALUE → ``("iri", iri)`` or ``("literal", xsd_datatype_or_None)``.

    Type → class IRI. Otherwise the schema entry type of the KEY decides, never
    the shape of the text: enum → enum IRI, reference → :func:`absolute_id` of
    the value (absolute passes through, else ``urn:uuid:``), literal → datatype
    annotation (an Attribute stays a literal even when its text looks like an
    IRI or a UUID). An undefined KEY falls to the shape heuristic: absolute IRI
    or canonical UUID → reference, else plain literal. Quoting and escaping
    belong to the serializer, not here.
    """
    if value is None:                    # no term — same answer as the flavors' null rows
        return "literal", None
    if key == "Type":
        return "iri", absolute_name(value, namespaces, undefined_namespace)
    kind = (value_types or {}).get(key)
    if kind == "enum":
        return "iri", absolute_name(value, namespaces, undefined_namespace)
    if kind == "reference":
        return "iri", absolute_id(value)
    if kind == "literal":
        return "literal", (datatypes or {}).get(key)
    if is_iri(value) or UUID_RE.match(str(value)):
        return "iri", absolute_id(str(value))
    return "literal", None


def node_kind(key, value_types):
    """``sh:nodeKind`` by schema entry type, the same rule the exporters apply: Association /
    Enumeration → ``"iri"``, Attribute → ``"literal"``; ``None`` (undefined KEY) → check the
    value form."""
    return {"reference": "iri", "enum": "iri", "literal": "literal"}.get(value_types.get(key))
