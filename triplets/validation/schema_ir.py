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

from ..export.cimxml_utils import _profile_identity_index
from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE

logger = logging.getLogger(__name__)

_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")

# engine dispatch key → presented violation type (vocabulary-accurate)
PRESENTED = {"sh:minCount": "xsd:minOccurs", "sh:maxCount": "xsd:maxOccurs",
             "sh:datatype": "xsd:type", "sh:in": "rdfs:range",
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

    concrete = _concrete_index(schema)
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


def _concrete_index(schema):
    """ancestor local name → concrete classes inheriting it, across ALL
    sections — inheritance is model knowledge, not a profile constraint."""
    concrete = {}
    for entries in schema.values():
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if isinstance(entry, dict) and entry.get("type") == "Class":
                for ancestor in entry.get("inheritance", ()):
                    concrete.setdefault(_local(ancestor), set()).add(name)
    return concrete


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


def _local(term):
    return str(term).lstrip("#").rsplit("#", 1)[-1].rsplit("/", 1)[-1]
