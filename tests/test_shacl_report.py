"""Tests for the sh:ValidationReport export (validation/shacl_report.py)."""
from pathlib import Path

import pandas
import pytest

pytest.importorskip("rdflib")

import triplets  # noqa: F401 — registers the shacl accessor namespace
from triplets.export import export_to_nquads
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


def test_path_less_result_echoing_the_focus_has_no_value():
    """A result without sh:resultPath whose sh:value is the focus node (pyshacl's
    node-level sh:or / sh:not / sh:xone) gives VALUE None; another value is kept."""
    import rdflib
    graph = rdflib.Graph().parse(format="turtle", data="""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        [] a sh:ValidationResult ; sh:focusNode <urn:uuid:b1> ; sh:value <urn:uuid:b1> ;
            sh:sourceConstraintComponent sh:OrConstraintComponent .
        [] a sh:ValidationResult ; sh:focusNode <urn:uuid:b2> ; sh:value <urn:uuid:other> ;
            sh:sourceConstraintComponent sh:SPARQLConstraintComponent .""")
    value = report_to_violations(graph).set_index("ID")["VALUE"]
    assert pandas.isna(value["b1"]) and value["b2"] == "other"


def test_detail_results_are_not_listed():
    """A sh:detail result (the cause of a sh:node result) is not a finding of its own:
    one row, the parent's."""
    import rdflib
    graph = rdflib.Graph().parse(format="turtle", data="""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix cim: <http://iec.ch/TC57/CIM100#> .
        [] a sh:ValidationResult ; sh:focusNode <urn:uuid:b1> ; sh:value <urn:uuid:bay1> ;
            sh:resultPath cim:Equipment.EquipmentContainer ;
            sh:sourceConstraintComponent sh:NodeConstraintComponent ;
            sh:detail [ a sh:ValidationResult ; sh:focusNode <urn:uuid:bay1> ;
                        sh:resultPath cim:IdentifiedObject.name ;
                        sh:sourceConstraintComponent sh:MinCountConstraintComponent ] .""")
    violations = report_to_violations(graph)
    assert list(zip(violations["ID"], violations["KEY"], violations["VIOLATION_TYPE"])) == [
        ("b1", "Equipment.EquipmentContainer", "sh:node")]


def test_blank_node_result_path_resolves_through_the_shapes_graph():
    """An inverse-path result reads the KEY the path names in the shapes graph;
    without the shapes graph the blank-node label stays."""
    import rdflib
    shapes = rdflib.Graph().parse(format="turtle", data="""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix cim: <http://iec.ch/TC57/CIM100#> .
        cim:BayShape a sh:NodeShape ; sh:targetClass cim:Bay ;
            sh:property [ sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; sh:minCount 1 ] .""")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    path = next(shapes.objects(None, sh.path))
    report = rdflib.Graph()
    result = rdflib.BNode()
    report.add((result, rdflib.RDF.type, sh.ValidationResult))
    report.add((result, sh.focusNode, rdflib.URIRef("urn:uuid:bay1")))
    report.add((result, sh.resultPath, path))
    report.add((result, sh.sourceConstraintComponent, sh.MinCountConstraintComponent))
    assert list(report_to_violations(report, shapes_graph=shapes)["KEY"]) == ["Equipment.EquipmentContainer"]
    assert list(report_to_violations(report)["KEY"]) == [str(path)]


