"""Schema-based validation: compile the export schema (rdf_map) into engine
IR — cardinality, datatypes and enumerations (``compile_schema``) and the
reference types of associations (``compile_references``), validated by the same
vectorized engines that run SHACL shapes.

``compile_schema`` compiles the profiles of an ``rdf_map_scope``, merged into
one (``triplets.validation.schema_scope``): a CGMES 3.0 EQ file is
``["EQ", "OP", "SC"]``. One profile = that profile's own rules.

The IR keeps the engines' ``sh:*`` dispatch keys (registries stay shared);
results present with vocabulary-accurate types instead — schema validation
does not masquerade as SHACL (constraint language ``"rdfs"``):

    sh:minCount → xsd:minOccurs      sh:maxCount → xsd:maxOccurs
    sh:datatype → xsd:type           sh:in       → rdfs:range
    sh:closed   → schema:domainIncludes
    triplets:range → rdfs:range      triplets:missing (a reference to an object not in the data)

Cardinality comes from the schema's already-resolved ``xsd:minOccours`` /
``xsd:maxOccours`` fields, datatypes from ``xsd:type`` (the lexical registry
consumes that form directly), enumeration membership from ``values``. Class
rules never widen to subclasses: each Class entry already lists its inherited
attributes. No rdflib on this path: ``graph`` is None and the pyshacl engine
refuses schema-compiled shapes.
"""
import logging

import pandas

from ..iri import TYPE_KEY, load_schema, local_value
from .schema_scope import (_PROPERTY_TYPES, SCOPE_KEY, _merged_entries, class_ancestors, require_entries,
                           scope_sections, scoped_rdf_map)
from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE

logger = logging.getLogger(__name__)

# engine dispatch key → presented violation type (vocabulary-accurate)
PRESENTED = {"sh:minCount": "xsd:minOccurs", "sh:maxCount": "xsd:maxOccurs",
             "sh:datatype": "xsd:type", "sh:in": "rdfs:range",
             "triplets:range": "rdfs:range", "sh:closed": "schema:domainIncludes"}


def compile_schema(rdf_map, rdf_map_scope=None, report_undefined_keys=False) -> CompiledShapes:
    """Compile the scoped profiles of an export schema (dict or path), merged into one.

    rdf_map_scope : profile identifier or sequence of them
        Section keys and/or declared profile IRIs; ``"*"`` = every section;
        None = the root entries. A scope without Class entries raises ValueError.
    report_undefined_keys : bool, default False
        False: only the keys the scoped profiles define are checked. True also
        reports keys on an object that no scoped profile defines for its class
        (one schema:domainIncludes check per class).
    """
    schema, digest, source = load_schema(rdf_map)
    sections = scope_sections(schema, rdf_map_scope)
    key = f"schema|{digest}|{sections}|undefined={report_undefined_keys}"
    if key in _COMPILE_CACHE:
        logger.debug("schema compile cache hit: %s", key[:40])
        return _COMPILE_CACHE[key]

    scoped, notes = scoped_rdf_map(schema, rdf_map_scope)
    entries = scoped[SCOPE_KEY]
    require_entries(entries, rdf_map_scope, classes=True)
    ir = pandas.DataFrame(_class_rows(sections, entries, report_undefined_keys), columns=IR_COLUMNS)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    compiled = CompiledShapes(
        graph=None, ir=ir, hash=key, sources=(source,), language="rdfs",
        stats={"node_shapes": int(ir["target_class"].nunique()), "constraints": len(ir),
               "skipped_shapes": notes["skipped_shapes"],
               "relaxed_constraints": notes["relaxed_constraints"], "unknown_components": []})
    _COMPILE_CACHE[key] = compiled
    logger.debug("compiled schema %s: %d constraints", sections, len(ir))
    return compiled


