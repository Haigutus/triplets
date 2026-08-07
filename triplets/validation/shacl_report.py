"""Map a pyshacl/SHACL ValidationReport graph to the canonical violations DataFrame,
and the inverse: export a violations DataFrame as a standard sh:ValidationReport.

Canonical violations schema (identical across all current and future SHACL
engines, so the later vectorized engines can produce it natively):
    [ID, KEY, VALUE, VIOLATION_TYPE, MESSAGE, SEVERITY, SOURCE_SHAPE]
"""
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas

from ..export.nquads_utils import CIM_NS, RDF_TYPE

logger = logging.getLogger(__name__)

VIOLATION_COLUMNS = ["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"]

_UUID_PREFIX = "urn:uuid:"
_SH = "http://www.w3.org/ns/shacl#"
_PROV = "http://www.w3.org/ns/prov#"
_DCTERMS = "http://purl.org/dc/terms/"
_XSD = "http://www.w3.org/2001/XMLSchema#"

# path suffix → rdflib serialize format (traversed in order for the reverse,
# so .xml — the suffix the docs push for RDF/XML — is the default before .rdf)
_EXT = {".ttl": "turtle", ".xml": "xml", ".rdf": "xml",
        ".nt": "nt", ".n3": "n3", ".jsonld": "json-ld", ".json-ld": "json-ld"}

# sh:sourceConstraintComponent URI suffix → short violation type
_COMPONENT_MAP = {
    "MinCountConstraintComponent": "sh:minCount",
    "MaxCountConstraintComponent": "sh:maxCount",
    "DatatypeConstraintComponent": "sh:datatype",
    "MinLengthConstraintComponent": "sh:minLength",
    "MaxLengthConstraintComponent": "sh:maxLength",
    "PatternConstraintComponent": "sh:pattern",
    "MinInclusiveConstraintComponent": "sh:minInclusive",
    "MaxInclusiveConstraintComponent": "sh:maxInclusive",
    "ClassConstraintComponent": "sh:class",
    "NodeConstraintComponent": "sh:node",
    "NodeKindConstraintComponent": "sh:nodeKind",
    "InConstraintComponent": "sh:in",
    "HasValueConstraintComponent": "sh:hasValue",
    "EqualsConstraintComponent": "sh:equals",
    "DisjointConstraintComponent": "sh:disjoint",
    "LessThanConstraintComponent": "sh:lessThan",
    "ClosedConstraintComponent": "sh:closed",
    "OrConstraintComponent": "sh:or",
    "AndConstraintComponent": "sh:and",
    "NotConstraintComponent": "sh:not",
    "SPARQLConstraintComponent": "sh:sparql",
}


def report_to_violations(report_graph):
    """ValidationReport rdflib graph → violations DataFrame (single columnar pass)."""
    import rdflib

    sh = rdflib.Namespace(_SH)
    # collect into per-column lists, build the DataFrame once (no per-row concat)
    columns = {name: [] for name in VIOLATION_COLUMNS}

    for result in report_graph.subjects(rdflib.RDF.type, sh.ValidationResult):
        path = report_graph.value(result, sh.resultPath)
        value = report_graph.value(result, sh.value)
        component = report_graph.value(result, sh.sourceConstraintComponent)
        severity = report_graph.value(result, sh.resultSeverity)
        shape = report_graph.value(result, sh.sourceShape)
        message = report_graph.value(result, sh.resultMessage)

        columns["ID"].append(_strip_uuid(report_graph.value(result, sh.focusNode)))
        columns["KEY"].append(_shorten(path))
        columns["VALUE"].append(_term_value(value))
        columns["VIOLATION_TYPE"].append(_component(component))
        columns["MESSAGE"].append(str(message) if message is not None else None)
        columns["SEVERITY"].append(_local_name(severity) if severity is not None else "Violation")
        columns["SOURCE_SHAPE"].append(str(shape) if shape is not None else None)

    return pandas.DataFrame(columns, columns=VIOLATION_COLUMNS)


def _strip_uuid(term):
    if term is None:
        return None
    value = str(term)
    return value[len(_UUID_PREFIX):] if value.startswith(_UUID_PREFIX) else value


def _shorten(term):
    """Predicate/path IRI → short KEY (CIM local name, rdf:type → 'Type')."""
    if term is None:
        return None
    value = str(term)
    if value == RDF_TYPE:
        return "Type"
    if value.startswith(CIM_NS):
        return value[len(CIM_NS):]
    return value


def _term_value(term):
    if term is None:
        return None
    if type(term).__name__ == "Literal":
        return str(term)
    return _strip_uuid(term)


def _component(term):
    if term is None:
        return "sh:unknown"
    value = str(term)
    suffix = value.split("#")[-1]
    if value.startswith(_TRIPLETS_NS):
        return f"triplets:{suffix}"
    return _COMPONENT_MAP.get(suffix, f"sh:{suffix}")


def _local_name(term):
    return str(term).split("#")[-1]


# short violation type → sh:sourceConstraintComponent URI (inverse of _COMPONENT_MAP)
_COMPONENT_URI = {short: f"{_SH}{suffix}" for suffix, short in _COMPONENT_MAP.items()}