def test_value_term_kind_follows_the_schema_entry_type():
    """sh:value is the node the data has: an IRI for a reference, an enumeration value and a
    class; a literal for an attribute (also one that reads like a UUID) and for an undefined KEY."""
    import rdflib
    cim = "http://iec.ch/TC57/CIM100#"
    uuid = "00000000-0000-4000-8000-000000000001"
    rdf_map = {"Bay": {"type": "Class", "namespace": cim},
               "Equipment.EquipmentContainer": {"type": "Association", "namespace": cim},
               "Breaker.kind": {"type": "Enumeration", "namespace": cim},
               "BreakerKind.air": {"type": "EnumerationValue", "namespace": cim},
               "IdentifiedObject.mRID": {"type": "Attribute", "namespace": cim, "xsd:type": "xsd:string"}}
    values = [("Equipment.EquipmentContainer", uuid), ("Equipment.EquipmentContainer", "http://example.org/bay"),
              ("Equipment.EquipmentContainer", "http://example.org/ns#bay1"),
              ("Equipment.EquipmentContainer", "Bay"),                    # an object ID that is also a class name
              ("Breaker.kind", "BreakerKind.air"), ("IdentifiedObject.mRID", uuid), ("Extension.ref", uuid),
              ("Type", "Bay"), ("Type", "http://example.org/other#Bay")]
    violations = pandas.DataFrame([("b1", key, value, "sh:class", "m", "Violation", None) for key, value in values],
                                  columns=VIOLATION_COLUMNS)
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")

    def terms(**kwargs):
        graph = violations_to_report_graph(violations, **kwargs)
        return {(str(graph.value(result, sh.resultPath)).removeprefix(cim), value)
                for result, value in graph.subject_objects(sh.value)}

    literals = {(key, rdflib.Literal(value)) for key, value in values}
    types = {(str(rdflib.RDF.type), rdflib.URIRef(cim + "Bay")),
             (str(rdflib.RDF.type), rdflib.URIRef("http://example.org/other#Bay"))}
    assert terms() == {(key, value) for key, value in literals if key != "Type"} | types
    assert terms(rdf_map=rdf_map) == {
        ("Equipment.EquipmentContainer", rdflib.URIRef(f"urn:uuid:{uuid}")),
        ("Equipment.EquipmentContainer", rdflib.URIRef("http://example.org/bay")),
        ("Equipment.EquipmentContainer", rdflib.URIRef("http://example.org/ns#bay1")),
        ("Equipment.EquipmentContainer", rdflib.URIRef("urn:uuid:Bay")),      # as export_to_nquads writes the row
        ("Breaker.kind", rdflib.URIRef(cim + "BreakerKind.air")),
        ("IdentifiedObject.mRID", rdflib.Literal(uuid)), ("Extension.ref", rdflib.Literal(uuid)), *types}
    back = report_to_violations(violations_to_report_graph(violations, rdf_map=rdf_map), rdf_map=rdf_map)
    pandas.testing.assert_frame_equal(_canon(back), _canon(violations))

    # a KEY given as its IRI has the entry of its name; a VALUE that is no text is its text
    other = pandas.DataFrame([("b1", cim + "Equipment.EquipmentContainer", uuid, "sh:class", "m", "Violation", None),
                              ("b2", "Equipment.EquipmentContainer", 3, "sh:class", "m", "Violation", None)],
                             columns=VIOLATION_COLUMNS)
    graph = violations_to_report_graph(other, rdf_map=rdf_map)
    assert set(graph.objects(None, sh.value)) == {rdflib.URIRef(f"urn:uuid:{uuid}"), rdflib.URIRef("urn:uuid:3")}
    assert set(graph.objects(None, sh.resultPath)) == {rdflib.URIRef(cim + "Equipment.EquipmentContainer")}


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
        "[shacl_message] too long",
        "[context_value] 100",
        "[shacl_description] Line length plausibility.",
        "[schema_property] Total length of the line. [0..1]",
        "[context_location] grid.xml line 5",
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
    assert any(message.startswith("[context_location] ") and "line 4" in message for message in messages)

    stamped = VIOLATIONS.head(1).assign(SOURCE_URI="custom.xml", SOURCE_LINE=99, SOURCE_SNIPPET="by hand")
    buffer = stamped.shacl.to_shacl_report(sources=[str(xml)], export_to_memory=True)
    graph = rdflib.Graph().parse(data=buffer.getvalue(), format="turtle")       # SOURCE_* on the frame are kept
    assert "[context_location] custom.xml line 99" in {str(message) for message
                                                       in graph.objects(None, sh.resultMessage)}


