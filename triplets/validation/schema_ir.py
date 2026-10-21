"""Schema-based validation: compile the export schema (rdf_map) into engine
IR — cardinality, datatypes and enumerations validated by the same vectorized
engines that run SHACL shapes.

``compile_schema`` compiles the profiles of an ``rdf_map_scope``, merged into
one (``triplets.validation.schema_scope``): a CGMES 3.0 EQ file is
``["EQ", "OP", "SC"]``. One profile = that profile's own rules.

The IR keeps the engines' ``sh:*`` dispatch keys (registries stay shared);
results present with vocabulary-accurate types instead — schema validation
does not masquerade as SHACL (constraint language ``"rdfs"``):

    sh:minCount → xsd:minOccurs      sh:maxCount → xsd:maxOccurs
    sh:datatype → xsd:type           sh:in       → rdfs:range
    sh:closed   → schema:domainIncludes

Cardinality comes from the schema's already-resolved ``xsd:minOccours`` /
``xsd:maxOccours`` fields, datatypes from ``xsd:type`` (the lexical registry
consumes that form directly), enumeration membership from ``values``. Class
rules never widen to subclasses: each Class entry already lists its inherited
attributes. No rdflib on this path: ``graph`` is None and the pyshacl engine
refuses schema-compiled shapes.
"""
import logging

import pandas

from ..iri import TYPE_KEY, load_schema
from .schema_scope import SCOPE_KEY, scope_sections, scoped_rdf_map
from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE

logger = logging.getLogger(__name__)

_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")

# engine dispatch key → presented violation type (vocabulary-accurate)
PRESENTED = {"sh:minCount": "xsd:minOccurs", "sh:maxCount": "xsd:maxOccurs",
             "sh:datatype": "xsd:type", "sh:in": "rdfs:range",
             "sh:closed": "schema:domainIncludes"}


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

    scoped, conflicts = scoped_rdf_map(schema, rdf_map_scope)
    entries = scoped[SCOPE_KEY]
    if not any(isinstance(entry, dict) and entry.get("type") == "Class" for entry in entries.values()):
        raise ValueError(f"rdf_map_scope {rdf_map_scope!r} selects no Class entries — name the profiles "
                         "(section keys / profile IRIs, or '*' for all)")
    ir = pandas.DataFrame(_class_rows(sections, entries, report_undefined_keys), columns=IR_COLUMNS)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    compiled = CompiledShapes(
        graph=None, ir=ir, hash=key, sources=(source,), language="rdfs",
        stats={"node_shapes": int(ir["target_class"].nunique()), "constraints": len(ir),
               "skipped_shapes": list(conflicts), "unknown_components": []})
    _COMPILE_CACHE[key] = compiled
    logger.debug("compiled schema %s: %d constraints", sections, len(ir))
    return compiled


def _shape_prefix(sections):
    """Shape-id namespace of a scope: one per scope, so findings of different
    scopes never share a shape (SARIF groups by shape); the local name is the
    class (``Breaker`` / ``Breaker.IdentifiedObject.name``)."""
    return "urn:triplets:schema" + "".join(f":{section}" for section in sections or ()) + "#"


def _class_rows(sections, entries, report_undefined_keys):
    """The merged profile's Class entries → IR rows."""
    prefix = _shape_prefix(sections)
    rows = []
    for name, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("type") != "Class":
            continue
        meta = {"shape_id": f"{prefix}{name}", "target_class": name,
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
