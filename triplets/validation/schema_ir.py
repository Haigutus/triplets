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
import logging

from dataclasses import dataclass, field

import pandas

from .._header import _profile_identity_index
from ..iri import CIM_NS, TYPE_KEY, absolute_name, is_absolute, load_schema, local_value, namespaces, rdf_map_entries
from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE

logger = logging.getLogger(__name__)

_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")

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
    schema, digest, source = load_schema(rdf_map)
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


def subclass_triples(rdf_map, undefined_namespace=CIM_NS):
    """``(child_iri, parent_iri)`` for each ``inheritance`` step.

    A child uses its own Class entry's namespace. Parents are absolute IRIs in the
    shipped bundles; a local one resolves like the child, and an abstract parent with
    no Class entry (``Equipment``, ``IdentifiedObject``, …) takes *undefined_namespace*
    (CIM100), so an NC class in ``cim4.eu`` still subclasses ``CIM100#Equipment``.
    """
    names = namespaces(rdf_map)

    def to_iri(name):
        return name if is_absolute(name) else absolute_name(local_value(name, "class"), names, undefined_namespace)

    return list(dict.fromkeys(
        (child, parent)
        for name, entry in rdf_map_entries(rdf_map) if entry.get("type") == "Class"
        for child in (to_iri(name),)
        for parent in map(to_iri, entry.get("inheritance", ()))
        if parent != child))


def expand_type_index(exact, schema):
    """``{Type value: id-collection}`` plus ancestor keys → union of descendants.

    *exact* is the per-``Type`` grouping already built from instance data.
    Abstract names (``Equipment``) gain an entry; concrete names are unchanged
    (their descendant set is themselves). Collections are concatenated in the
    caller's type (list / ndarray / Series) — the caller unique-s them.
    """
    expanded = dict(exact)
    for ancestor, leaves in descendants(schema).items():
        parts = [exact[leaf] for leaf in leaves if leaf in exact]
        # instances typed as the abstract ancestor itself (Type=Equipment)
        if ancestor in exact and ancestor not in leaves:
            parts = [exact[ancestor], *parts]
        if parts:
            expanded[ancestor] = parts[0] if len(parts) == 1 else parts
    return expanded


def descendants(schema):
    """ancestor local name → frozenset of concrete Class names inheriting it."""
    return {key: frozenset(names) for key, names in _concrete_index(schema).items()}


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
                    concrete.setdefault(local_value(ancestor, "class"), set()).add(name)
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
            rows.append({**meta, "component": "sh:closed", "params": [*parameters, TYPE_KEY]})
    classes = {name for name, entry in entries.items()
               if isinstance(entry, dict) and entry.get("type") == "Class"}
    abstracts = sorted({local_value(ancestor, "class") for name in classes
                        for ancestor in entries[name].get("inheritance", ())
                        if local_value(ancestor, "class") not in classes})
    if abstracts:
        # deny-list: Type in a known abstract (not a Class entry). Unknown
        # types and extra concrete types on a multi-profile instance are silent
        # — combined EQ+SSH files would otherwise fail SSH's allow-list.
        type_meta = {
            "shape_id": f"urn:triplets:schema#{section}:Type", "target_class": TYPE_KEY,
            "target_kind": "subjectsOf", "path": TYPE_KEY, "inverse": False, "via_type": False,
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
        targets = concrete.get(local_value(entry.get("range", ""), "class"), ())
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


