"""Schema-based validation: compile the export schema (rdf_map) into engine
IR — cardinality, datatypes, enumerations and association ranges validated by
the same vectorized engines that run SHACL shapes.

The schema JSON is a SET of profiles; ``compile_schema`` compiles every
contained profile separately — one :class:`CompiledShapes` per section,
addressable through the profiles' own declared identifiers (versionIRI /
conformsTo URI, keyword, section key). **Profiles are never merged**: an
instance file is validated per profile it declares, against that profile's
own constraints (``validate_schema`` orchestrates this per INSTANCE_ID).

The IR keeps the engines' ``sh:*`` dispatch keys (registries stay shared);
results present with vocabulary-accurate types instead — schema validation
does not masquerade as SHACL (constraint language ``"rdfs"``):

    sh:minCount → xsd:minOccurs      sh:maxCount → xsd:maxOccurs
    sh:datatype → xsd:type           sh:in       → rdfs:range
    triplets:range → rdfs:range      sh:closed   → schema:domainIncludes

Cardinality comes from the schema's already-resolved ``xsd:minOccours`` /
``xsd:maxOccours`` fields, datatypes from ``xsd:type`` (the lexical registry
consumes that form directly), enumeration membership from ``values``, and
association targets from ``range`` expanded to concrete subclasses via the
classes' ``inheritance`` lists — the expansion index spans ALL sections
(class inheritance is model knowledge, not a profile constraint). A
referenced object conforms when ANY of its types is in the range set;
dangling references are silent. No rdflib on this path: ``graph`` is None
and the pyshacl engine refuses schema-compiled shapes.
"""
import hashlib
import json
import logging
import os

from dataclasses import dataclass, field

import pandas

from .._header import _profile_identity_index
from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE, _local

logger = logging.getLogger(__name__)

_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")
CIM_NS = "http://iec.ch/TC57/CIM100#"

# engine dispatch key → presented violation type (vocabulary-accurate)
PRESENTED = {"sh:minCount": "xsd:minOccurs", "sh:maxCount": "xsd:maxOccurs",
             "sh:datatype": "xsd:type", "sh:in": "rdfs:range", "sh:not": "rdfs:range",
             "triplets:range": "rdfs:range", "sh:closed": "schema:domainIncludes"}


@dataclass
class CompiledSchema:
    """All profiles of one export schema, compiled — never merged.

    ``profiles`` holds one CompiledShapes per section; ``index`` maps every
    identifier a profile declares (versionIRI / conformsTo URI, keyword,
    section key — the same identity the instance headers reference) to its
    section, so instance-declared profile URIs look up directly.
    """

    profiles: dict                # section key → CompiledShapes
    index: dict                   # declared identifier → section key
    hash: str
    sources: tuple = ()
    stats: dict = field(default_factory=dict)   # {"profiles": [...], "skipped_shapes": [...]}

    def get(self, identifier):
        """CompiledShapes for a profile URI / keyword / section key, or None."""
        section = self.index.get(str(identifier))
        return self.profiles.get(section) if section else None

    def section(self, identifier):
        """Section key for any declared identifier, or None."""
        return self.index.get(str(identifier))


def compile_schema(rdf_map, closed=False) -> CompiledSchema:
    """Compile every profile contained in the export schema (dict or path).

    closed=False (default): properties a profile does not define for a class
    are not reported. closed=True adds one schema:domainIncludes check per
    class and profile.
    """
    schema, digest, source = _load(rdf_map)
    key = f"schema|closed={closed}|{digest}"
    if key in _COMPILE_CACHE:
        logger.debug("schema compile cache hit: %s", key[:30])
        return _COMPILE_CACHE[key]

    concrete = descendants(schema)
    profiles, skipped = {}, []
    for section, entries in schema.items():
        if not isinstance(entries, dict):
            continue
        section_skipped = []
        rows = _section_rows(section, entries, concrete, closed, section_skipped)
        if not rows:
            continue
        skipped.extend(f"{section}: {entry}" for entry in section_skipped)
        ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
        ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
        profiles[section] = CompiledShapes(
            graph=None, ir=ir, hash=f"{key}|{section}", sources=(source,), language="rdfs",
            stats={"node_shapes": int(ir["target_class"].nunique()), "constraints": len(ir),
                   "skipped_shapes": sorted(section_skipped), "unknown_components": []})

    compiled = CompiledSchema(profiles=profiles, index=_profile_identity_index(schema),
                              hash=key, sources=(source,),
                              stats={"profiles": sorted(profiles), "skipped_shapes": sorted(skipped)})
    _COMPILE_CACHE[key] = compiled
    logger.debug("compiled %d schema profiles: %s", len(profiles), ", ".join(sorted(profiles)))
    return compiled


