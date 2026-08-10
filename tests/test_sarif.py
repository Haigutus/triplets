"""Tests for the SARIF 2.1.0 exporter (triplets.validation.sarif)."""
import io
import json

from pathlib import Path

import pandas
import pytest

pytest.importorskip("rdflib")

import triplets
from triplets.validation.sarif import build_sarif, export_to_sarif

SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:NameShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:name "Line completeness" ;
    sh:description "Lines carry a name and a length." ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:minCount 1 ;
                  sh:message "every line needs a name" ] ;
    sh:property [ sh:path cim:Conductor.length ; sh:minCount 1 ; sh:severity sh:Warning ] .
"""


def frame(count=8):
    """count nameless, lengthless lines + the instance meta rows."""
    rows = [(f"{i:08d}-2222-3333-4444-555555555555", "Type", "ACLineSegment", "g1")
            for i in range(count)]
    rows += [("dddddddd-2222-3333-4444-555555555555", "Type", "Distribution", "g1"),
             ("dddddddd-2222-3333-4444-555555555555", "label", "Svedala EQ.xml", "g1")]
    return pandas.DataFrame(rows, columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])


@pytest.fixture(scope="module")
def shapes():
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=SHAPE, format="turtle")
    return triplets.validation.compile(graph)


@pytest.fixture(scope="module")
def violations(shapes):
    data = frame()
    return triplets.validation.validate(data, shapes, engine="pandas",
                                        context=True)   # 8 name + 8 length violations


def test_document_skeleton(violations):
    document = build_sarif(violations)
    assert document["version"] == "2.1.0"
    assert document["$schema"].endswith("sarif-schema-2.1.0.json")
    run = document["runs"][0]
    assert run["tool"]["driver"]["name"] == "triplets-shacl"
    for result in run["results"]:
        assert run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"] == result["ruleId"]


def test_grouped_default(violations):
    run = build_sarif(violations)["runs"][0]
    assert len(run["results"]) == 2                      # one per rule, not 16
    by_level = {result["level"]: result for result in run["results"]}
    error = by_level["error"]                            # name shape: default severity
    assert error["occurrenceCount"] == 8
    assert "8 object(s) affected" in error["message"]["text"]
    assert "every line needs a name" in error["message"]["text"]
    assert "…" in error["message"]["text"]               # first 3 … last 3
    assert len(error["locations"]) == 6
    assert by_level["warning"]["occurrenceCount"] == 8   # sh:severity sh:Warning

    logical = error["locations"][0]["logicalLocations"][0]
    assert logical["fullyQualifiedName"].startswith("ACLineSegment/")
    assert error["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "Svedala%20EQ.xml"
    assert error["properties"]["count"] == 8
    assert len(error["properties"]["sampleIDs"]) == 6


def test_small_group_lists_all(shapes):
    data = frame(count=2)
    violations = triplets.validation.validate(data, shapes, engine="pandas", context=True)
    result = build_sarif(violations)["runs"][0]["results"][0]
    assert result["occurrenceCount"] == 2
    assert len(result["locations"]) == 2
    assert "…" not in result["message"]["text"]


def test_ungrouped(violations):
    run = build_sarif(violations, group=False)["runs"][0]
    assert len(run["results"]) == 16                     # one per violation row
    result = run["results"][0]
    assert "occurrenceCount" not in result
    assert len(result["locations"]) == 1
    assert result["properties"]["objectType"] == "ACLineSegment"
    assert "value" not in result["properties"]           # nulls dropped


def test_rules_metadata(violations):
    rules = build_sarif(violations)["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) == 2
    for rule in rules:
        assert rule["id"].endswith("/sh:minCount")
        assert rule["shortDescription"]["text"] == "Line completeness"      # inherited sh:name
        assert rule["fullDescription"]["text"] == "Lines carry a name and a length."
        assert rule["properties"]["constraint"] == "sh:minCount"


def test_unenriched_frame_and_null_id():
    violations = pandas.DataFrame(
        [[None, "IdentifiedObject.name", None, "triplets:invalidSparql",
          "oxigraph rejected the query", "Warning", "urn:shape"]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    result = build_sarif(violations)["runs"][0]["results"][0]
    assert result["level"] == "warning"
    assert "locations" not in result                     # no ID → no locations
    assert result["message"]["text"].startswith("oxigraph rejected")


def test_message_fallback_generated():
    violations = pandas.DataFrame(
        [["id1", "Conductor.length", "-4", "sh:minInclusive", None, "Violation", None]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    document = build_sarif(violations)
    result = document["runs"][0]["results"][0]
    assert "Conductor.length: sh:minInclusive constraint violated" in result["message"]["text"]
    assert document["runs"][0]["tool"]["driver"]["rules"][0]["id"] == "sh:minInclusive"


def test_export_paths_and_memory(violations, tmp_path):
    target = tmp_path / "out.sarif"
    export_to_sarif(violations, path=target)
    on_disk = json.loads(target.read_text(encoding="utf-8"))

    buffer = export_to_sarif(violations, export_to_memory=True)
    assert isinstance(buffer, io.BytesIO) and buffer.name == "report.sarif"
    assert json.loads(buffer.read().decode("utf-8")) == on_disk

    as_str = export_to_sarif(violations, path=str(tmp_path / "out2.sarif"))
    assert Path(as_str).exists()


def test_exporter_runs_enrichment(shapes):
    """Passing the sources to export_to_sarif enriches internally."""
    data = frame(count=2)
    violations = triplets.validation.validate(data, shapes, engine="pandas")   # no context
    document = build_sarif(violations)
    assert "logicalLocations" in json.dumps(document)    # works without enrichment too

    buffer = export_to_sarif(violations, data=data, shapes=shapes, export_to_memory=True)
    document = json.loads(buffer.read().decode("utf-8"))
    rule = document["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["shortDescription"]["text"] == "Line completeness"
    uri = document["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "Svedala%20EQ.xml"


def test_accessor(violations):
    buffer = violations.shacl.to_sarif(export_to_memory=True)
    assert json.loads(buffer.read().decode("utf-8"))["version"] == "2.1.0"
    enriched = violations.shacl.enrich()
    assert "OBJECT_NAME" in enriched.columns


def test_official_schema_conformance(violations):
    """Every output shape validates against the official OASIS SARIF 2.1.0
    JSON schema (vendored in tests/data)."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parent / "data" / "sarif-schema-2.1.0.json")
                        .read_text(encoding="utf-8"))
    jsonschema.validate(build_sarif(violations), schema)               # grouped, enriched
    jsonschema.validate(build_sarif(violations, group=False), schema)  # per-violation

    minimal = pandas.DataFrame(                                        # unenriched, null ID
        [[None, "IdentifiedObject.name", None, "triplets:invalidSparql",
          "engine rejected the query", "Warning", None]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    jsonschema.validate(build_sarif(minimal), schema)


def test_github_code_scanning_requirements(violations):
    """GitHub code-scanning ingestion rules (docs.github.com, 'SARIF support
    for code scanning'): driver.name, a non-empty message.text per result,
    the level vocabulary, unique rule ids resolvable via ruleIndex, and
    repo-relative artifact URIs (absolute paths/URIs cannot be annotated).
    The grouped default also keeps results far under the 25,000-per-run cap."""
    run = build_sarif(violations)["runs"][0]
    assert run["tool"]["driver"]["name"]
    rule_ids = [rule["id"] for rule in run["tool"]["driver"]["rules"]]
    assert len(rule_ids) == len(set(rule_ids))
    assert len(run["results"]) <= 25_000
    for result in run["results"]:
        assert result["message"]["text"].strip()
        assert result["level"] in {"none", "note", "warning", "error"}
        assert rule_ids[result["ruleIndex"]] == result["ruleId"]
        for location in result.get("locations", []):
            uri = location["physicalLocation"]["artifactLocation"]["uri"]
            assert "://" not in uri and not uri.startswith("/")


XML = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:cim="http://iec.ch/TC57/CIM100#">
  <cim:ACLineSegment rdf:ID="_aaaaaaaa-2222-3333-4444-555555555555">
    <cim:IdentifiedObject.name>L1</cim:IdentifiedObject.name>
  </cim:ACLineSegment>
  <cim:ACLineSegment rdf:ID="_bbbbbbbb-2222-3333-4444-555555555555">
    <cim:Conductor.length>7</cim:Conductor.length>
  </cim:ACLineSegment>
</rdf:RDF>
"""
# object definitions on lines 4 and 7; L1's name property on line 5


def test_regions_from_sources(shapes, tmp_path):
    """sources= locates the violating objects in the XML text: the region
    points at the violated property's line, or the object definition."""
    xml = tmp_path / "grid EQ.xml"
    xml.write_text(XML, encoding="utf-8")
    data = pandas.read_RDF([str(xml)])
    violations = triplets.validation.validate(data, shapes, engine="pandas", context=True)

    run = build_sarif(violations, group=False, sources=[str(xml)])["runs"][0]
    regions = {(result["properties"]["id"], result["properties"]["key"]):
               result["locations"][0]["physicalLocation"] for result in run["results"]}

    # 'a' misses Conductor.length (not in its element) → object definition line
    a_length = regions[("aaaaaaaa-2222-3333-4444-555555555555", "Conductor.length")]
    assert a_length["region"]["startLine"] == 4
    # 'b' misses IdentifiedObject.name → its object definition line
    b_name = regions[("bbbbbbbb-2222-3333-4444-555555555555", "IdentifiedObject.name")]
    assert b_name["region"]["startLine"] == 7
    assert b_name["artifactLocation"]["uri"].endswith("grid%20EQ.xml")


def test_region_points_at_property_line(tmp_path):
    """A value violation lands on the property element's own line."""
    import rdflib
    shape = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .
cim:ShortName a sh:NodeShape ; sh:targetClass cim:ACLineSegment ;
    sh:property [ sh:path cim:IdentifiedObject.name ; sh:maxLength 1 ] .
"""
    graph = rdflib.Graph(); graph.parse(data=shape, format="turtle")
    xml = tmp_path / "grid.xml"
    xml.write_text(XML, encoding="utf-8")
    data = pandas.read_RDF([str(xml)])
    violations = triplets.validation.validate(data, graph, engine="pandas", context=True)

    run = build_sarif(violations, sources=[str(xml)])["runs"][0]
    region = run["results"][0]["locations"][0]["physicalLocation"]["region"]
    # fully bounded single-line region — GitHub cannot display start-only regions
    assert region == {"startLine": 5, "endLine": 5,
                      "startColumn": 5,                  # the '<' of the property element
                      "endColumn": len(XML.splitlines()[4]) + 1}


def test_regions_schema_and_github_shape(shapes, tmp_path):
    """Documents with regions still validate against the official schema."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parent / "data" / "sarif-schema-2.1.0.json")
                        .read_text(encoding="utf-8"))
    xml = tmp_path / "grid.xml"
    xml.write_text(XML, encoding="utf-8")
    data = pandas.read_RDF([str(xml)])
    violations = triplets.validation.validate(data, shapes, engine="pandas", context=True)
    jsonschema.validate(build_sarif(violations, sources=[str(xml)]), schema)
    jsonschema.validate(build_sarif(violations, group=False, sources=[str(xml)]), schema)


def test_sources_absent_ids_fall_back(shapes):
    """IDs not found in the sources keep the enrichment-label location."""
    violations = triplets.validation.validate(frame(count=2), shapes,
                                              engine="pandas", context=True)
    run = build_sarif(violations, sources=[])["runs"][0]           # nothing locatable
    location = run["results"][0]["locations"][0]
    assert "region" not in location.get("physicalLocation", {})
    assert location["physicalLocation"]["artifactLocation"]["uri"] == "Svedala%20EQ.xml"
