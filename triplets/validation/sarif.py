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
from .locations import LOCATION_COLUMNS, locate_violations
from .shacl_ir import _local

logger = logging.getLogger(__name__)

_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
_LEVELS = {"Violation": "error", "Warning": "warning", "Info": "note"}
_SAMPLES = 3   # per end: grouped results carry the first 3 and last 3 instances


def export_to_sarif(violations, data=None, shapes=None, rdf_map=None, group=True,
                    sources=None, path=None, export_to_memory=False):
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
    sources : optional
        The original CIM/XML files (paths/zips/file-likes, same shapes
        read_rdf accepts). When given, the reported instances are located
        in the text (one grep-style pass per file, at export time only) and
        results carry a whole-line physicalLocation.region (startLine ==
        endLine) on the violated property element's line (or the object
        definition's) — what GitHub code scanning needs to annotate lines.
    path : str or Path, optional
        Output file (default "report.sarif"). Ignored with export_to_memory.
    export_to_memory : bool, default False
        Return a BytesIO (with .name) instead of writing to disk.
    """
    if any(source is not None for source in (data, shapes, rdf_map)) \
            and not set(ENRICHMENT_COLUMNS) <= set(violations.columns):
        violations = enrich(violations, data=data, shapes=shapes, rdf_map=rdf_map)

    payload = json.dumps(build_sarif(violations, group=group, sources=sources),
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


def build_sarif(violations, group=True, sources=None):
    """Violations frame → SARIF document dict (I/O only when *sources* is
    given — the locate_violations pass reads just the source files; a frame
    already carrying LOCATION_COLUMNS is used as-is)."""
    frame = violations.copy()
    if sources is not None and not set(LOCATION_COLUMNS) <= set(frame.columns):
        frame = locate_violations(frame, sources)
    for column in (*ENRICHMENT_COLUMNS, *LOCATION_COLUMNS):   # tolerate bare frames
        if column not in frame.columns:
            frame[column] = pandas.NA

    groups = [(key, rows.to_dict("records")) for key, rows
              in frame.groupby(["SOURCE_SHAPE", "VIOLATION_TYPE"], dropna=False, sort=False)]

    rules, results = [], []
    seen_ids = {}
    for (shape, constraint), records in groups:
        rule = _rule(shape, constraint, records, seen_ids)
        rule_index = len(rules)
        rules.append(rule)
        if group:
            results.append(_grouped_result(rule["id"], rule_index, records))
        else:
            results.extend(_result(rule["id"], rule_index, record) for record in records)

    import triplets
    meta = violations.attrs.get("validation", {})
    run = _prune({
        "tool": {"driver": _prune({
            "name": "triplets-shacl",
            "informationUri": "https://github.com/Haigutus/triplets",
            "version": getattr(triplets, "__version__", None),
            "rules": rules,
        })},
        # the validation-run metadata validate() stamps on the frame — the
        # same facts the sh:ValidationReport carries as prov/dcterms terms;
        # timestamps go to the invocation, everything else (engine, duration,
        # shape/constraint counts, coverage) to the run property bag
        "invocations": [_prune({
            "executionSuccessful": True,
            "startTimeUtc": _utc(meta.get("started_at")),
            "endTimeUtc": _utc(meta.get("generated_at")),
        })] if meta.get("generated_at") else None,
        # empty coverage lists stay in — a clean run STATES full coverage
        "properties": {key: value for key, value in meta.items()
                       if key not in ("started_at", "generated_at", "creator")
                       and value is not None} or None,
        "results": results,
    })
    return {
        "$schema": _SCHEMA,
        "version": "2.1.0",
        "runs": [run],
    }


def _samples(records):
    """The instances a grouped result reports: all, or first 3 + last 3."""
    return records if len(records) <= 2 * _SAMPLES else records[:_SAMPLES] + records[-_SAMPLES:]


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
    samples = _samples(records)
    head = ", ".join(filter(None, (_describe(record) for record in samples[:_SAMPLES])))
    tail = ", ".join(filter(None, (_describe(record) for record in samples[_SAMPLES:])))
    described = f"{head} … {tail}" if total > 2 * _SAMPLES else ", ".join(filter(None, (head, tail)))

    message = _message(records[0])
    text = f"{message} — {total} object(s) affected." + (f" Examples: {described}" if described else "")
    locations = [location for location in (_location(record) for record in samples)
                 if location]
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
    """Point at the model element via logicalLocations; the physical side is
    the exact source region when the locate_violations pass found the object
    (LOCATION_COLUMNS on the frame), else just the file the enrichment traced."""
    if pandas.isna(record["ID"]):
        return None
    qualified = (f"{record['OBJECT_TYPE']}/{record['ID']}"
                 if not pandas.isna(record["OBJECT_TYPE"]) else str(record["ID"]))
    location = {"logicalLocations": [_prune({
        "fullyQualifiedName": qualified,
        "name": _value(record["OBJECT_NAME"]) or str(record["ID"]),
        "kind": "object",
    })]}
    if not pandas.isna(record["SOURCE_URI"]):
        # whole-line region, explicitly bounded (endLine given) — columns are
        # per-format anchor rules that broke SARIF viewers for no information
        # gain; a start-only region makes GitHub render from the error onward
        line = int(record["SOURCE_LINE"])
        location["physicalLocation"] = {
            "artifactLocation": {"uri": quote(str(record["SOURCE_URI"]), safe="/")},
            "region": {"startLine": line, "endLine": line},
        }
    elif not pandas.isna(record["INSTANCE_LABEL"]):
        location["physicalLocation"] = {
            "artifactLocation": {"uri": quote(str(record["INSTANCE_LABEL"]))}}
    return location


def _utc(timestamp):
    """ISO timestamp → the Z-suffixed form SARIF expects (None passes through)."""
    return timestamp.replace("+00:00", "Z") if timestamp else None


def _value(cell):
    return None if pandas.isna(cell) else str(cell)


def _text(cell):
    return None if pandas.isna(cell) else {"text": str(cell)}


def _prune(mapping):
    return {key: value for key, value in mapping.items() if value is not None}