def _load(rdf_map):
    """dict or path → (schema dict, content digest, source name)."""
    if isinstance(rdf_map, dict):
        digest = hashlib.sha256(
            json.dumps(rdf_map, sort_keys=True, default=str).encode()).hexdigest()
        return rdf_map, digest, "rdf_map"
    with open(rdf_map, "rb") as file:
        content = file.read()
    return (json.loads(content), hashlib.sha256(content).hexdigest(),
            os.path.basename(str(rdf_map)))


def _norm_ns(namespace):
    """Schema ``namespace`` field → URI prefix ending in ``#`` or ``/``."""
    if not namespace:
        return CIM_NS
    if not str(namespace).endswith(("#", "/")):
        return str(namespace) + "#"
    return str(namespace)


def class_namespaces(schema):
    """Concrete class local name → namespace prefix, across ALL sections.

    Abstract ancestors that are not Class entries are absent — callers fall
    back to :data:`CIM_NS` (CIM abstracts live in the CIM namespace even when
    an NC class inherits them).
    """
    names = {}
    for entries in schema.values():
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if isinstance(entry, dict) and entry.get("type") == "Class" and name not in names:
                names[name] = _norm_ns(entry.get("namespace"))
    return names


def class_iri(name, namespaces, fallback_ns=None):
    """Absolute IRI for a class name or fragment, using *that* class's namespace.

    A full ``http(s):`` URI is returned unchanged. Otherwise the local name is
    looked up in *namespaces* (from :func:`class_namespaces`); missing
    abstracts use *fallback_ns* or :data:`CIM_NS` — never the child's
    namespace.
    """
    if isinstance(name, str) and name.startswith(("http://", "https://")):
        return name
    local = _local(name)
    return (namespaces.get(local) or fallback_ns or CIM_NS) + local


def subclass_triples(schema):
    """``(child_iri, parent_iri)`` for each ``inheritance`` step.

    Each local name is resolved from its own Class entry. An abstract parent
    with no Class entry (``Equipment``, ``IdentifiedObject``, …) uses
    :data:`CIM_NS`, so an NC class in ``cim4.eu`` still subclasses
    ``http://iec.ch/TC57/CIM100#Equipment``.
    """
    namespaces = class_namespaces(schema)
    seen, triples = set(), []
    for entries in schema.values():
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("type") != "Class":
                continue
            child_iri = class_iri(name, namespaces)
            for ancestor in entry.get("inheritance", ()):
                if _local(ancestor) == name:
                    continue
                parent_iri = class_iri(ancestor, namespaces)
                pair = (child_iri, parent_iri)
                if pair not in seen:
                    seen.add(pair)
                    triples.append(pair)
    return triples


def descendants(schema):
    """ancestor local name → frozenset of concrete Class names, across ALL
    sections — inheritance is model knowledge, not a profile constraint.

    Each Class entry's ``inheritance`` list already includes itself, so a
    concrete class maps to at least ``{itself}``. Abstract names (not Class
    entries) map to every concrete descendant that listed them.
    """
    concrete = {}
    for entries in schema.values():
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if isinstance(entry, dict) and entry.get("type") == "Class":
                for ancestor in entry.get("inheritance", ()):
                    concrete.setdefault(_local(ancestor), set()).add(name)
    return {ancestor: frozenset(names) for ancestor, names in concrete.items()}


def bind_inheritance(ir, schema):
    """Fan out ``sh:targetClass`` rows and expand ``sh:class`` params via the
    schema subclass graph. Unknown names stay exact (``{themselves}``).

    *schema* is the loaded export-schema dict (caller already ran ``_load``).
    A NodeShape that already lists both an ancestor and a descendant does not
    double-count — the same signature-dedup parse_ir uses (issue #99).
    """
    if ir is None or ir.empty:
        return ir
    index = descendants(schema)
    expanded = []
    for record in ir.to_dict("records"):
        if record.get("component") == "sh:class" and isinstance(record.get("params"), str):
            record = {**record, "params": sorted(index.get(record["params"], {record["params"]}))}
        if record.get("target_kind", "class") == "class":
            for target in sorted(index.get(record["target_class"], {record["target_class"]})):
                expanded.append({**record, "target_class": target})
        else:
            expanded.append(record)
    out = pandas.DataFrame(expanded, columns=IR_COLUMNS)
    signature = out.assign(params=out["params"].astype(str))
    out = out.loc[~signature.duplicated()].reset_index(drop=True)
    out["message"] = out["message"].astype(object).where(out["message"].notna(), None)
    return out