def test_located_frame_is_not_read_for_referenced_objects(tmp_path):
    """The SHACL report writes no referenced-object location: a frame located
    before enrich filled TARGET_ID (SOURCE_* on the frame, no located referenced
    object) is exported without reading the sources — the missing file is never
    opened — and the result messages are the ones without sources=."""
    xml = tmp_path / "grid.xml"
    xml.write_text('<?xml version="1.0"?>\n'
                   '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                   'xmlns:cim="http://iec.ch/TC57/CIM100#">\n'
                   '  <cim:Breaker rdf:ID="_b1">\n'
                   '    <cim:Equipment.EquipmentContainer rdf:resource="#_sub1"/>\n'
                   '  </cim:Breaker>\n'
                   '  <cim:Substation rdf:ID="_sub1"/>\n'
                   '</rdf:RDF>\n')
    data = pandas.read_RDF([str(xml)])
    violations = pandas.DataFrame(
        [["b1", "Equipment.EquipmentContainer", "sub1", "sh:class", "m", "Violation", "urn:shape"]],
        columns=VIOLATION_COLUMNS)
    located = triplets.validation.enrich(violations.shacl.locate(sources=[str(xml)]), data=data)
    assert located["TARGET_ID"].tolist() == ["sub1"] and located["TARGET_URI"].isna().all()
    assert located["SOURCE_LINE"].tolist() == [4]

    import rdflib
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    absent = [str(tmp_path / "absent.xml")]
    messages = [{str(message) for message
                 in rdflib.Graph().parse(data=buffer.getvalue(), format="turtle").objects(None, sh.resultMessage)}
                for buffer in (located.shacl.to_shacl_report(sources=absent, export_to_memory=True),
                               located.shacl.to_shacl_report(export_to_memory=True))]
    assert messages[0] == messages[1] and f"[context_location] {xml} line 4" in messages[0]
    with pytest.raises(FileNotFoundError):             # the SARIF export does locate them
        located.shacl.to_sarif(sources=absent, export_to_memory=True)

    # a frame not located: the pass looks for b1 only and stops after grid.xml — sub1,
    # in the data but not in that file, does not make it open the missing source
    other = tmp_path / "other.xml"
    other.write_text(xml.read_text().replace('  <cim:Substation rdf:ID="_sub1"/>\n', ""))
    enriched = triplets.validation.enrich(violations, data=data)
    report = enriched.shacl.to_shacl_report(sources=[str(other), *absent], export_to_memory=True)
    assert f"[context_location] {other} line 4" in report.getvalue().decode()
    with pytest.raises(FileNotFoundError):
        enriched.shacl.to_sarif(sources=[str(other), *absent], export_to_memory=True)


def report_names(buffer):
    """(data file names, dcterms:references) of a serialized report: the file of
    each [context_location] message and dcterms:source."""
    import rdflib
    graph = rdflib.Graph().parse(data=buffer.getvalue(), format="turtle")
    located = {str(message).removeprefix("[context_location] ").rsplit(" line ", 1)[0]
               for message in graph.objects(None, rdflib.URIRef("http://www.w3.org/ns/shacl#resultMessage"))
               if str(message).startswith("[context_location] ")}
    dcterms = rdflib.Namespace("http://purl.org/dc/terms/")
    return (located, {str(name) for name in graph.objects(None, dcterms.source)},
            {str(name) for name in graph.objects(None, dcterms.references)})


def parsed_run(root, parse_path):
    """repo layout under *root* (models/grid.xml, shapes/shapes.ttl) → the file
    parsed by *parse_path* and validated."""
    (root / "models").mkdir(parents=True)
    (root / "shapes").mkdir()
    (root / "models" / "grid.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#">\n'
        '  <cim:Breaker rdf:ID="_11111111-2222-3333-4444-555555555555"/>\n'
        '</rdf:RDF>\n')
    (root / "shapes" / "shapes.ttl").write_text(SHAPE_TTL)
    data = pandas.read_RDF([parse_path])
    return data.shacl.validate_shacl(str(root / "shapes" / "shapes.ttl"), engine="pandas")


