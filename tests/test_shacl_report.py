"""Tests for the sh:ValidationReport export (validation/shacl_report.py)."""
from pathlib import Path

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


def test_format_from_path_suffix(tmp_path):
    """path suffix selects the rdflib format when format is None."""
    import rdflib

    xml_path = export_to_shacl_report(VIOLATIONS, path=tmp_path / "report.xml")
    assert rdflib.Graph().parse(xml_path, format="xml")

    ttl_path = export_to_shacl_report(VIOLATIONS, path=tmp_path / "report.ttl")
    assert rdflib.Graph().parse(ttl_path, format="turtle")


def test_explicit_format_wins_over_suffix(tmp_path):
    """format= always overrides path-derived format."""
    import rdflib

    path = export_to_shacl_report(VIOLATIONS, path=tmp_path / "report.ttl", format="pretty-xml")
    text = Path(path).read_text()
    assert "<rdf:RDF" in text or "rdf:RDF" in text
    assert rdflib.Graph().parse(path, format="xml")


def test_export_to_memory_default_turtle():
    buffer = export_to_shacl_report(VIOLATIONS, export_to_memory=True)
    assert buffer.name.endswith(".ttl")
    import rdflib
    assert rdflib.Graph().parse(data=buffer.getvalue(), format="turtle")


def test_default_path_prefers_xml_suffix():
    buffer = export_to_shacl_report(VIOLATIONS, export_to_memory=True, format="xml")
    assert buffer.name == "report.xml"


def test_report_metadata():
    import rdflib
    from triplets.validation.shacl_report import violations_to_report_graph

    prov = rdflib.Namespace("http://www.w3.org/ns/prov#")
    dcterms = rdflib.Namespace("http://purl.org/dc/terms/")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")

    graph = violations_to_report_graph(
        VIOLATIONS, report_source="file.xml", report_references=[Path("a.ttl"), "b.ttl"])
    report = next(graph.subjects(rdflib.RDF.type, sh.ValidationReport))

    assert graph.value(report, prov.generatedAtTime) is not None
    assert "triplets" in str(graph.value(report, dcterms.creator))
    assert {str(v) for v in graph.objects(report, dcterms.source)} == {"file.xml"}
    assert {str(v) for v in graph.objects(report, dcterms.references)} == {"a.ttl", "b.ttl"}


def test_multi_messages_from_context_and_location_columns():
    """Enrichment and location columns become additional plain-text
    sh:resultMessage entries (SHACL has no location vocabulary)."""
    import rdflib
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")

    enriched = VIOLATIONS.head(1).copy()
    enriched["SHAPE_DESCRIPTION"] = "Line length plausibility."
    enriched["SCHEMA_DESCRIPTION"] = "Total length of the line."
    enriched["SCHEMA_MULTIPLICITY"] = "0..1"
    enriched["SOURCE_URI"] = "grid.xml"
    enriched["SOURCE_LINE"] = 5
    enriched["SOURCE_COLUMN"] = 3

    graph = violations_to_report_graph(enriched)
    assert {str(message) for message in graph.objects(None, sh.resultMessage)} == {
        "too long",
        "Description: Line length plausibility.",
        "Schema: Total length of the line. [0..1]",
        "Source: grid.xml line 5 column 3",
    }


def test_sources_add_location_message(tmp_path):
    """export_to_shacl_report(sources=...) runs the locate pass itself."""
    import rdflib
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    xml = tmp_path / "grid.xml"
    xml.write_text('<?xml version="1.0"?>\n'
                   '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                   'xmlns:cim="http://iec.ch/TC57/CIM100#">\n'
                   '  <cim:ACLineSegment rdf:ID="_11111111-2222-3333-4444-555555555555">\n'
                   '    <cim:Conductor.length>100</cim:Conductor.length>\n'
                   '  </cim:ACLineSegment>\n'
                   '</rdf:RDF>\n')

    buffer = VIOLATIONS.head(1).shacl.to_shacl_report(sources=[str(xml)], export_to_memory=True)
    graph = rdflib.Graph().parse(data=buffer.getvalue(), format="turtle")
    messages = {str(message) for message in graph.objects(None, sh.resultMessage)}
    assert any(message.startswith("Source: ") and "line 4" in message for message in messages)
