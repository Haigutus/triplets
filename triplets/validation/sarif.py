"""SARIF 2.1.0 export of validation results.

Turns the canonical violations frame into a SARIF log any SARIF consumer can
ingest (GitHub, SonarQube, VS Code viewers, ...). Grouping is the default:
a model with 100k identical findings is *one* issue — one result per rule
with ``occurrenceCount`` and sample instances (first 3 + last 3) — so
reports stay reviewable; ``group=False`` emits one result per violation.

RDF objects have no text coordinates, so results point at the model through
``logicalLocations`` (object id / type / name) plus the source file as
``artifactLocation`` when the enrichment pass can trace it. Passing
data/shapes/rdf_map runs that pass automatically (see context.enrich).
"""
import io
import os
import json
import logging

from urllib.parse import quote

import pandas

from .context import ENRICHMENT_COLUMNS, enrich
from .shacl_ir import _local

logger = logging.getLogger(__name__)

_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
_LEVELS = {"Violation": "error", "Warning": "warning", "Info": "note"}
_SAMPLES = 3   # per end: grouped results carry the first 3 and last 3 instances


def export_to_sarif(violations, data=None, shapes=None, rdf_map=None, group=True,
                    path=None, export_to_memory=False):
    """Violations frame → SARIF 2.1.0 log.

    Parameters
    ----------
    violations : DataFrame in VIOLATION_COLUMNS (any engine's output).
        A frame already carrying the enrichment columns is used as-is.
    data, shapes, rdf_map : optional
        Run the context enrichment pass first (see context.enrich) — fills
        object names, source files and shape/schema descriptions into the
        log. Pass the same shapes object the validation ran with.
    group : bool, default True
        One result per rule (occurrenceCount + sample instances) instead of
        one result per violation row.
    path : str or Path, optional
        Output file (default "report.sarif"). Ignored with export_to_memory.
    export_to_memory : bool, default False
        Return a BytesIO (with .name) instead of writing to disk.
    """
    if any(source is not None for source in (data, shapes, rdf_map)) \
            and not set(ENRICHMENT_COLUMNS) <= set(violations.columns):
        violations = enrich(violations, data=data, shapes=shapes, rdf_map=rdf_map)

    payload = json.dumps(build_sarif(violations, group=group),
                         ensure_ascii=False, indent=2).encode("utf-8")
    if export_to_memory:
        buffer = io.BytesIO(payload)
        buffer.name = "report.sarif"
        return buffer

    path = "report.sarif" if path is None else os.fspath(path)
    with open(path, "wb") as file:
        file.write(payload)
    logger.info("Saved %s", path)
    return path


def build_sarif(violations, group=True):
    """Violations frame → SARIF document dict (pure, I/O-free)."""
    frame = violations.copy()
    for column in ENRICHMENT_COLUMNS:                # tolerate un-enriched frames
        if column not in frame.columns:
            frame[column] = pandas.NA

    rules, results = [], []
    seen_ids = {}
    for (shape, constraint), rows in frame.groupby(["SOURCE_SHAPE", "VIOLATION_TYPE"],
                                                   dropna=False, sort=False):
        records = rows.to_dict("records")
        rule = _rule(shape, constraint, records, seen_ids)
        rule_index = len(rules)
        rules.append(rule)
        if group:
            results.append(_grouped_result(rule["id"], rule_index, records))
        else:
            results.extend(_result(rule["id"], rule_index, record) for record in records)

    import triplets
    return {
        "$schema": _SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": _prune({
                "name": "triplets-shacl",
                "informationUri": "https://github.com/Haigutus/triplets",
                "version": getattr(triplets, "__version__", None),
                "rules": rules,
            })},
            "results": results,
        }],
    }


def _rule(shape, constraint, records, seen_ids):
    identifier = f"{_local(str(shape))}/{constraint}" if not pandas.isna(shape) else str(constraint)
    if identifier in seen_ids:                       # distinct shapes, same local name
        seen_ids[identifier] += 1
        identifier = f"{identifier}-{seen_ids[identifier]}"
    else:
        seen_ids[identifier] = 1

    first = records[0]
    return _prune({
        "id": identifier,
        "name": _value(first["SHAPE_NAME"]),
        "shortDescription": _text(first["SHAPE_NAME"]),
        "fullDescription": _text(first["SHAPE_DESCRIPTION"]),
        "helpUri": str(shape) if not pandas.isna(shape) and str(shape).startswith("http") else None,
        "defaultConfiguration": {"level": _LEVELS.get(_value(first["SEVERITY"]), "warning")},
        "properties": _prune({"sourceShape": _value(shape), "constraint": str(constraint)}),
    })