def test_source_root_end_to_end(tmp_path):
    """A file parsed by its absolute path, validated, exported with source_root:
    the located file and dcterms:source are the same root-relative name —
    located by sources= or before the export; without sources= the report
    names the file in dcterms:source only. dcterms:references of a run is a
    file name without directories and stays one; an explicit absolute
    report_references= under the root is written relative."""
    root = tmp_path / "repo"
    model = str(root / "models" / "grid.xml")
    violations = parsed_run(root, model)
    assert len(violations) == 1 and violations.attrs["validation"]["source"] == [model]

    reports = {
        "sources": violations.shacl.to_shacl_report(sources=[model], source_root=root, export_to_memory=True),
        "located": violations.shacl.locate(sources=[model]).shacl.to_shacl_report(source_root=root,
                                                                                  export_to_memory=True),
        "label": violations.shacl.to_shacl_report(source_root=root, export_to_memory=True),
    }
    for flow, buffer in reports.items():
        assert str(root) not in buffer.getvalue().decode("utf-8"), flow
        assert report_names(buffer) == (set() if flow == "label" else {"models/grid.xml"},
                                        {"models/grid.xml"}, {"shapes.ttl"}), flow

    explicit = violations.shacl.to_shacl_report(sources=[model], source_root=root, export_to_memory=True,
                                                report_references=[str(root / "shapes" / "shapes.ttl"), "extra.ttl"])
    assert report_names(explicit)[2] == {"shapes/shapes.ttl", "extra.ttl"}
    assert report_names(violations.shacl.to_shacl_report(sources=[model], export_to_memory=True))[:2] \
        == ({model}, {model})                                           # default: the names as given


def test_source_root_relative_parse_path(tmp_path, monkeypatch):
    """A file parsed by a relative path keeps that name in every place, also
    with the working directory below the root."""
    root = tmp_path / "repo"
    (root / "work").mkdir(parents=True)
    monkeypatch.chdir(root / "work")
    violations = parsed_run(root, "../models/grid.xml")

    for source_root in (root, ".."):
        buffer = violations.shacl.to_shacl_report(sources=["../models/grid.xml"], source_root=source_root,
                                                  export_to_memory=True)
        assert report_names(buffer)[:2] == ({"../models/grid.xml"}, {"../models/grid.xml"})
    located = violations.shacl.locate(sources=["../models/grid.xml"])
    assert report_names(located.shacl.to_shacl_report(source_root=root, export_to_memory=True))[:2] \
        == ({"../models/grid.xml"}, {"../models/grid.xml"})


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
    violations = triplets.validation.validate_shacl(DATA, shapes, engine="pandas")
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
ex:CustomTarget a sh:NodeShape ; sh:target ex:NotSparql ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
ex:DeepPath a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path ( cim:a cim:b cim:c ) ; sh:minCount 1 ] .
"""


def test_minimal_run_exports_both_formats(tmp_path):
    """A bare validate_shacl() — no rdf_map, no context enrichment, no sources= —
    still exports both formats: the constraint message plus [shacl_expected]
    (known from the compiled IR, no extra input needed), never empty
    [schema_property]/[shacl_description]/[context_object]/[context_location]/[context_snippet] entries."""
    import rdflib
    from triplets.validation.sarif import build_sarif

    shapes = tmp_path / "minimal_shapes.ttl"
    shapes.write_text(SHAPE_TTL)
    violations = triplets.validation.validate_shacl(DATA, shapes, engine="pandas")
    assert len(violations) == 1

    buffer = violations.shacl.to_shacl_report(export_to_memory=True)
    graph = rdflib.Graph().parse(data=buffer.getvalue(), format="turtle")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    messages = sorted(str(m) for m in graph.objects(None, sh.resultMessage))
    assert messages[0].startswith("[engine_message] ")
    assert messages[1] == "[shacl_expected] at least 1 value(s)"
    assert len(messages) == 2

    text = build_sarif(violations)["runs"][0]["results"][0]["message"]["text"]
    assert text.split("\n")[0].startswith("[engine_message] ")
    assert "[shacl_expected] at least 1 value(s)" in text
    for absent in ("[schema_property]", "[shacl_description]", "[context_object]", "[context_location]", "[context_snippet]", "[context_message]"):
        assert absent not in text


def test_report_embeds_source_shape_definitions(tmp_path):
    """sh:sourceShape is never an empty node: the violated shapes' defining
    triples (incl. the sh:in list) are embedded in the report, so the
    expected values are machine-recoverable from the report alone."""
    import rdflib
    from rdflib.collection import Collection

    shapes = tmp_path / "container_shapes.ttl"
    shapes.write_text("""
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path ( cim:Equipment.EquipmentContainer rdf:type ) ;
                  sh:in ( cim:Bay cim:VoltageLevel ) ] .
