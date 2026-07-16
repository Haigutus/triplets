"""Map a pyshacl/SHACL ValidationReport graph to the canonical violations DataFrame,
and the inverse: export a violations DataFrame as a standard sh:ValidationReport.

Canonical violations schema (identical across all current and future SHACL
engines, so the later vectorized engines can produce it natively):
    [ID, KEY, VALUE, VIOLATION_TYPE, MESSAGE, SEVERITY, SOURCE_SHAPE]
"""
import io
import logging
import os

import pandas

from ..export.nquads_utils import CIM_NS, RDF_TYPE

logger = logging.getLogger(__name__)

VIOLATION_COLUMNS = ["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"]

_UUID_PREFIX = "urn:uuid:"
_SH = "http://www.w3.org/ns/shacl#"

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


def violations_to_report_graph(violations):
    """Violations DataFrame → sh:ValidationReport rdflib graph (inverse of
    report_to_violations; KEYs expand to the CIM namespace unless already URIs)."""
    import rdflib

    sh = rdflib.Namespace(_SH)
    graph = rdflib.Graph()
    graph.bind("sh", sh)

    report = rdflib.BNode()
    graph.add((report, rdflib.RDF.type, sh.ValidationReport))
    graph.add((report, sh.conforms, rdflib.Literal(violations.empty)))

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
        if pandas.notna(row.MESSAGE):
            graph.add((result, sh.resultMessage, rdflib.Literal(row.MESSAGE)))
        if pandas.notna(row.SOURCE_SHAPE):
            shape = str(row.SOURCE_SHAPE)   # anonymous property shapes stay blank nodes
            graph.add((result, sh.sourceShape,
                       rdflib.URIRef(shape) if "://" in shape or shape.startswith("urn:") else rdflib.BNode(shape)))

    return graph


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


def export_to_shacl_report(violations, path=None, export_to_memory=False):
    """Violations frame → standard sh:ValidationReport, serialized as turtle.

    Parameters
    ----------
    violations : DataFrame in VIOLATION_COLUMNS (any engine's output).
    path : str or Path, optional
        Output file (default "report.ttl"). Ignored with export_to_memory.
    export_to_memory : bool, default False
        Return a BytesIO (with .name) instead of writing to disk.
    """
    payload = violations_to_report_graph(violations).serialize(format="turtle").encode("utf-8")
    if export_to_memory:
        buffer = io.BytesIO(payload)
        buffer.name = "report.ttl"
        return buffer

    path = "report.ttl" if path is None else os.fspath(path)
    with open(path, "wb") as file:
        file.write(payload)
    logger.info("Saved %s", path)
    return path
