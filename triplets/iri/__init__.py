"""The triplet ↔ IRI contract — one definition for ID, KEY and VALUE.

Instance data in a triplet frame is stored *local*: bare IDs, ``Class.attr``
KEYs, local-name VALUEs (W3C: *local name*). RDF serializations (N-Quads,
SPARQL stores, SHACL reports) are *absolute*: absolute IRIs. This package owns
both directions.

Which function for which column
-------------------------------
=====================================  ==============================  ==================
column / context                       local                           absolute
=====================================  ==============================  ==================
``ID``, ``INSTANCE_ID``, focus, graph  ``local_id``                    ``absolute_id``
``KEY``                                ``local_key``                   ``absolute_key``
``VALUE``                              ``local_value(iri, kind)``      ``absolute_value``
namespace + local name of any IRI      ``split_iri``                   —
=====================================  ==============================  ==================

One split, :func:`split_iri`, underlies every name. Two policies sit on top: a
*name* (KEY, class, enum value) always drops its namespace — the schema restores it;
a *node* (ID, reference) drops only what a convention restores (:func:`local_id`:
``urn:uuid:``, ``#_``, ``_``) and otherwise stays whole. The local form is for people
working on imported data; where local names collide (``type`` is both
``rdf:type`` and ``dcterms:type``), only the absolute form is exact.

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
from urllib.parse import urljoin

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

TYPE_KEY = "Type"
"""KEY of the typed-node element name — the one type CIM XML writes as the object element."""
DESCRIPTION_TYPE = "Description"
"""``Type`` VALUE of an ``rdf:Description`` element: no typed-node shorthand, so not a class —
exported as ``rdf:Description`` in CIM XML and as no ``rdf:type`` in N-Quads."""
DESCRIPTION_IRI = "http://www.w3.org/1999/02/22-rdf-syntax-ns#Description"
"""The same, in the absolute form."""

IRI_FORMS = ("local", "prefixed", "absolute")
"""How ``parse(iri_form=…)`` writes every IRI in a frame (ID, KEY, ``Type`` VALUE, resource
VALUEs; literals never change): ``local`` names (the CIM convention, easiest to work with),
``prefixed`` ``prefix:local`` from the document's namespace map (readable, no collisions:
``rdf:type`` vs ``dct:type``), or ``absolute`` IRIs (exact)."""

UUID_PREFIX = "urn:uuid:"
ID_PREFIXES = ("urn:uuid:", "#_", "_")           # longest first; exactly one is stripped
URI_PREFIXES = ("http://", "https://", "urn:")

# Flavors consume ``.pattern`` — keep case and anchors inside the pattern text,
# never in flags, so pandas / polars / duckdb / C++ see the same regex.
ID_PREFIX_RE = re.compile("^(?:" + "|".join(map(re.escape, ID_PREFIXES)) + ")")
URI_PREFIX_RE = re.compile("^(?:" + "|".join(map(re.escape, URI_PREFIXES)) + ")")
SPLIT_RE = re.compile(r"^(?:.*[#/]|urn:.*:)")  # the namespace: up to the last "#" or "/", else a URN's last ":"
GUESS_NAME_RE = re.compile(r"^http.*#(?:.*/)?")   # local_value guess: split_iri's namespace, only for http IRIs with a "#"
PREFIXED_RE = re.compile(r"^([A-Za-z_][\w.-]*):(?:[^/]|$)")   # no lookahead: RE2 (Arrow) has none
"""``prefix:local`` — a candidate only; :func:`is_iri` text (``urn:``, ``http:``) is never prefixed."""
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


def split_iri(iri):
    """IRI → ``(namespace, local name)``, lossless (``namespace + local == iri``): cut after
    the last ``#`` or ``/``, else after a ``urn:``'s last ``:``; neither → ``("", iri)``.
    None → ``(None, None)``. The one split every name rule uses (flavors: :data:`SPLIT_RE`).

    The CIM XML parsers get KEY and ``Type`` VALUE from element tags, where XML has already
    split the QName (lxml ``{ns}local``, pugixml ``prefix:local``); this is the same split
    for an IRI string — an XML local name holds neither ``#`` nor ``/``.
    """
    if iri is None:
        return None, None
    iri = str(iri)
    cut = max(iri.rfind("#"), iri.rfind("/"))
    if cut < 0 and iri.startswith("urn:"):
        cut = iri.rfind(":")
    return iri[:cut + 1], iri[cut + 1:]


def local_key(iri, type_key=TYPE_KEY):
    """Predicate → KEY: ``rdf:type`` → *type_key*, else the :func:`split_iri` local name."""
    if iri is None:
        return None
    return type_key if str(iri) == RDF_TYPE else split_iri(iri)[1]


VALUE_KINDS = ("class", "enum", "reference")
"""``local_value`` kinds: ``class`` (a ``Type`` VALUE) and ``enum`` are names,
``reference`` a node; ``None`` guesses without a schema."""


def local_value(iri, kind=None):
    """VALUE IRI → local VALUE by *kind*:

    - ``"class"`` / ``"enum"`` → a name: the :func:`split_iri` local name.
    - ``"reference"`` → a node: :func:`local_id` (other namespaces stay whole).
    - ``None`` — no schema, guess: :func:`local_id`, then an ``http(s)`` IRI with a ``#`` is
      taken as a name (enum / class: ``Kind.value``, its :func:`split_iri` local name);
      anything else stays whole (``/``-only IRIs are external references). None → None.
    """
    if iri is None:
        return None
    if kind in ("class", "enum"):
        return split_iri(iri)[1]
    iri = local_id(iri)
    if kind is None and iri.startswith("http") and "#" in iri:
        return split_iri(iri)[1]
    return iri


def is_iri(text):
    """Absolute IRI already (``http://``, ``https://``, ``urn:``)."""
    return text is not None and str(text).startswith(URI_PREFIXES)


def compact_iri(iri, prefix_of):
    """Absolute ``http(s)`` IRI → ``prefix:local`` when *prefix_of* (namespace → prefix) binds
    its :func:`split_iri` namespace; anything else (URNs, unbound namespaces) unchanged."""
    if iri is None or not str(iri).startswith(("http://", "https://")):
        return iri
    namespace, name = split_iri(iri)
    prefix = prefix_of.get(namespace)
    return f"{prefix}:{name}" if prefix else iri


def expand_iri(text, prefixes):
    """``prefix:local`` → namespace + local when *prefixes* (prefix → namespace) binds the
    prefix; local names, absolute IRIs (``urn:`` included) and unbound prefixes unchanged."""
    if text is None or not prefixes or is_iri(text):
        return text
    text = str(text)
    match = PREFIXED_RE.match(text)
    namespace = prefixes.get(match.group(1)) if match else None
    return namespace + text[len(match.group(1)) + 1:] if namespace else text


def schema_name(text, namespaces, prefixes=None):
    """The schema's local name for *text* in any form, or None: a local name the schema
    declares as is; else ``prefix:local`` / an absolute IRI whose namespace equals the schema
    entry's namespace for that local name (so ``rdf:type`` never matches ``dcterms:type``)."""
    if text is None:
        return None
    text = str(text)
    if text in namespaces:
        return text
    expanded = expand_iri(text, prefixes)
    if not str(expanded).startswith(("http://", "https://")):
        return None
    namespace, name = split_iri(expanded)
    return name if namespaces.get(name) == namespace else None


def is_absolute(text):
    """Absolute for resolution and the RDFS tools: :func:`is_iri`, or any ``scheme://`` IRI
    (``file://`` too). A prefixed name (``cim:Foo``) or ``M:1..1`` is not."""
    return is_iri(text) or (text is not None and "://" in str(text))


def resolve_iri(reference, base):
    """RDF/XML reference → absolute IRI against *base* (``xml:base``, else the document URI),
    RFC 3986: an absolute IRI passes through, ``#frag`` / ``""`` → base without its fragment
    + reference, else a relative-path join. No *base* → unchanged. None → None."""
    if reference is None or not base or is_iri(reference):
        return reference
    if reference == "" or reference.startswith("#"):
        return base.split("#", 1)[0] + reference
    return urljoin(base, reference)


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
    return RDF_TYPE if key == TYPE_KEY else absolute_name(key, namespaces, undefined_namespace)


def defined(key, value, namespaces=None, value_types=None):
    """Does the schema account for this row? ``Type`` → the class is declared; else the KEY is
    declared and, for an enum KEY, so is the value. Without a schema nothing is defined."""
    namespaces, value_types = namespaces or {}, value_types or {}
    if key == TYPE_KEY:
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
    if key == TYPE_KEY:
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