""")
    data = DATA.copy()
    data.loc[len(data)] = ("b1", "Equipment.EquipmentContainer", "s1", "i1")
    data.loc[len(data)] = ("s1", "Type", "Substation", "i1")
    violations = triplets.validation.validate_shacl(data, shapes, engine="pandas")
    assert len(violations) == 1

    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    graph = violations_to_report_graph(violations)
    result = next(graph.subjects(rdflib.RDF.type, sh.ValidationResult))
    shape_node = graph.value(result, sh.sourceShape)
    assert graph.value(shape_node, sh.path) is not None            # not an empty node
    allowed = Collection(graph, graph.value(shape_node, sh["in"]))
    assert [str(item).split("#")[-1] for item in allowed] == ["Bay", "VoltageLevel"]
    # and the human-readable twin
    assert (violations["EXPECTED"] == "one of: Bay, VoltageLevel").all()
    # a finding of a shape without sh:path (the closed NodeShape), next to the embedded sh:in list: a reference
    closed = violations.assign(SOURCE_SHAPE="http://iec.ch/TC57/CIM100#BreakerShape", VALUE="s1")
    both = pandas.concat([violations, closed, closed.assign(SOURCE_SHAPE=None)], ignore_index=True)
    both.attrs = violations.attrs
    cim = "http://iec.ch/TC57/CIM100#"
    values = violations_to_report_graph(both, rdf_map={"Equipment.EquipmentContainer": {"type": "Association", "namespace": cim}})
    assert sorted(values.objects(None, sh.value)) == [rdflib.URIRef(cim + "Substation")] + [rdflib.URIRef("urn:uuid:s1")] * 2
    # with the schema, the value of the ( association rdf:type ) path is the class, there and back
    cim = "http://iec.ch/TC57/CIM100#"
    rdf_map = {"Substation": {"type": "Class", "namespace": cim},
               "Equipment.EquipmentContainer": {"type": "Association", "namespace": cim}}
    typed = violations_to_report_graph(violations, rdf_map=rdf_map)
    assert list(typed.objects(None, sh.value)) == [rdflib.URIRef(cim + "Substation")]
    assert list(report_to_violations(typed, rdf_map=rdf_map)["VALUE"]) == ["Substation"]


PATHS_TTL = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
cim:BreakerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path ( cim:Equipment.EquipmentContainer rdf:type ) ; sh:in ( cim:Bay ) ] .
cim:SubstationShape a sh:NodeShape ; sh:targetClass cim:Substation ;
    sh:property [ sh:path [ sh:inversePath cim:Equipment.EquipmentContainer ] ; sh:class cim:Bay ] .
cim:Checks a sh:NodeShape ; sh:targetNode cim:IDchecks ;
    sh:property [ sh:path rdf:type ; sh:sparql [ sh:select \"\"\"SELECT $this ?value WHERE {
        { ?value a <http://iec.ch/TC57/CIM100#Breaker> }
        UNION { ?b <http://iec.ch/TC57/CIM100#IdentifiedObject.name> ?value }
        UNION { ?b a ?value . FILTER (?value = <http://iec.ch/TC57/CIM100#Breaker>) } }\"\"\" ] ] .
"""


