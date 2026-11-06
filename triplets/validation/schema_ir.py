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
``xsd:maxOccours`` fields, datatypes from ``xsd:type`` expanded by the schema's
prefixes: an XSD type is checked as such; any other (``cim:ActivePower``) by the
XSD type of its own datatype entry (``dataType``, else the IRI's local name — the
CIMDatatype entry a bundle carries); a type without one is listed in
``skipped_shapes``, not checked. Enumeration membership comes from ``values``. Class
rules never widen to subclasses: each Class entry already lists its inherited
attributes. No rdflib on this path: ``graph`` is None and the pyshacl engine
refuses schema-compiled shapes.
"""
import logging

import pandas

from .._header import _PROPERTY_TYPES
from ..iri import (_PLAIN_DATATYPES, TYPE_KEY, XSD_NS, absolute_datatype, language_tag, load_schema, local_value,
                   prefixes, split_iri)
from .schema_scope import (SCOPE_KEY, _merged_entries, class_ancestors, require_entries,
                           scope_sections, scoped_rdf_map)
from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE

logger = logging.getLogger(__name__)

# engine dispatch key → presented violation type (vocabulary-accurate)
PRESENTED = {"sh:minCount": "xsd:minOccurs", "sh:maxCount": "xsd:maxOccurs",
             "sh:datatype": "xsd:type", "sh:in": "rdfs:range",
             "triplets:range": "rdfs:range", "sh:closed": "schema:domainIncludes"}
_SEVERITIES = ("Violation", "Warning", "Info")


def compile_schema(rdf_map, rdf_map_scope=None, report_undefined_keys=False, severity="Violation") -> CompiledShapes:
    """Compile the scoped profiles of an export schema (dict or path), merged into one.

    rdf_map_scope : profile identifier or sequence of them
        Section keys and/or declared profile IRIs; ``"*"`` = every section;
        None = the root entries. A scope without Class entries raises ValueError.
    report_undefined_keys : bool, default False
        False: only the keys the scoped profiles define are checked. True also
        reports keys on an object that no scoped profile defines for its class
        (one schema:domainIncludes check per class).
    severity : {"Violation", "Warning", "Info"}, default "Violation"
        The severity of every rule.
    """
    _require_severity(severity)
    schema, digest, source = load_schema(rdf_map)
    sections = scope_sections(schema, rdf_map_scope)
    key = f"schema|{digest}|{sections}|undefined={report_undefined_keys}|severity={severity}"
    if key in _COMPILE_CACHE:
        logger.debug("schema compile cache hit: %s", key[:40])
        return _COMPILE_CACHE[key]

    scoped, notes = scoped_rdf_map(schema, rdf_map_scope)
    entries = scoped[SCOPE_KEY]
    require_entries(entries, rdf_map_scope, classes=True)
    bases = _xsd_bases(entries, prefixes(scoped), notes["skipped_shapes"])
    ir = pandas.DataFrame(_class_rows(sections, entries, bases, report_undefined_keys), columns=IR_COLUMNS)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    ir["severity"] = severity
    compiled = CompiledShapes(
        graph=None, ir=ir, hash=key, sources=(source,), language="rdfs",
        stats={"node_shapes": int(ir["target_on"].nunique()), "constraints": len(ir),
               "skipped_shapes": notes["skipped_shapes"],
               "relaxed_constraints": notes["relaxed_constraints"], "unknown_components": []})
    _COMPILE_CACHE[key] = compiled
    logger.debug("compiled schema %s: %d constraints", sections, len(ir))
    return compiled


def compile_references(rdf_map, rdf_map_scope, target_rdf_map_scope=(), report_missing_targets=True,
                       inheritance=True, severity="Violation") -> CompiledShapes:
    """Compile the association reference checks of the scoped (source) profiles, merged.

    A reference conforms when ANY type of the referenced object (RDF types are
    cumulative — issue #100) is the range class or, with *inheritance*, a class
    whose :func:`~triplets.validation.schema_scope.class_ancestors` over the source
    and target profiles reach it. A range class none of those profiles defines
    cannot be decided: the association is listed in ``skipped_shapes``.

    report_missing_targets : bool, default True
        Also report references whose object is not in the validated data
        (``triplets:missing``); False leaves them silent.
    severity : {"Violation", "Warning", "Info"}, default "Violation"
        The severity of every rule.
    """
    _require_severity(severity)
    schema, digest, source = load_schema(rdf_map)
    sources = scope_sections(schema, rdf_map_scope)
    targets = scope_sections(schema, target_rdf_map_scope) if target_rdf_map_scope else []
    if sources is None and targets:
        raise ValueError("target_rdf_map_scope names profiles — name the source profiles too")
    key = (f"references|{digest}|{sources}|{targets}|missing={report_missing_targets}"
           f"|inheritance={inheritance}|severity={severity}")
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
            skipped.append(f"{meta['target_on']}.{prop}: association range {entry.get('range')!r} "
                           "matches no Class entry of the source or target profiles")
            continue
        rows.append({**meta, "component": "triplets:range", "params": sorted(classes)})
        if report_missing_targets:
            rows.append({**meta, "component": "triplets:missing", "params": sorted(classes)})
    ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    ir["severity"] = severity
    compiled = CompiledShapes(
        graph=None, ir=ir, hash=key, sources=(source,), language="rdfs",
        stats={"node_shapes": int(ir["target_on"].nunique()), "constraints": len(ir),
               # no cardinality or enumeration rows here: nothing the merge relaxes is checked
               "skipped_shapes": skipped, "relaxed_constraints": [], "unknown_components": []})
    _COMPILE_CACHE[key] = compiled
    return compiled


def _require_severity(severity):
    if severity not in _SEVERITIES:
        raise ValueError(f"severity must be one of {_SEVERITIES}, not {severity!r}")


def _associations(sections, entries):
    """(row meta, property, entry) for every Association parameter of the Class entries."""
    for meta, entry in _class_meta(sections, entries):
        for prop in dict.fromkeys(entry.get("parameters", ())):
            prop_entry = entries.get(prop)
            if isinstance(prop_entry, dict) and prop_entry.get("type") == "Association":
                shape_id = f"{meta['shape_id']}.{prop}"
                yield {**meta, "path": prop, "shape_id": shape_id, "shapes": (*meta["shapes"], shape_id)}, \
                    prop, prop_entry


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
            yield {"shape_id": f"{prefix}{name}", "shapes": (f"{prefix}{name}",), "target_on": name,
                   "target_by": "Type", "path": None, "inverse": False, "via_type": False,
                   "component": None, "params": None, "message": None,
                   # no name: the SARIF title synthesizes "RDFS <profile> <Class> <attr>"
                   "name": None, "description": entry.get("description")}, entry


def _xsd_bases(entries, prefixes, skipped):
    """Attribute KEY → its ``sh:datatype`` params ``xsd:<local>``: the expanded ``xsd:type``
    when it is an XSD type, else the XSD type of the datatype entry it names (``dataType``,
    else the IRI's local name). A plain literal type or base (``xsd:string``,
    ``rdf:langString``) has nothing to check, nor has a tagged KEY (``language``: its tag and type
    raise as in the export, :func:`~triplets.iri.language_tag`); a KEY with no XSD type is noted
    in *skipped*, once."""
    bases = {}
    for name, entry in entries.items():
        if isinstance(entry, dict) and entry.get("type") == "Attribute" and entry.get("language"):
            language_tag(name, entry["language"], entry.get("xsd:type"), prefixes)
        elif isinstance(entry, dict) and entry.get("type") == "Attribute" and entry.get("xsd:type"):
            declared = str(entry["xsd:type"])
            datatype = absolute_datatype(name, declared, prefixes)
            if datatype not in _PLAIN_DATATYPES and not datatype.startswith(XSD_NS):
                support = entries.get(entry.get("dataType")) or entries.get(split_iri(datatype)[1])
                base = support.get("xsd:type") if isinstance(support, dict) else None
                datatype = absolute_datatype(name, str(base), prefixes) if base else ""
            if datatype in _PLAIN_DATATYPES:
                continue
            if datatype.startswith(XSD_NS):
                bases[name] = "xsd:" + datatype.removeprefix(XSD_NS)
            else:
                skipped.append(f"{name}: datatype {declared} has no xsd base type — not checked")
    return bases


def _class_rows(sections, entries, bases, report_undefined_keys):
    """The merged profile's Class entries → IR rows."""
    rows = []
    for meta, entry in _class_meta(sections, entries):
        parameters = []
        for prop in entry.get("parameters", ()):
            prop_entry = entries.get(prop)
            if isinstance(prop_entry, dict) and prop_entry.get("type") in _PROPERTY_TYPES \
                    and prop not in parameters:
                parameters.append(prop)
                rows.extend(_property_rows(meta, prop, prop_entry, bases.get(prop)))
        if report_undefined_keys and parameters:
            rows.append({**meta, "component": "sh:closed", "params": [*parameters, TYPE_KEY]})
    return rows


def _property_rows(meta, prop, entry, datatype):
    """One property entry (*datatype*: its xsd base type, if any) → cardinality + value-space IR rows.

    Per-property shape_id (like SHACL property shapes): rules and alert
    titles group per (profile, class, property)."""
    shape_id = f"{meta['shape_id']}.{prop}"
    meta = {**meta, "path": prop, "shape_id": shape_id, "shapes": (*meta["shapes"], shape_id)}
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
    if kind == "Attribute" and datatype:
        rows.append({**meta, "component": "sh:datatype", "params": datatype})
    elif kind == "Enumeration" and entry.get("values"):
        rows.append({**meta, "component": "sh:in", "params": list(entry["values"])})
    return rows
