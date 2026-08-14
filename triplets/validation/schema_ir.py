"""Schema-based validation: compile the export schema (rdf_map) into the
engine IR — cardinality, datatypes, enumerations and association ranges
validated by the same vectorized engines that run SHACL shapes.

The IR keeps the engines' ``sh:*`` dispatch keys (registries stay shared);
results present with vocabulary-accurate types instead — schema validation
does not masquerade as SHACL (constraint language ``"rdfs"``):

    sh:minCount → xsd:minOccurs      sh:maxCount → xsd:maxOccurs
    sh:datatype → xsd:type           sh:in       → rdfs:range
    sh:closed   → rdfs:domain

Cardinality comes from the schema's already-resolved ``xsd:minOccours`` /
``xsd:maxOccours`` fields, datatypes from ``xsd:type`` (the lexical registry
consumes that form directly), enumeration membership from ``values``, and
association targets from ``range`` expanded to concrete subclasses via the
classes' ``inheritance`` lists (ranges are often abstract). Association
checks ride the ``via_type`` path — a dangling reference yields no value
node (minOccurs catches absence). No rdflib on this path: ``graph`` is None
and the pyshacl engine refuses schema-compiled shapes.
"""
import hashlib
import json
import logging
import os

import pandas

from .shacl_ir import CompiledShapes, IR_COLUMNS, _COMPILE_CACHE

logger = logging.getLogger(__name__)

_PROPERTY_TYPES = ("Attribute", "Association", "Enumeration")

# engine dispatch key → presented violation type (vocabulary-accurate)
PRESENTED = {"sh:minCount": "xsd:minOccurs", "sh:maxCount": "xsd:maxOccurs",
             "sh:datatype": "xsd:type", "sh:in": "rdfs:range", "sh:closed": "rdfs:domain"}


def compile_schema(rdf_map, closed=False) -> CompiledShapes:
    """Compile the export schema (dict or path) once into engine-ready IR.

    closed=False (default): properties the schema does not define for a class
    are not reported — multi-profile data legitimately unions properties.
    closed=True adds one rdfs:domain check per class.
    """
    schema, digest, source = _load(rdf_map)
    key = f"schema|closed={closed}|{digest}"
    if key in _COMPILE_CACHE:
        logger.debug("schema compile cache hit: %s", key[:30])
        return _COMPILE_CACHE[key]

    rows, skipped = _schema_rows(schema, closed=closed)
    ir = pandas.DataFrame(rows, columns=IR_COLUMNS)
    ir["message"] = ir["message"].astype(object).where(ir["message"].notna(), None)
    compiled = CompiledShapes(
        graph=None, ir=ir, hash=key, sources=(source,), language="rdfs",
        stats={"node_shapes": int(ir["target_class"].nunique()), "constraints": len(ir),
               "skipped_shapes": sorted(skipped), "unknown_components": []})
    _COMPILE_CACHE[key] = compiled
    logger.debug("compiled %d schema constraint rows for %d classes",
                 len(ir), compiled.stats["node_shapes"])
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


def _schema_rows(schema, closed):
    """Walk profile sections → IR rows; (class, property) deduped across
    profiles, first profile wins (same rule as flatten_schema)."""
    classes = {}       # class → {"description", "parameters" (ordered union), "properties" {prop: entry}}
    concrete = {}      # ancestor local name → {concrete classes inheriting it}
    for entries in schema.values():
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("type") != "Class":
                continue
            record = classes.setdefault(name, {"description": entry.get("description"),
                                               "parameters": [], "properties": {}})
            for ancestor in entry.get("inheritance", ()):
                concrete.setdefault(_local(ancestor), set()).add(name)
            for prop in entry.get("parameters", ()):
                if prop not in record["properties"]:
                    prop_entry = entries.get(prop)
                    if isinstance(prop_entry, dict) and prop_entry.get("type") in _PROPERTY_TYPES:
                        record["properties"][prop] = prop_entry
                        record["parameters"].append(prop)

    rows, skipped = [], []
    for name, record in classes.items():
        meta = {"shape_id": f"urn:triplets:schema#{name}", "target_class": name,
                "target_kind": "class", "path": None, "inverse": False, "via_type": False,
                "component": None, "params": None, "severity": "Violation", "message": None,
                "name": f"{name} (schema)", "description": record["description"]}
        for prop, entry in record["properties"].items():
            rows.extend(_property_rows(meta, prop, entry, concrete, skipped))
        if closed:
            rows.append({**meta, "component": "sh:closed",
                         "params": [*record["parameters"], "Type"]})
    return rows, skipped


def _property_rows(meta, prop, entry, concrete, skipped):
    """One property entry → cardinality + value-space IR rows."""
    meta = {**meta, "path": prop}
    rows = []
    low, high = entry.get("xsd:minOccours", ""), entry.get("xsd:maxOccours", "")
    if low.isdigit() and int(low) > 0:
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
            # the referenced object's Type must be a concrete subclass of the
            # range — the via_type path; dangling references yield no value node
            rows.append({**meta, "via_type": True, "component": "sh:in",
                         "params": sorted(targets)})
        else:
            skipped.append(f"{meta['target_class']}.{prop}: association range "
                           f"{entry.get('range')!r} names no known class")
    return rows


def _local(term):
    return str(term).lstrip("#").rsplit("#", 1)[-1].rsplit("/", 1)[-1]