@pytest.mark.parametrize("fmt", ["turtle", "xml"])
def test_report_paths_and_sparql_values_round_trip(tmp_path, fmt):
    """sh:resultPath is the shape's own path (a sequence, an inverse), sh:value of a
    sh:sparql result has the kind of the ?value binding (a node, a literal, a class) and
    of an inverse path the node; the serialized report reads back to the frame, the
    blank-node paths resolved in the report itself."""
    import rdflib
    from rdflib.collection import Collection

    shapes = tmp_path / "paths.ttl"
    shapes.write_text(PATHS_TTL)
    data = pandas.DataFrame([("b1", "Type", "Breaker", "i1"), ("b1", "IdentifiedObject.name", "B1", "i1"),
                             ("b1", "Equipment.EquipmentContainer", "s1", "i1"), ("s1", "Type", "Substation", "i1")],
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    violations = triplets.validation.validate_shacl(data, shapes, engine="pandas")
    cim, sh = "http://iec.ch/TC57/CIM100#", rdflib.Namespace("http://www.w3.org/ns/shacl#")
    rdf_map = {"Breaker": {"type": "Class", "namespace": cim}, "Bay": {"type": "Class", "namespace": cim},
               "IdentifiedObject.name": {"type": "Attribute", "namespace": cim},
               "Equipment.EquipmentContainer": {"type": "Association", "namespace": cim}}
    payload = export_to_shacl_report(violations, export_to_memory=True, format=fmt, rdf_map=rdf_map)
    graph = rdflib.Graph().parse(data=payload.getvalue(), format=fmt)

    def path(result):
        node = graph.value(result, sh.resultPath)
        if (node, rdflib.RDF.first, None) in graph:
            return tuple(map(str, Collection(graph, node)))
        inverse = graph.value(node, sh.inversePath)
        return ("^", str(inverse)) if inverse is not None else str(node)

    results = {(path(result), graph.value(result, sh.value))
               for result in graph.subjects(rdflib.RDF.type, sh.ValidationResult)}
    rdf_type = str(rdflib.RDF.type)
    assert results == {((cim + "Equipment.EquipmentContainer", rdf_type), rdflib.URIRef(cim + "Substation")),
                       (("^", cim + "Equipment.EquipmentContainer"), rdflib.URIRef("urn:uuid:b1")),
                       (rdf_type, rdflib.URIRef("urn:uuid:b1")), (rdf_type, rdflib.Literal("B1")),
                       (rdf_type, rdflib.URIRef(cim + "Breaker"))}

    def rows(frame):   # parsing relabels the blank nodes: SOURCE_SHAPE left out
        frame = frame[["ID", "KEY", "VALUE", "VALUE_KIND", "VIOLATION_TYPE", "MESSAGE", "SEVERITY"]].astype(object)
        return set(frame.where(frame.notna(), None).itertuples(index=False, name=None))

    back = report_to_violations(graph, rdf_map=rdf_map)
    assert rows(back) == rows(violations)
    # the paths of a report we wrote resolve in the report, also next to the shapes graph
    assert rows(report_to_violations(graph, shapes_graph=rdflib.Graph().parse(shapes), rdf_map=rdf_map)) == rows(violations)
    assert {(row[2], row[3]) for row in rows(back)} == {
        ("Substation", None), ("b1", None), ("b1", "reference"), ("B1", "literal"), ("Breaker", "class")}

    # pyshacl's report of the same run reads to the same rows (its own messages aside)
    pyshacl = pytest.importorskip("pyshacl")
    nquads = export_to_nquads(data, rdf_map=rdf_map, export_to_memory=True, undefined_namespace=cim)
    dataset = rdflib.Dataset(default_union=True).parse(data=nquads.getvalue(), format="nquads")
    report = pyshacl.validate(dataset, shacl_graph=rdflib.Graph().parse(shapes), advanced=True)[1]
    assert ({row[:5] for row in rows(report_to_violations(report, rdf_map=rdf_map))}
            == {row[:5] for row in rows(violations)})


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb", "pyshacl"])
def test_literal_focus_node_round_trip(tmp_path, engine):
    """The objects of an Attribute KEY as focus nodes (sh:targetObjectsOf) are literals:
    ID_KIND literal, a literal sh:focusNode, read back as the same rows."""
    import rdflib
    pytest.importorskip(engine)
    shapes = tmp_path / "names.ttl"
    shapes.write_text("""
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:NameShape a sh:NodeShape ; sh:targetObjectsOf cim:IdentifiedObject.name ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ] .
""")
    cim = "http://iec.ch/TC57/CIM100#"
    rdf_map = {"Breaker": {"type": "Class", "namespace": cim},
               "IdentifiedObject.name": {"type": "Attribute", "namespace": cim}}
    data = pandas.DataFrame([("b1", "Type", "Breaker", "i1"), ("b1", "IdentifiedObject.name", "B 1", "i1")],
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    violations = triplets.validation.validate_shacl(data, shapes, rdf_map=rdf_map, engine=engine)
    assert list(zip(violations["ID"], violations["ID_KIND"])) == [("B 1", "literal")]
    graph = violations_to_report_graph(violations, rdf_map=rdf_map)
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    assert list(graph.objects(None, sh.focusNode)) == [rdflib.Literal("B 1")]
    back = report_to_violations(graph, rdf_map=rdf_map)
    assert list(zip(back["ID"], back["ID_KIND"])) == [("B 1", "literal")]


GENC4_TTL = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
cim:IDchecksShape a sh:NodeShape ; sh:targetNode cim:IDchecks ;
    sh:property [ sh:path rdf:type ; sh:sparql [ sh:select
        "SELECT $this ?value WHERE { ?value rdf:type <http://iec.ch/TC57/CIM100#Breaker> . }" ;
        sh:prefixes [ sh:declare [ sh:prefix "rdf" ;
            sh:namespace "http://www.w3.org/1999/02/22-rdf-syntax-ns#"^^<http://www.w3.org/2001/XMLSchema#anyURI> ] ] ] ] .
cim:ContainerShape a sh:NodeShape ; sh:targetClass cim:Breaker ;
    sh:property [ sh:path cim:Equipment.EquipmentContainer ; sh:closed true ; sh:ignoredProperties ( rdf:type ) ] .
"""


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb", "pyshacl"])
def test_enriched_report_values_and_paths(tmp_path, engine):
    """Through enrich, on every engine: the ?value node of a sh:sparql check on rdf:type
    is its urn:uuid: IRI; the finding of sh:closed on a property shape names the
    property found as sh:resultPath, not the shape's path."""
    import rdflib
    pytest.importorskip(engine)
    uuid, container = "5a0e1f0e-0000-4000-8000-000000000002", "5a0e1f0e-0000-4000-8000-000000000003"
    shapes = tmp_path / "genc4.ttl"
    shapes.write_text(GENC4_TTL)
    data = pandas.DataFrame([(uuid, "Type", "Breaker", "i1"), (uuid, "Equipment.EquipmentContainer", container, "i1"),
                             (container, "Type", "Substation", "i1"), (container, "IdentifiedObject.name", "S1", "i1")],
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    violations = triplets.validation.validate_shacl(data, shapes, engine=engine)
    graph = violations_to_report_graph(triplets.validation.enrich(violations, data=data))
    sh, cim = rdflib.Namespace("http://www.w3.org/ns/shacl#"), "http://iec.ch/TC57/CIM100#"
    results = {(str(graph.value(result, sh.sourceConstraintComponent)).split("#")[1], graph.value(result, sh.resultPath),
                graph.value(result, sh.value)) for result in graph.subjects(rdflib.RDF.type, sh.ValidationResult)}
    assert results == {("SPARQLConstraintComponent", rdflib.RDF.type, rdflib.URIRef(f"urn:uuid:{uuid}")),
                       ("ClosedConstraintComponent", rdflib.URIRef(cim + "IdentifiedObject.name"), rdflib.Literal("S1"))}


def test_message_source_distinguishes_authored_from_engine(tmp_path):
    """[shacl_message] = the shape's own sh:message verbatim, [engine_message] =
    engine-worded text — stamped by validate_shacl() (MESSAGE_SOURCE)."""
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
    violations = triplets.validation.validate_shacl(DATA, shapes, engine="pandas")
    sources = dict(zip(violations["KEY"], violations["MESSAGE_SOURCE"]))
    assert sources["IdentifiedObject.name"] == "shacl"    # authored sh:message
    assert sources["Breaker.inTransit"] == "engine"       # engine default text

    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    dcterms = rdflib.Namespace("http://purl.org/dc/terms/")
    graph = violations_to_report_graph(violations)
    report = next(graph.subjects(rdflib.RDF.type, sh.ValidationReport))
    assert str(graph.value(report, dcterms.creator)).endswith("(engine: pandas)")
    messages = {str(m) for m in graph.objects(None, sh.resultMessage)}
    assert "[shacl_message] every breaker needs a name" in messages
    assert any(m.startswith("[engine_message] Breaker.inTransit") for m in messages)


def test_metadata_reports_skipped_coverage(tmp_path):
    """Shapes a vectorized run cannot evaluate land in the metadata — the
    report says what was NOT validated, not just what failed."""
    shapes = tmp_path / "partial_shapes.ttl"
    shapes.write_text(SKIPPED_TTL)
    violations = triplets.validation.validate_shacl(DATA, shapes, engine="pandas")
    meta = violations.attrs["validation"]
    assert meta["node_shapes"] == 2
    assert any("sh:target not walked" in entry for entry in meta["skipped_shapes"])
    assert any("unsupported sh:path" in entry for entry in meta["skipped_shapes"])


def test_run_stats_reach_sarif_and_csv(tmp_path):
    """The full validate_shacl() stamp — engine, duration, counts, coverage —
    round-trips into the SARIF run properties and the CSV sidecar; empty
    coverage is stated explicitly, not implied by absence."""
    from triplets.validation import violations_to_csv
    from triplets.validation.sarif import build_sarif

    shapes = tmp_path / "coverage_shapes.ttl"
    shapes.write_text(SKIPPED_TTL)
    violations = triplets.validation.validate_shacl(DATA, shapes, engine="pandas")

    properties = build_sarif(violations)["runs"][0]["properties"]
    assert properties["engine"] == "pandas"
    assert properties["node_shapes"] == 2 and properties["duration_seconds"] >= 0
    assert any("sh:target not walked" in entry for entry in properties["skipped_shapes"])
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


GRID = "http://www.ucaiug.org/grid18v15#"
GRID_SCHEMA = {"EQ": {
    "ProfileMetadata": {"keyword": "EQ"},
    "IdentifiedObject.name": {"type": "Attribute", "namespace": GRID},
    "Conductor.length": {"type": "Attribute", "namespace": GRID},
    "ACLineSegment.r": {"type": "Attribute", "namespace": GRID},
}}


def test_report_paths_follow_the_schema_namespace():
    """A profile outside CIM100 (EMTIOP, grid18v15): rdf_map= gives each sh:resultPath
    its schema namespace; a name the schema lacks takes undefined_namespace;
    the report reads back to the same frame."""
    import rdflib
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    graph = violations_to_report_graph(VIOLATIONS, rdf_map=GRID_SCHEMA)
    assert {str(path) for path in graph.objects(None, sh.resultPath)} == {
        f"{GRID}Conductor.length", f"{GRID}IdentifiedObject.name", f"{GRID}ACLineSegment.r"}
    focus = {str(node) for node in graph.objects(None, sh.focusNode)}
    assert focus == {f"urn:uuid:{ID}" for ID in VIOLATIONS["ID"]}   # same IRIs as the N-Quads / SPARQL views
    pandas.testing.assert_frame_equal(_canon(report_to_violations(graph)), _canon(VIOLATIONS))

    unknown = VIOLATIONS.assign(KEY="Custom.foo")
    graph = violations_to_report_graph(unknown, rdf_map=GRID_SCHEMA, undefined_namespace="http://acme#")
    assert {str(path) for path in graph.objects(None, sh.resultPath)} == {"http://acme#Custom.foo"}