def _grouped_result(rule_id, rule_index, records):
    total = len(records)
    samples = records if total <= 2 * _SAMPLES else records[:_SAMPLES] + records[-_SAMPLES:]
    described = ", ".join(filter(None, (_describe(record) for record in samples)))
    if total > 2 * _SAMPLES:
        first = ", ".join(filter(None, (_describe(r) for r in records[:_SAMPLES])))
        last = ", ".join(filter(None, (_describe(r) for r in records[-_SAMPLES:])))
        described = f"{first} … {last}"

    message = _message(records[0])
    text = f"{message} — {total} object(s) affected." + (f" Examples: {described}" if described else "")
    locations = [location for location in (_location(record) for record in samples) if location]
    return _prune({
        "ruleId": rule_id,
        "ruleIndex": rule_index,
        "level": _LEVELS.get(_value(records[0]["SEVERITY"]), "warning"),
        "message": {"text": text},
        "occurrenceCount": total,
        "locations": locations or None,
        "properties": _prune({
            "count": total,
            "key": _value(records[0]["KEY"]),
            "sampleIDs": [record["ID"] for record in samples if not pandas.isna(record["ID"])] or None,
            "sampleValues": sorted({str(record["VALUE"]) for record in samples
                                    if not pandas.isna(record["VALUE"])}) or None,
            "violationType": _value(records[0]["VIOLATION_TYPE"]),
            "sourceShape": _value(records[0]["SOURCE_SHAPE"]),
            "schemaDescription": _value(records[0]["SCHEMA_DESCRIPTION"]),
            "schemaMultiplicity": _value(records[0]["SCHEMA_MULTIPLICITY"]),
            "classDescription": _value(records[0]["CLASS_DESCRIPTION"]),
        }),
    })


def _result(rule_id, rule_index, record):
    location = _location(record)
    return _prune({
        "ruleId": rule_id,
        "ruleIndex": rule_index,
        "level": _LEVELS.get(_value(record["SEVERITY"]), "warning"),
        "message": {"text": _message(record)},
        "locations": [location] if location else None,
        "properties": _prune({
            "id": _value(record["ID"]),
            "key": _value(record["KEY"]),
            "value": _value(record["VALUE"]),
            "violationType": _value(record["VIOLATION_TYPE"]),
            "sourceShape": _value(record["SOURCE_SHAPE"]),
            "instanceId": _value(record["INSTANCE_ID"]),
            "objectType": _value(record["OBJECT_TYPE"]),
            "objectName": _value(record["OBJECT_NAME"]),
            "schemaDescription": _value(record["SCHEMA_DESCRIPTION"]),
            "schemaMultiplicity": _value(record["SCHEMA_MULTIPLICITY"]),
            "classDescription": _value(record["CLASS_DESCRIPTION"]),
        }),
    })


def _message(record):
    if not pandas.isna(record["MESSAGE"]) and record["MESSAGE"]:
        return str(record["MESSAGE"])
    key = _value(record["KEY"])
    text = f"{key}: " if key else ""
    text += f"{record['VIOLATION_TYPE']} constraint violated"
    if not pandas.isna(record["VALUE"]):
        text += f" — value '{record['VALUE']}'"
    return text


def _describe(record):
    """Human-readable instance sample: 'Type ID (name)'."""
    if pandas.isna(record["ID"]):
        return None
    parts = []
    if not pandas.isna(record["OBJECT_TYPE"]):
        parts.append(str(record["OBJECT_TYPE"]))
    parts.append(str(record["ID"]))
    if not pandas.isna(record["OBJECT_NAME"]):
        parts.append(f"({record['OBJECT_NAME']})")
    return " ".join(parts)


def _location(record):
    """RDF objects have no text coordinates — point at the model element via
    logicalLocations, plus the source file when the enrichment traced it.
    TODO: emit physicalLocation.region (line/column inside the source XML)
    once the parser records element positions."""
    if pandas.isna(record["ID"]):
        return None
    qualified = (f"{record['OBJECT_TYPE']}/{record['ID']}"
                 if not pandas.isna(record["OBJECT_TYPE"]) else str(record["ID"]))
    location = {"logicalLocations": [_prune({
        "fullyQualifiedName": qualified,
        "name": _value(record["OBJECT_NAME"]) or str(record["ID"]),
        "kind": "object",
    })]}
    if not pandas.isna(record["INSTANCE_LABEL"]):
        location["physicalLocation"] = {
            "artifactLocation": {"uri": quote(str(record["INSTANCE_LABEL"]))}}
    return location


def _value(cell):
    return None if pandas.isna(cell) else str(cell)


def _text(cell):
    return None if pandas.isna(cell) else {"text": str(cell)}


def _prune(mapping):
    return {key: value for key, value in mapping.items() if value is not None}
