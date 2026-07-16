"""Tests for the sh:ValidationReport export (validation/shacl_report.py)."""
import pandas
import pytest

pytest.importorskip("rdflib")

import triplets  # noqa: F401 — registers the shacl accessor namespace
from triplets.validation import VIOLATION_COLUMNS, export_to_shacl_report
from triplets.validation.shacl_report import report_to_violations, violations_to_report_graph

VIOLATIONS = pandas.DataFrame([
    ("11111111-2222-3333-4444-555555555555", "Conductor.length", "100",
     "sh:maxInclusive", "too long", "Warning", "n0f320cdb3"),
    ("22222222-2222-3333-4444-555555555555", "IdentifiedObject.name", None,
     "sh:minCount", "needs a name", "Violation", "http://example.org/shapes#NameShape"),
    ("33333333-2222-3333-4444-555555555555", "ACLineSegment.r", "0.66",
     "sh:sparql", "R/X ratio high", "Violation", "n0f320cdb3"),
    ("44444444-2222-3333-4444-555555555555", "Conductor.length", "1",
     "triplets:lexicalForm", "integer form for a float", "Warning", None),
], columns=VIOLATION_COLUMNS)


def _canon(frame):
    return (frame[VIOLATION_COLUMNS].fillna("")
            .sort_values(VIOLATION_COLUMNS).reset_index(drop=True))


def test_report_graph_roundtrip():
    """violations -> ValidationReport graph -> violations is exact
    (report_to_violations is the existing inverse mapping)."""
    back = report_to_violations(violations_to_report_graph(VIOLATIONS))
    pandas.testing.assert_frame_equal(_canon(back), _canon(VIOLATIONS))


def test_report_graph_shape():
    import rdflib
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    graph = violations_to_report_graph(VIOLATIONS)

    report = next(graph.subjects(rdflib.RDF.type, sh.ValidationReport))
    assert graph.value(report, sh.conforms).toPython() is False
    assert len(list(graph.objects(report, sh.result))) == len(VIOLATIONS)
    # anonymous property shapes stay blank nodes, named shapes stay URIs
    shapes = set(graph.objects(None, sh.sourceShape))
    assert rdflib.URIRef("http://example.org/shapes#NameShape") in shapes
    assert any(isinstance(shape, rdflib.BNode) for shape in shapes)


def test_empty_frame_conforms():
    import rdflib
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    graph = violations_to_report_graph(VIOLATIONS.iloc[0:0])
    report = next(graph.subjects(rdflib.RDF.type, sh.ValidationReport))
    assert graph.value(report, sh.conforms).toPython() is True


def test_export_paths_memory_and_accessor(tmp_path):
    import rdflib

    path = export_to_shacl_report(VIOLATIONS, path=tmp_path / "report.ttl")
    assert rdflib.Graph().parse(path, format="turtle")

    buffer = VIOLATIONS.shacl.to_shacl_report(export_to_memory=True)
    assert buffer.name == "report.ttl"
    assert rdflib.Graph().parse(data=buffer.getvalue(), format="turtle")
