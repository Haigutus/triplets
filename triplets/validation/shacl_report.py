"""Map a pyshacl/SHACL ValidationReport graph to the canonical violations DataFrame.

Canonical violations schema (identical across all current and future SHACL
engines, so the later vectorized engines can produce it natively):
    [ID, KEY, VALUE, VIOLATION_TYPE, MESSAGE, SEVERITY, SOURCE_SHAPE]
"""
import logging

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
    suffix = str(term).split("#")[-1]
    return _COMPONENT_MAP.get(suffix, f"sh:{suffix}")


def _local_name(term):
    return str(term).split("#")[-1]