_TRIPLETS_NS = "http://triplets#"


def violations_to_report_graph(violations, report_source=None, report_references=None):
    """Violations DataFrame → sh:ValidationReport rdflib graph (inverse of
    report_to_violations; KEYs expand to the CIM namespace unless already URIs).

    A result carries several plain-text ``sh:resultMessage``s when the frame
    has the context/location columns: the engine message, the shape and
    schema descriptions (context.enrich) and the source position
    (locations.locate_violations) — SHACL has no location vocabulary, so a
    message is the interoperable carrier.

    Report-level metadata (always): ``prov:generatedAtTime``,
    ``dcterms:creator`` (tool + version). Optional: ``dcterms:source`` /
    ``dcterms:references`` from ``report_source`` / ``report_references``
    (str or sequence — validated file name(s) / shape file name(s)).

    Defaults come from ``violations.attrs["validation"]`` — the metadata
    ``validate()`` stamps on the frame (timestamp of the validation run, tool
    version, data/shape file names). Explicit arguments override it.
    """
    import rdflib
    import triplets

    meta = violations.attrs.get("validation", {})
    if report_source is None:
        report_source = meta.get("source")
    if report_references is None:
        report_references = meta.get("references")
    generated_at = meta.get("generated_at") or datetime.now(timezone.utc).isoformat()
    creator = meta.get("creator") or f"triplets {triplets.__version__}"

    sh = rdflib.Namespace(_SH)
    prov = rdflib.Namespace(_PROV)
    dcterms = rdflib.Namespace(_DCTERMS)
    graph = rdflib.Graph()
    graph.bind("sh", sh)
    graph.bind("prov", prov)
    graph.bind("dcterms", dcterms)

    report = rdflib.BNode()
    graph.add((report, rdflib.RDF.type, sh.ValidationReport))
    graph.add((report, sh.conforms, rdflib.Literal(violations.empty)))
    graph.add((report, prov.generatedAtTime,
               rdflib.Literal(generated_at, datatype=rdflib.URIRef(f"{_XSD}dateTime"))))
    graph.add((report, dcterms.creator, rdflib.Literal(creator)))
    for value in _as_list(report_source):
        graph.add((report, dcterms.source, rdflib.Literal(value)))
    for value in _as_list(report_references):
        graph.add((report, dcterms.references, rdflib.Literal(value)))

    for row in violations.itertuples(index=False):
        result = rdflib.BNode()
        graph.add((report, sh.result, result))
        graph.add((result, rdflib.RDF.type, sh.ValidationResult))
        graph.add((result, sh.resultSeverity, sh[row.SEVERITY if pandas.notna(row.SEVERITY) else "Violation"]))
        if pandas.notna(row.ID):
            graph.add((result, sh.focusNode, rdflib.URIRef(f"{_UUID_PREFIX}{row.ID}")))
        if pandas.notna(row.KEY):
            graph.add((result, sh.resultPath, rdflib.URIRef(_expand(row.KEY))))
        if pandas.notna(row.VALUE):
            graph.add((result, sh.value, rdflib.Literal(row.VALUE)))
        if pandas.notna(row.VIOLATION_TYPE):
            graph.add((result, sh.sourceConstraintComponent, rdflib.URIRef(_expand(row.VIOLATION_TYPE))))
        for message in _messages(row):
            graph.add((result, sh.resultMessage, rdflib.Literal(message)))
        if pandas.notna(row.SOURCE_SHAPE):
            shape = str(row.SOURCE_SHAPE)   # anonymous property shapes stay blank nodes
            graph.add((result, sh.sourceShape,
                       rdflib.URIRef(shape) if "://" in shape or shape.startswith("urn:") else rdflib.BNode(shape)))

    return graph


def _as_list(value):
    if value is None:
        return ()
    if isinstance(value, (str, Path, bytes)):
        value = (value,)
    return tuple(v.decode() if isinstance(v, bytes) else os.fspath(v) if isinstance(v, Path) else v
                 for v in value)


def _messages(row):
    """The result's message set — engine message + optional context/location."""
    def cell(name):
        value = getattr(row, name, None)
        return None if value is None or pandas.isna(value) else value

    messages = []
    if cell("MESSAGE") is not None:
        messages.append(str(row.MESSAGE))
    if cell("SHAPE_DESCRIPTION") is not None:
        messages.append(f"Description: {row.SHAPE_DESCRIPTION}")
    if cell("SCHEMA_DESCRIPTION") is not None:
        multiplicity = f" [{row.SCHEMA_MULTIPLICITY}]" if cell("SCHEMA_MULTIPLICITY") is not None else ""
        messages.append(f"Schema: {row.SCHEMA_DESCRIPTION}{multiplicity}")
    if cell("SOURCE_URI") is not None:
        column = f" column {int(row.SOURCE_COLUMN)}" if cell("SOURCE_COLUMN") is not None else ""
        messages.append(f"Source: {row.SOURCE_URI} line {int(row.SOURCE_LINE)}{column}")
    return messages


