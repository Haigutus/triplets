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
from .locations import LOCATION_COLUMNS, locate_violations, relative_name
from .shacl_report import _expand, message_prefix, profile_names, profile_text
from ..iri import split_iri

logger = logging.getLogger(__name__)

_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
_LEVELS = {"Violation": "error", "Warning": "warning", "Info": "note"}
_SAMPLES = 3   # per end: grouped results carry the first 3 and last 3 instances
_TAGS = 10     # GitHub keeps the first 10 tags of a rule
# run-metadata list → descriptor id of its "not evaluated" configuration notifications
_SKIPPED = {"skipped_shapes": "triplets/skippedShape",
            "skipped_components": "triplets/skippedComponent"}
# violation-type prefix → where the constraint is defined (the term IRI otherwise)
_CONSTRAINT_DOCS = {"sh": "https://www.w3.org/TR/shacl/#{local}",
                    "triplets": "https://github.com/Haigutus/triplets/blob/main/docs/validation.md"}


def export_to_sarif(violations, data=None, shapes=None, rdf_map=None, group=True,
                    sources=None, path=None, export_to_memory=False, source_root=None):
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
    source_root : str or Path, optional
        Source file names under this directory are written relative to it
        (forward slashes): the paths in *sources* the locate pass reads, and
        the absolute file names the frame carries (INSTANCE_LABEL, the run
        metadata's ``source`` / ``references``). Paths outside it — logged in
        one warning — relative names, zip members, file-likes and the
        SOURCE_URI of a frame already carrying LOCATION_COLUMNS keep their
        names (see locations.relative_name).
    """
    if any(source is not None for source in (data, shapes, rdf_map)) \
            and not set(ENRICHMENT_COLUMNS) <= set(violations.columns):
        violations = enrich(violations, data=data, shapes=shapes, rdf_map=rdf_map)

    payload = json.dumps(build_sarif(violations, group=group, sources=sources, source_root=source_root),
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


def build_sarif(violations, group=True, sources=None, source_root=None):
    """Violations frame → SARIF document dict (I/O only when *sources* is
    given — the locate_violations pass reads just the source files; a frame
    already carrying LOCATION_COLUMNS is used as-is). *source_root*: file
    names under it are written relative to it (see export_to_sarif)."""
    root = None if source_root is None else os.path.abspath(source_root)
    frame = violations.copy()
    if sources is not None and not set(LOCATION_COLUMNS) <= set(frame.columns):
        frame = locate_violations(frame, sources, root)
    for column in (*ENRICHMENT_COLUMNS, *LOCATION_COLUMNS):   # tolerate bare frames
        if column not in frame.columns:
            frame[column] = pandas.NA

    meta = violations.attrs.get("validation", {})
    if root is not None:     # names from the data and the run metadata: absolute paths under the root
        relative = lambda name: relative_name(str(name), root) or name  # noqa: E731
        frame["INSTANCE_LABEL"] = frame["INSTANCE_LABEL"].map(relative, na_action="ignore")
        meta = {**meta, **{key: [relative(name) for name in meta[key]]
                           for key in ("source", "references") if meta.get(key)}}
    language = meta.get("language", "shacl")
    groups = [(key, rows.to_dict("records")) for key, rows
              in frame.groupby(["SOURCE_SHAPE", "VIOLATION_TYPE"], dropna=False, sort=False)]

    rules, results = [], []
    seen_ids = {}
    for (shape, constraint), records in groups:
        rule = _rule(shape, constraint, records, seen_ids, occurrences=len(records) if group else None,
                     language=language, references=meta.get("references"))
        rule_index = len(rules)
        rules.append(rule)
        if group:
            results.append(_grouped_result(rule["id"], rule_index, records, language))
        else:
            results.extend(_result(rule["id"], rule_index, record, language)
                           for record in records)

    import triplets
    # GitHub displays a result only if it has a location: rows with neither a
    # focus ID nor stamped source columns fall back to a whole-file artifact —
    # the shapes file for tool findings (triplets:*), else the first data file
    for result in results:
        if "locations" not in result:
            tool_finding = str(result.get("properties", {})
                               .get("violationType", "")).startswith("triplets:")
            names = meta.get("references") if tool_finding else meta.get("source")
            if names:
                result["locations"] = [{"physicalLocation": _artifact_location(names[0])}]
    # what this run did not evaluate (shapes, components): configuration
    # notifications — the shapes graph / schema is the tool's rule configuration
    notifications = [{"descriptor": {"id": descriptor}, "level": "warning", "message": {"text": str(text)}}
                     for key, descriptor in _SKIPPED.items() for text in meta.get(key) or ()]
    run = _prune({
        "tool": {"driver": _prune({
            "name": f"triplets-{language}",
            "informationUri": "https://github.com/Haigutus/triplets",
            "version": getattr(triplets, "__version__", None),
            "rules": rules,
        })},
        # the validation-run metadata the validation functions stamp on the frame — the
        # same facts the sh:ValidationReport carries as prov/dcterms terms;
        # timestamps go to the invocation, everything else (engine, duration,
        # shape/constraint counts, coverage) to the run property bag
        "invocations": [_prune({
            "executionSuccessful": True,
            "startTimeUtc": _utc(meta.get("started_at")),
            "endTimeUtc": _utc(meta.get("generated_at")),
            "toolConfigurationNotifications": notifications or None,
        })] if meta.get("generated_at") or notifications else None,
        # empty coverage lists stay in — a clean run STATES full coverage
        "properties": {key: value for key, value in meta.items()
                       if key not in ("started_at", "generated_at", "creator",
                                      "source_shapes")   # in-memory graphs, SHACL report only
                       and value is not None} or None,
        "results": results,
    })
    return {
        "$schema": _SCHEMA,
        "version": "2.1.0",
        "runs": [run],
    }


def _fallback_name(shape, first, language):
    """Alert label when the shape carries no sh:name — GitHub otherwise shows
    the raw multi-line message text as the title; the title carries it after
    the language prefix. Schema runs: "<profiles> <Class> <attr>" (the class
    is the shape's local name); unnamed SHACL shapes: "<attr> <violation type>"."""
    key = _value(first["KEY"])
    if language != "shacl":
        profile = profile_text(first.get("PROFILE"))
        cls = None if pandas.isna(shape) else split_iri(str(shape))[1]
        if cls and key and cls.endswith(f".{key}"):             # per-property shape id
            cls = cls[: -len(key) - 1]
        label = (key if key and cls and key.startswith(f"{cls}.")
                 else " ".join(filter(None, (cls, key))))
        return " ".join(filter(None, (profile, label)))
    return " ".join(filter(None, (key, _value(first["VIOLATION_TYPE"])))) or None


def _samples(records):
    """The instances a grouped result reports: all, or first 3 + last 3."""
    return records if len(records) <= 2 * _SAMPLES else records[:_SAMPLES] + records[-_SAMPLES:]


def _rule(shape, constraint, records, seen_ids, occurrences=None, language="shacl", references=None):
    identifier = f"{split_iri(str(shape))[1]}/{constraint}" if not pandas.isna(shape) else str(constraint)
    if identifier in seen_ids:                       # distinct shapes, same local name
        seen_ids[identifier] += 1
        identifier = f"{identifier}-{seen_ids[identifier]}"
    else:
        seen_ids[identifier] = 1

    first = records[0]
    # the rule title — what GitHub shows as the alert name — names the
    # constraint language; grouped runs add the occurrence count (the ruleId
    # stays stable, so alerts still match)
    name = _value(first["SHAPE_NAME"]) or _fallback_name(shape, first, language)
    name = f"{language.upper()} {name}" if name else None
    if name and occurrences:
        name = f"{name} ({occurrences}×)"
    uri = str(shape) if not pandas.isna(shape) and str(shape).startswith("http") else None
    # GitHub filters alerts by tag: language, constraint, the rule's profiles
    profiles = (profile for record in records for profile in profile_names(record.get("PROFILE")))
    tags = list(dict.fromkeys(filter(None, (language, _value(constraint), *profiles))))[:_TAGS]
    return _prune({
        "id": identifier,
        "name": name,
        "shortDescription": {"text": name} if name else None,
        "fullDescription": _text(first["SHAPE_DESCRIPTION"]),
        "helpUri": uri,
        "help": _help(first, uri, constraint, references),
        "defaultConfiguration": {"level": _LEVELS.get(_value(first["SEVERITY"]), "warning")},
        "properties": _prune({"sourceShape": _value(shape), "constraint": str(constraint),
                              "tags": tags or None}),
    })


def _help(record, shape_iri, constraint, references=None):
    """Rule help (text + markdown) — the panel under a GitHub alert: what the
    constraint checks (the shape's own sh:message, path, expected value), the
    schema context (when enriched with the rdf_map) and the source (shape IRI,
    profiles, the single shapes file, the constraint's definition). Lines
    without data are left out, sections without lines dropped."""
    path, constraint = _value(record["KEY"]), _value(constraint)
    message = _value(record["MESSAGE"]) if _value(record.get("MESSAGE_SOURCE")) == "shacl" else None
    multiplicity = _value(record["SCHEMA_MULTIPLICITY"])
    schema = " ".join(filter(None, (_value(record["SCHEMA_DESCRIPTION"]),
                                    multiplicity and f"[{multiplicity}]")))
    files = [name for name in references or () if name != "rdf_map"]   # a dict schema loads as "rdf_map"
    link = _constraint_link(constraint) if constraint else None
    sections = {
        "What this checks": [_line(None, message and " ".join(message.split())),   # one list item
                             _line("Path", path, f"`{path}`"),
                             _line("Expected", _value(record.get("EXPECTED")))],
        "Schema context": [_line(None, schema),
                           _line("Class", _value(record["CLASS_DESCRIPTION"]))],
        "Source": [_line("Shape", shape_iri),
                   _line("Profile", profile_text(record.get("PROFILE"))),
                   _line("File", files[0] if len(files) == 1 else None),
                   _line("Constraint", link and f"{constraint} — {link}", f"[{constraint}]({link})")],
    }
    sections = [(heading, [line for line in lines if line]) for heading, lines in sections.items()]
    sections = [(heading, lines) for heading, lines in sections if lines]
    if not sections:
        return None
    return {
        "text": "\n\n".join("\n".join([heading, *(text for text, _ in lines)])
                             for heading, lines in sections),
        # one list item per line: a single newline is a soft break in markdown
        "markdown": "\n\n".join("\n".join([f"### {heading}", *(f"- {markdown}" for _, markdown in lines)])
                                 for heading, lines in sections),
    }


def _line(label, value, markdown=None):
    """One help line as (text, markdown); None without a value."""
    if not value:
        return None
    prefix = f"{label}: " if label else ""
    return f"{prefix}{value}", f"{prefix}{markdown or value}"


def _constraint_link(constraint):
    """The definition of a violation type: the SHACL spec section of an sh:*
    component, the triplets docs for a tool finding, else the term IRI itself."""
    uri = _expand(constraint)
    template = _CONSTRAINT_DOCS.get(constraint.partition(":")[0], "{uri}")
    return template.format(uri=uri, local=split_iri(uri)[1])


def _grouped_result(rule_id, rule_index, records, language="shacl"):
    total = len(records)
    samples = _samples(records)
    head = ", ".join(filter(None, (_describe(record) for record in samples[:_SAMPLES])))
    tail = ", ".join(filter(None, (_describe(record) for record in samples[_SAMPLES:])))
    described = f"{head} … {tail}" if total > 2 * _SAMPLES else ", ".join(filter(None, (head, tail)))

    # newline-separated blocks, each prefixed with its origin — same tags as
    # the sh:ValidationReport's resultMessages. Shape-level notes (e.g.
    # triplets:invalidSparql, ID always null) are not about affected objects,
    # so they carry no [context_count]/[context_examples] blocks.
    blocks = _message_blocks(records[0], targets=[r.get("TARGET") for r in samples],
                             language=language)
    if any(not pandas.isna(record["ID"]) for record in records):
        blocks.append(f"[context_count] {total} object(s) affected")
        if described:
            blocks.append(f"[context_examples] {described}")
    text = "\n".join(blocks)
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
            "profile": profile_text(records[0].get("PROFILE")),
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


def _result(rule_id, rule_index, record, language="shacl"):
    location = _location(record)
    return _prune({
        "ruleId": rule_id,
        "ruleIndex": rule_index,
        "level": _LEVELS.get(_value(record["SEVERITY"]), "warning"),
        "message": {"text": "\n".join(_message_blocks(record, language=language, value=True))},
        "locations": [location] if location else None,
        "properties": _prune({
            "id": _value(record["ID"]),
            "profile": profile_text(record.get("PROFILE")),
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


def _message_blocks(record, targets=None, language="shacl", value=False):
    """The prefixed message lines a result carries: the constraint message
    verbatim ([shacl_message]/[engine_message] — exactly one of the two), the violated
    path ([shacl_path]), what the constraint requires ([shacl_expected]), the referenced
    object's state ([context_message] — per group: the distinct ones) and the schema
    definition ([schema_property]) when enriched — file position and snippet go to
    physicalLocation, shape description to the rule."""
    blocks = [f"{message_prefix(record['VIOLATION_TYPE'], record.get('MESSAGE_SOURCE'), language)} "
              f"{_message(record)}"]
    if profile_text(record.get("PROFILE")) is not None:
        blocks.append(f"[{language}_profile] {profile_text(record['PROFILE'])}")
    if not pandas.isna(record["KEY"]):
        blocks.append(f"[{language}_path] {record['KEY']}")
    if value and not pandas.isna(record["VALUE"]):       # grouped: values ride the examples
        blocks.append(f"[context_value] {record['VALUE']}")
    if not pandas.isna(record.get("EXPECTED")):
        blocks.append(f"[{language}_expected] {record['EXPECTED']}")
    targets = [record.get("TARGET")] if targets is None else targets
    targets = list(dict.fromkeys(t for t in targets if not pandas.isna(t)))
    if len(targets) > 3:                                 # grouped: keep the line readable
        targets = targets[:3] + [f"… ({len(targets)} distinct)"]
    if targets:
        blocks.append(f"[context_message] {'; '.join(targets)}")
    if not pandas.isna(record["SCHEMA_DESCRIPTION"]):
        multiplicity = (f" [{record['SCHEMA_MULTIPLICITY']}]"
                        if not pandas.isna(record["SCHEMA_MULTIPLICITY"]) else "")
        blocks.append(f"[schema_property] {record['SCHEMA_DESCRIPTION']}{multiplicity}")
    if not pandas.isna(record["CLASS_DESCRIPTION"]):
        blocks.append(f"[schema_class] {record['CLASS_DESCRIPTION']}")
    return blocks


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
    """One grouped-example entry: object identity plus the offending value —
    the validator sees WHICH object carries WHICH bad value."""
    if pandas.isna(record["ID"]):
        return None
    parts = []
    if not pandas.isna(record["OBJECT_TYPE"]):
        parts.append(str(record["OBJECT_TYPE"]))
    parts.append(str(record["ID"]))
    if not pandas.isna(record["OBJECT_NAME"]):
        parts.append(f"({record['OBJECT_NAME']})")
    if not pandas.isna(record["VALUE"]):
        parts.append(f"= '{record['VALUE']}'")
    return " ".join(parts)


def _location(record):
    """Assemble a location from whatever the row carries: the model element
    (logicalLocations — needs a focus ID) and/or the source position
    (physicalLocation — the locate pass columns work even for null-ID rows,
    e.g. externally stamped shape-level notes). GitHub displays a result only
    with at least one location, region.startLine included — build_sarif adds
    a run-level artifact fallback for rows that carry neither."""
    location = {}
    if not pandas.isna(record["ID"]):
        qualified = (f"{record['OBJECT_TYPE']}/{record['ID']}"
                     if not pandas.isna(record["OBJECT_TYPE"]) else str(record["ID"]))
        location["logicalLocations"] = [_prune({
            "fullyQualifiedName": qualified,
            "name": _value(record["OBJECT_NAME"]) or str(record["ID"]),
            "kind": "object",
        })]
    if not pandas.isna(record["SOURCE_URI"]):
        # whole-line region, explicitly bounded (endLine given) — columns are
        # per-format anchor rules that broke SARIF viewers for no information
        # gain; a start-only region makes GitHub render from the error onward
        line = int(record["SOURCE_LINE"])
        region = {"startLine": line, "endLine": line}
        if not pandas.isna(record.get("SOURCE_SNIPPET")):
            region["snippet"] = {"text": str(record["SOURCE_SNIPPET"])}
        location["physicalLocation"] = {
            "artifactLocation": {"uri": quote(str(record["SOURCE_URI"]), safe="/")},
            "region": region,
        }
    elif not pandas.isna(record["INSTANCE_LABEL"]):
        location["physicalLocation"] = _artifact_location(str(record["INSTANCE_LABEL"]))
    return location or None


def _artifact_location(uri):
    """Whole-file physicalLocation — GitHub requires a region.startLine, and
    line 1 is the whole-file convention (verified against the SARIF support
    docs: a result without a location does not display at all)."""
    return {"artifactLocation": {"uri": quote(str(uri), safe="/")},
            "region": {"startLine": 1, "endLine": 1}}


def _utc(timestamp):
    """ISO timestamp → the Z-suffixed form SARIF expects (None passes through)."""
    return timestamp.replace("+00:00", "Z") if timestamp else None


def _value(cell):
    return None if pandas.isna(cell) else str(cell)


def _text(cell):
    return None if pandas.isna(cell) else {"text": str(cell)}


def _prune(mapping):
    return {key: value for key, value in mapping.items() if value is not None}