def _section_rows(section, entries, concrete, closed, skipped):
    """One profile section → IR rows (this profile's constraints only)."""
    rows = []
    for name, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("type") != "Class":
            continue
        meta = {"shape_id": f"urn:triplets:schema#{section}:{name}", "target_class": name,
                "target_kind": "class", "path": None, "inverse": False, "via_type": False,
                "component": None, "params": None, "severity": "Violation", "message": None,
                # no name: the SARIF title synthesizes "RDFS <profile> <Class> <attr>"
                "name": None, "description": entry.get("description")}
        parameters = []
        for prop in entry.get("parameters", ()):
            prop_entry = entries.get(prop)
            if isinstance(prop_entry, dict) and prop_entry.get("type") in _PROPERTY_TYPES \
                    and prop not in parameters:
                parameters.append(prop)
                rows.extend(_property_rows(meta, prop, prop_entry, concrete, skipped))
        if closed and parameters:
            rows.append({**meta, "component": "sh:closed", "params": [*parameters, "Type"]})
    classes = {name for name, entry in entries.items()
               if isinstance(entry, dict) and entry.get("type") == "Class"}
    abstracts = sorted({_local(ancestor) for name in classes
                        for ancestor in entries[name].get("inheritance", ())
                        if _local(ancestor) not in classes})
    if abstracts:
        # deny-list: Type in a known abstract (not a Class entry). Unknown
        # types and extra concrete types on a multi-profile instance are silent
        # — combined EQ+SSH files would otherwise fail SSH's allow-list.
        type_meta = {
            "shape_id": f"urn:triplets:schema#{section}:Type", "target_class": "Type",
            "target_kind": "subjectsOf", "path": "Type", "inverse": False, "via_type": False,
            "component": "sh:not", "params": None, "severity": "Violation",
            "message": None, "name": None, "description": None,
        }
        nested = {**type_meta, "shape_id": f"{type_meta['shape_id']}.in",
                  "component": "sh:in", "params": abstracts}
        rows.append({**type_meta, "params": [nested]})
    return rows


def _property_rows(meta, prop, entry, concrete, skipped):
    """One property entry → cardinality + value-space IR rows.

    Per-property shape_id (like SHACL property shapes): rules and alert
    titles group per (profile, class, property)."""
    meta = {**meta, "path": prop, "shape_id": f"{meta['shape_id']}.{prop}"}
    rows = []
    low, high = entry.get("xsd:minOccours", ""), entry.get("xsd:maxOccours", "")
    if low.isdigit() and int(low) > 0:
        # incl. IdentifiedObject.mRID: the schemas are profile-accurate —
        # CGMES 2.4 declares it 0..1 (not serialized), CGMES 3.0/NCP 1..1
        # (element expected); validate what each profile says, per instance
        rows.append({**meta, "component": "sh:minCount", "params": int(low)})
    if high.isdigit():
        rows.append({**meta, "component": "sh:maxCount", "params": int(high)})

    kind = entry.get("type")
    if kind == "Attribute" and entry.get("xsd:type"):
        rows.append({**meta, "component": "sh:datatype", "params": entry["xsd:type"]})
    elif kind == "Enumeration" and entry.get("values"):
        rows.append({**meta, "component": "sh:in", "params": list(entry["values"])})
    elif kind == "Association":
        targets = concrete.get(_local(entry.get("range", "")), ())
        if targets:
            # ANY of the referenced object's types in the expanded range set
            # conforms (RDF types are cumulative); dangling references are
            # silent (cross-instance references resolve outside the scope)
            rows.append({**meta, "component": "triplets:range",
                         "params": sorted(targets)})
        else:
            skipped.append(f"{meta['target_class']}.{prop}: association range "
                           f"{entry.get('range')!r} names no known class")
    return rows