def _expand(value):
    """Short KEY / violation type / shape name → URI (inverse of _shorten/_component)."""
    value = str(value)
    if value == "Type":
        return RDF_TYPE
    if "://" in value or value.startswith("urn:"):
        return value
    if value in _COMPONENT_URI:
        return _COMPONENT_URI[value]
    if value.startswith("sh:"):
        return f"{_SH}{value[3:]}"
    if value.startswith("triplets:"):
        return f"{_TRIPLETS_NS}{value[len('triplets:'):]}"
    return f"{CIM_NS}{value}"


def _resolve_format(path, format):
    """Explicit rdflib format wins; else path suffix via ``_EXT``; else turtle."""
    if format is not None:
        return format
    if path is not None:
        return _EXT.get(Path(path).suffix.lower(), "turtle")
    return "turtle"


def _default_path(fmt):
    """First suffix in ``_EXT`` that maps to ``fmt``, else ``.ttl``."""
    for suffix, name in _EXT.items():
        if name == fmt:
            return f"report{suffix}"
    return "report.ttl"


def export_to_shacl_report(violations, sources=None, path=None, export_to_memory=False,
                           format=None, report_source=None, report_references=None):
    """Violations frame → standard sh:ValidationReport (any rdflib format).

    Parameters
    ----------
    violations : DataFrame in VIOLATION_COLUMNS (any engine's output).
        Enrichment/location columns, when present, become additional
        ``sh:resultMessage``s (Description/Schema/Source).
    sources : optional
        The original CIM/XML files — runs the locate_violations pass so each
        result carries a "Source: file line N column M" message (a frame
        already carrying LOCATION_COLUMNS is used as-is).
    path : str or Path, optional
        Output file. Default ``report.<ext>`` from the resolved format.
        Suffix selects format when ``format`` is None (``.xml``/``.rdf`` →
        RDF/XML, ``.ttl`` → turtle, …).
    export_to_memory : bool, default False
        Return a BytesIO (with .name) instead of writing to disk.
    format : str or None, default None
        rdflib serialize format. When None, derived from ``path`` suffix
        (unknown/missing → turtle). Explicit value always wins.
    report_source : str or sequence, optional
        ``dcterms:source`` on the ValidationReport (validated file name(s)).
        Metadata only — ``sources`` is the one that runs the locate pass.
        Default: the data file names ``validate()`` stamped in
        ``violations.attrs["validation"]``.
    report_references : str or sequence, optional
        ``dcterms:references`` on the ValidationReport (shape file name(s)).
        Plain labels — not the shapes object ``to_sarif(shapes=)`` takes.
        Default: the shape file names from ``violations.attrs["validation"]``.
    """
    if sources is not None:
        from .locations import LOCATION_COLUMNS, locate_violations
        if not set(LOCATION_COLUMNS) <= set(violations.columns):
            violations = locate_violations(violations, sources)
    fmt = _resolve_format(path, format)
    if path is None:
        path = _default_path(fmt)
    else:
        path = os.fspath(path)
    payload = (violations_to_report_graph(violations, report_source=report_source,
                                          report_references=report_references)
               .serialize(format=fmt).encode("utf-8"))
    if export_to_memory:
        buffer = io.BytesIO(payload)
        buffer.name = os.path.basename(path)
        return buffer

    with open(path, "wb") as file:
        file.write(payload)
    logger.info("Saved %s", path)
    return path



# ── tabular exports (csv / excel) — same metadata as the RDF/SARIF reports ───

def _meta_rows(meta):
    """The attrs["validation"] dict as (KEY, VALUE) rows — lists fan out."""
    return [(key, item) for key, value in meta.items()
            for item in (value if isinstance(value, (list, tuple)) else (value,))]


def violations_to_csv(violations, path="violations.csv"):
    """Violations frame → CSV, plus a ``<name>_meta.<ext>`` sidecar carrying
    the validation metadata (``violations.attrs["validation"]``) as KEY,VALUE
    rows. No sidecar when the frame carries no metadata."""
    path = Path(path)
    violations.to_csv(path, index=False)
    logger.info("Saved %s", path)
    meta = violations.attrs.get("validation")
    if meta:
        meta_path = path.with_name(f"{path.stem}_meta{path.suffix}")
        pandas.DataFrame(_meta_rows(meta), columns=["KEY", "VALUE"]).to_csv(meta_path, index=False)
        logger.info("Saved %s", meta_path)
    return str(path)


def violations_to_excel(violations, path="violations.xlsx"):
    """Violations frame → Excel; the validation metadata
    (``violations.attrs["validation"]``) goes to a second "metadata" sheet."""
    path = Path(path)
    meta = violations.attrs.get("validation")
    with pandas.ExcelWriter(path, engine="openpyxl") as writer:
        violations.to_excel(writer, sheet_name="violations", index=False)
        if meta:
            pandas.DataFrame(_meta_rows(meta), columns=["KEY", "VALUE"]).to_excel(
                writer, sheet_name="metadata", index=False)
    logger.info("Saved %s", path)
    return str(path)