def compile_references(rdf_map, rdf_map_scope, target_rdf_map_scope=(), report_missing_targets=True,
                       inheritance=True) -> CompiledShapes:
    """Compile the association reference checks of the scoped (source) profiles, merged.

    A reference conforms when ANY type of the referenced object (RDF types are
    cumulative — issue #100) is the range class or, with *inheritance*, a class
    whose :func:`~triplets.validation.schema_scope.class_ancestors` over the source
    and target profiles reach it. A range class none of those profiles defines
    cannot be decided: the association is listed in ``skipped_shapes``.

    report_missing_targets : bool, default True
        Also report references whose object is not in the validated data
        (``triplets:missing``); False leaves them silent.
    """
    schema, digest, source = load_schema(rdf_map)
    sources = scope_sections(schema, rdf_map_scope)
    targets = scope_sections(schema, target_rdf_map_scope) if target_rdf_map_scope else []
    if sources is None and targets:
        raise ValueError("target_rdf_map_scope names profiles — name the source profiles too")
    key = (f"references|{digest}|{sources}|{targets}|missing={report_missing_targets}"
           f"|inheritance={inheritance}")
    if key in _COMPILE_CACHE:
        return _COMPILE_CACHE[key]

    entries = _merged_entries(schema, rdf_map_scope)[1]   # the merge's datatype/cardinality notes concern no reference
    require_entries(entries, rdf_map_scope, classes=True)
    combined = None if sources is None else list(dict.fromkeys([*sources, *targets]))
    ancestors = class_ancestors(schema, combined)
    accepted = {}
    for name, steps in ancestors.items():
        accepted.setdefault(name, set()).add(name)
        for parent in (local_value(step, "class") for step in steps) if inheritance else ():
            accepted.setdefault(parent, set()).add(name)
    rows, skipped = [], []
    for meta, prop, entry in _associations(sources, entries):
        classes = accepted.get(local_value(entry.get("range", ""), "class"))
        if not classes:
            skipped.append(f"{meta['target_class']}.{prop}: association range {entry.get('range')!r} "
                           "matches no Class entry of the source or target profiles")
            continue
        rows.append({**meta, "component": "triplets:range", "params": sorted(classes)})
        if report_missing_targets:
            rows.append({**meta, "component": "triplets:missing", "params": sorted(classes)})
    ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    compiled = CompiledShapes(
        graph=None, ir=ir, hash=key, sources=(source,), language="rdfs",
        stats={"node_shapes": int(ir["target_class"].nunique()), "constraints": len(ir),
               # no cardinality or enumeration rows here: nothing the merge relaxes is checked
               "skipped_shapes": skipped, "relaxed_constraints": [], "unknown_components": []})
    _COMPILE_CACHE[key] = compiled
    return compiled


def _associations(sections, entries):
    """(row meta, property, entry) for every Association parameter of the Class entries."""
    for meta, entry in _class_meta(sections, entries):
        for prop in dict.fromkeys(entry.get("parameters", ())):
            prop_entry = entries.get(prop)
            if isinstance(prop_entry, dict) and prop_entry.get("type") == "Association":
                yield {**meta, "path": prop, "shape_id": f"{meta['shape_id']}.{prop}"}, prop, prop_entry


def _shape_prefix(sections):
    """Shape-id namespace of a scope: one per scope, so findings of different
    scopes never share a shape (SARIF groups by shape); the local name is the
    class (``Breaker`` / ``Breaker.IdentifiedObject.name``)."""
    return "urn:triplets:schema" + "".join(f":{section}" for section in sections or ()) + "#"


def _class_meta(sections, entries):
    """(IR row meta, entry) per Class entry of the merged profile."""
    prefix = _shape_prefix(sections)
    for name, entry in entries.items():
        if isinstance(entry, dict) and entry.get("type") == "Class":
            yield {"shape_id": f"{prefix}{name}", "target_class": name,
                   "target_kind": "class", "path": None, "inverse": False, "via_type": False,
                   "component": None, "params": None, "severity": "Violation", "message": None,
                   # no name: the SARIF title synthesizes "RDFS <profile> <Class> <attr>"
                   "name": None, "description": entry.get("description")}, entry


def _class_rows(sections, entries, report_undefined_keys):
    """The merged profile's Class entries → IR rows."""
    rows = []
    for meta, entry in _class_meta(sections, entries):
        parameters = []
        for prop in entry.get("parameters", ()):
            prop_entry = entries.get(prop)
            if isinstance(prop_entry, dict) and prop_entry.get("type") in _PROPERTY_TYPES \
                    and prop not in parameters:
                parameters.append(prop)
                rows.extend(_property_rows(meta, prop, prop_entry))
        if report_undefined_keys and parameters:
            rows.append({**meta, "component": "sh:closed", "params": [*parameters, TYPE_KEY]})
    return rows


def _property_rows(meta, prop, entry):
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
    return rows
