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

    graph = violations_to_report_graph(enriched)
    assert {str(message) for message in graph.objects(None, sh.resultMessage)} == {
        "[message] too long",
        "[description] Line length plausibility.",
        "[schema] Total length of the line. [0..1]",
        "[instance] grid.xml line 5",
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
    assert any(message.startswith("[instance] ") and "line 4" in message for message in messages)


# ── validation-run metadata (violations.attrs["validation"]) ─────────────────

SHAPE_TTL = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
"""

DATA = pandas.DataFrame([
    ("d1", "Type", "Distribution", "i1"),
    ("d1", "label", "grid.xml", "i1"),
    ("b1", "Type", "Breaker", "i1"),
], columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])

META = {"generated_at": "2026-08-07T10:00:00+00:00", "creator": "triplets test",
        "source": ["grid.xml"], "references": ["shapes.ttl"]}


def _with_meta(frame):
    frame = frame.copy()
    frame.attrs["validation"] = dict(META)
    return frame


def test_validate_stamps_metadata(tmp_path):
    shapes = tmp_path / "breaker_shapes.ttl"
    shapes.write_text(SHAPE_TTL)
    violations = triplets.validation.validate(DATA, shapes, engine="pandas")
    meta = violations.attrs["validation"]
    assert meta["source"] == ["grid.xml"]
    assert meta["references"] == ["breaker_shapes.ttl"]
    assert meta["creator"].startswith("triplets ")
    assert meta["engine"] == "pandas"
    assert meta["started_at"] <= meta["generated_at"]
    assert meta["duration_seconds"] >= 0
    assert meta["node_shapes"] == 1 and meta["constraints"] == 1
    assert meta["skipped_shapes"] == [] and meta["skipped_components"] == []
    assert len(violations) == 1  # b1 has no name


SKIPPED_TTL = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix ex:  <http://example.org/#> .
ex:NodeTargeted a sh:NodeShape ; sh:targetNode ex:n1 ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
ex:DeepPath a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path ( cim:a cim:b cim:c ) ; sh:minCount 1 ] .
"""


def test_message_source_distinguishes_authored_from_engine(tmp_path):
    """[message] = the shape's own sh:message verbatim, [engine] =
    engine-worded text — stamped by validate() (MESSAGE_SOURCE)."""
    import rdflib
    shapes = tmp_path / "authored_shapes.ttl"
    shapes.write_text("""
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                  sh:message "every breaker needs a name" ] ;
    sh:property [ sh:path cim:Breaker.inTransit ; sh:minCount 1 ] .
""")
    violations = triplets.validation.validate(DATA, shapes, engine="pandas")
    sources = dict(zip(violations["KEY"], violations["MESSAGE_SOURCE"]))
    assert sources["IdentifiedObject.name"] == "shacl"    # authored sh:message
    assert sources["Breaker.inTransit"] == "engine"       # engine default text

    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    dcterms = rdflib.Namespace("http://purl.org/dc/terms/")
    graph = violations_to_report_graph(violations)
    report = next(graph.subjects(rdflib.RDF.type, sh.ValidationReport))
    assert str(graph.value(report, dcterms.creator)).endswith("(engine: pandas)")
    messages = {str(m) for m in graph.objects(None, sh.resultMessage)}
    assert "[message] every breaker needs a name" in messages
    assert any(m.startswith("[engine] Breaker.inTransit") for m in messages)


def test_metadata_reports_skipped_coverage(tmp_path):
    """Shapes a vectorized run cannot evaluate land in the metadata — the
    report says what was NOT validated, not just what failed."""
    shapes = tmp_path / "partial_shapes.ttl"
    shapes.write_text(SKIPPED_TTL)
    violations = triplets.validation.validate(DATA, shapes, engine="pandas")
    meta = violations.attrs["validation"]
    assert meta["node_shapes"] == 2
    assert any("sh:targetNode" in entry for entry in meta["skipped_shapes"])
    assert any("unsupported sh:path" in entry for entry in meta["skipped_shapes"])


def test_run_stats_reach_sarif_and_csv(tmp_path):
    """The full validate() stamp — engine, duration, counts, coverage —
    round-trips into the SARIF run properties and the CSV sidecar; empty
    coverage is stated explicitly, not implied by absence."""
    from triplets.validation import violations_to_csv
    from triplets.validation.sarif import build_sarif

    shapes = tmp_path / "coverage_shapes.ttl"
    shapes.write_text(SKIPPED_TTL)
    violations = triplets.validation.validate(DATA, shapes, engine="pandas")

    properties = build_sarif(violations)["runs"][0]["properties"]
    assert properties["engine"] == "pandas"
    assert properties["node_shapes"] == 2 and properties["duration_seconds"] >= 0
    assert any("sh:targetNode" in entry for entry in properties["skipped_shapes"])
    assert properties["skipped_components"] == []       # empty list survives

    violations_to_csv(violations, tmp_path / "report.csv")
    meta = pandas.read_csv(tmp_path / "report_meta.csv", keep_default_na=False)
    keys = set(meta["KEY"])
    assert {"engine", "duration_seconds", "node_shapes", "constraints",
            "skipped_shapes", "skipped_components"} <= keys
    assert (meta.loc[meta["KEY"] == "skipped_components", "VALUE"] == "").all()


def test_enrich_and_locate_preserve_metadata(tmp_path):
    violations = _with_meta(VIOLATIONS)
    enriched = triplets.validation.enrich(violations, data=DATA)
    assert enriched.attrs["validation"] == META
    xml = tmp_path / "grid.xml"
    xml.write_text("<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'/>")
    located = triplets.validation.locate_violations(violations, [str(xml)])
    assert located.attrs["validation"] == META


def test_report_defaults_from_metadata_and_override():
    import rdflib
    prov = rdflib.Namespace("http://www.w3.org/ns/prov#")
    dcterms = rdflib.Namespace("http://purl.org/dc/terms/")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")

    graph = violations_to_report_graph(_with_meta(VIOLATIONS))
    report = next(graph.subjects(rdflib.RDF.type, sh.ValidationReport))
    assert str(graph.value(report, prov.generatedAtTime)) == META["generated_at"]
    assert str(graph.value(report, dcterms.creator)) == "triplets test"
    assert {str(v) for v in graph.objects(report, dcterms.source)} == {"grid.xml"}
    assert {str(v) for v in graph.objects(report, dcterms.references)} == {"shapes.ttl"}

    override = violations_to_report_graph(_with_meta(VIOLATIONS), report_source="other.zip")
    report = next(override.subjects(rdflib.RDF.type, sh.ValidationReport))
    assert {str(v) for v in override.objects(report, dcterms.source)} == {"other.zip"}


def test_sarif_carries_metadata():
    from triplets.validation.sarif import build_sarif
    run = build_sarif(_with_meta(VIOLATIONS))["runs"][0]
    assert run["invocations"] == [{"executionSuccessful": True,
                                   "endTimeUtc": "2026-08-07T10:00:00Z"}]
    assert run["properties"] == {"source": ["grid.xml"], "references": ["shapes.ttl"]}
    assert "invocations" not in build_sarif(VIOLATIONS)["runs"][0]


def test_csv_export_writes_meta_sidecar(tmp_path):
    from triplets.validation import violations_to_csv
    path = violations_to_csv(_with_meta(VIOLATIONS), tmp_path / "report.csv")
    assert Path(path).exists()
    sidecar = tmp_path / "report_meta.csv"
    meta = pandas.read_csv(sidecar)
    assert list(meta.columns) == ["KEY", "VALUE"]
    rows = set(zip(meta["KEY"], meta["VALUE"]))
    assert ("source", "grid.xml") in rows and ("references", "shapes.ttl") in rows
    assert ("creator", "triplets test") in rows

    violations_to_csv(VIOLATIONS, tmp_path / "bare.csv")   # no metadata → no sidecar
    assert not (tmp_path / "bare_meta.csv").exists()


def test_tabular_exports_to_memory():
    """csv/excel exports return BytesIO objects with .name — no filesystem
    required, same convention as the cimxml/csv exports."""
    from triplets.validation import violations_to_csv, violations_to_excel

    files = violations_to_csv(_with_meta(VIOLATIONS), export_to_memory=True)
    assert [f.name for f in files] == ["violations.csv", "violations_meta.csv"]
    meta = pandas.read_csv(files[1])
    assert ("creator", "triplets test") in set(zip(meta["KEY"], meta["VALUE"]))
    assert [f.name for f in violations_to_csv(VIOLATIONS, export_to_memory=True)] \
        == ["violations.csv"]                      # bare frame → no sidecar

    pytest.importorskip("openpyxl")
    buffer = violations_to_excel(_with_meta(VIOLATIONS), export_to_memory=True)
    assert buffer.name == "violations.xlsx"
    assert set(pandas.read_excel(buffer, sheet_name=None)) == {"violations", "metadata"}


def test_excel_export_writes_metadata_sheet(tmp_path):
    pytest.importorskip("openpyxl")
    from triplets.validation import violations_to_excel
    path = violations_to_excel(_with_meta(VIOLATIONS), tmp_path / "report.xlsx")
    sheets = pandas.read_excel(path, sheet_name=None)
    assert set(sheets) == {"violations", "metadata"}
    meta = sheets["metadata"]
    assert ("references", "shapes.ttl") in set(zip(meta["KEY"], meta["VALUE"]))
