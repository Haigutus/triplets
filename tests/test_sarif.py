"""Tests for the SARIF 2.1.0 exporter (triplets.validation.sarif)."""
import io
import json
import zipfile

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
    violations = triplets.validation.validate_shacl(data, shapes, engine="pandas")   # 8 name + 8 length violations
    return triplets.validation.enrich(violations, data=data, shapes=shapes)


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
    violations = triplets.validation.enrich(triplets.validation.validate_shacl(data, shapes, engine="pandas"),
                                            data=data, shapes=shapes)
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


def test_message_blocks_prefixed(violations):
    """One line per origin, each prefixed with where it came from — same tags
    as the sh:ValidationReport resultMessages. [shacl_message] marks text taken
    verbatim from the shape's own sh:message; engine-worded text is [engine_message]."""
    results = build_sarif(violations)["runs"][0]["results"]
    texts = {result["level"]: result["message"]["text"] for result in results}
    assert texts["error"].startswith("[shacl_message] every line needs a name")  # authored, verbatim
    assert texts["warning"].startswith("[engine_message] ")                        # engine default text
    lines = texts["error"].split("\n")
    assert "[shacl_path] IdentifiedObject.name" in lines
    assert any(line == "[context_count] 8 object(s) affected" for line in lines)
    assert any(line.startswith("[context_examples] ") for line in lines)


def test_rules_metadata(violations):
    rules = build_sarif(violations)["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) == 2
    for rule in rules:
        assert rule["id"].endswith("/sh:minCount")
        assert rule["shortDescription"]["text"] == "SHACL Line completeness (8×)"  # sh:name + grouped count
        assert rule["fullDescription"]["text"] == "Lines carry a name and a length."
        assert rule["properties"]["constraint"] == "sh:minCount"


def test_rule_help(violations):
    """Every rule carries help (text + markdown): the authored sh:message, path,
    expected value and a link to the constraint's SHACL spec section; no schema
    context without the rdf_map, no Shape line for an anonymous property shape."""
    rules = build_sarif(violations)["runs"][0]["tool"]["driver"]["rules"]
    helps = {rule["defaultConfiguration"]["level"]: rule["help"] for rule in rules}
    assert all(set(rule_help) == {"text", "markdown"} for rule_help in helps.values())
    markdown = helps["error"]["markdown"]
    for line in ("- every line needs a name", "- Path: `IdentifiedObject.name`",
                 "- Expected: at least 1 value(s)",
                 "- Constraint: [sh:minCount](https://www.w3.org/TR/shacl/#MinCountConstraintComponent)"):
        assert line in markdown
    assert "Constraint: sh:minCount — https://www.w3.org/TR/shacl/#MinCountConstraintComponent" \
        in helps["error"]["text"]
    assert "fewer than" not in helps["warning"]["text"]           # engine wording is per result
    assert all("Schema context" not in rule_help["text"] and "Shape:" not in rule_help["text"]
               for rule_help in helps.values())


def test_rule_help_source_and_tool_link():
    """Bare frame: an authored multi-line message stays one list item; an http
    shape IRI and the single shapes file name the source; triplets:* findings
    link the triplets docs."""
    violations = pandas.DataFrame(
        [[None, None, None, "triplets:invalidSparql", "first line\n  second line", "Warning",
          "http://iec.ch/TC57/CIM100#NameShape", "shacl"]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE",
                 "MESSAGE_SOURCE"])
    violations.attrs["validation"] = {"references": ["shapes.ttl"]}
    markdown = build_sarif(violations)["runs"][0]["tool"]["driver"]["rules"][0]["help"]["markdown"]
    assert markdown.split("\n") == [
        "### What this checks", "- first line second line", "",
        "### Source", "- Shape: http://iec.ch/TC57/CIM100#NameShape", "- File: shapes.ttl",
        "- Constraint: [triplets:invalidSparql](https://github.com/Haigutus/triplets/blob/main/docs/validation.md)"]


def test_unenriched_frame_and_null_id():
    violations = pandas.DataFrame(
        [[None, "IdentifiedObject.name", None, "triplets:invalidSparql",
          "oxigraph rejected the query", "Warning", "urn:shape"]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    result = build_sarif(violations)["runs"][0]["results"][0]
    assert result["level"] == "warning"
    assert "locations" not in result                     # no ID → no locations
    assert result["message"]["text"].startswith("[engine_message] oxigraph rejected")


def test_rule_title_fallback_for_unnamed_shapes():
    """Unnamed SHACL shapes get "SHACL <attr> <violation type>" as the alert title
    instead of GitHub dumping the raw message text."""
    violations = pandas.DataFrame(
        [["id1", "EnergyConsumer.q", "1", "triplets:lexicalForm", "narrower form",
          "Warning", "n0f320cdb3"]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    rule = build_sarif(violations)["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["name"] == "SHACL EnergyConsumer.q triplets:lexicalForm (1×)"


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
    violations = triplets.validation.validate_shacl(data, shapes, engine="pandas")   # no context
    document = build_sarif(violations)
    assert "logicalLocations" in json.dumps(document)    # works without enrichment too

    buffer = export_to_sarif(violations, data=data, shapes=shapes, export_to_memory=True)
    document = json.loads(buffer.read().decode("utf-8"))
    rule = document["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["shortDescription"]["text"] == "SHACL Line completeness (2×)"
    uri = document["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "Svedala%20EQ.xml"


def test_accessor(violations):
    buffer = violations.shacl.to_sarif(export_to_memory=True)
    assert json.loads(buffer.read().decode("utf-8"))["version"] == "2.1.0"
    enriched = violations.shacl.enrich()
    assert "OBJECT_NAME" in enriched.columns


def test_null_id_rows_keep_stamped_locations():
    """Shape-level rows (null ID) with externally stamped source columns get a
    physicalLocation — GitHub rejects location-less results."""
    violations = pandas.DataFrame(
        [[None, "IdentifiedObject.name", None, "triplets:invalidSparql",
          "query rejected", "Warning", "urn:shape"]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    violations["SOURCE_URI"] = "shapes/equipment.ttl"
    violations["SOURCE_LINE"] = 42
    location = build_sarif(violations)["runs"][0]["results"][0]["locations"][0]
    assert "logicalLocations" not in location                  # no focus object
    assert location["physicalLocation"]["artifactLocation"]["uri"] == "shapes/equipment.ttl"
    assert location["physicalLocation"]["region"]["startLine"] == 42


def test_tool_findings_fall_back_to_shapes_artifact():
    """A bare tool finding (no ID, no stamps) points at the shapes file from
    the run metadata — whole-file region, so GitHub can display it."""
    violations = pandas.DataFrame(
        [[None, None, None, "triplets:invalidSparql", "m", "Violation", "s"]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    violations.attrs["validation"] = {"source": ["grid.xml"], "references": ["shapes.ttl"]}
    result = build_sarif(violations)["runs"][0]["results"][0]
    physical = result["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "shapes.ttl"   # not the data file
    assert physical["region"] == {"startLine": 1, "endLine": 1}


def test_official_schema_conformance(violations):
    """Every output shape validates against the official OASIS SARIF 2.1.0
    JSON schema (vendored in tests/data)."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parent / "data" / "sarif-schema-2.1.0.json")
                        .read_text(encoding="utf-8"))
    document = build_sarif(violations)
    assert all("help" in rule for rule in document["runs"][0]["tool"]["driver"]["rules"])
    jsonschema.validate(document, schema)                              # grouped, enriched
    jsonschema.validate(build_sarif(violations, group=False), schema)  # per-violation

    minimal = pandas.DataFrame(                                        # unenriched, null ID
        [[None, "IdentifiedObject.name", None, "triplets:invalidSparql",
          "engine rejected the query", "Warning", None]],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    jsonschema.validate(build_sarif(minimal), schema)

    minimal.attrs["validation"] = {"language": "rdfs", "skipped_shapes": ["X: datatype conflict"],
                                   "skipped_components": ["sh:qualifiedValueShape"]}
    document = build_sarif(minimal)                                    # notifications, no timestamps
    assert document["runs"][0]["invocations"][0]["toolConfigurationNotifications"]
    assert document["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]
    jsonschema.validate(document, schema)


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
    assert all(rule["help"]["text"].strip() for rule in run["tool"]["driver"]["rules"])
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
    violations = triplets.validation.enrich(triplets.validation.validate_shacl(data, shapes, engine="pandas"),
                                            data=data, shapes=shapes)

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
    violations = triplets.validation.enrich(triplets.validation.validate_shacl(data, graph, engine="pandas"),
                                            data=data, shapes=graph)

    run = build_sarif(violations, sources=[str(xml)])["runs"][0]
    region = run["results"][0]["locations"][0]["physicalLocation"]["region"]
    # whole-line region, explicitly bounded — start-only regions break GitHub;
    # the located line's text rides along as the SARIF-native snippet
    assert region == {"startLine": 5, "endLine": 5,
                      "snippet": {"text": "<cim:IdentifiedObject.name>L1</cim:IdentifiedObject.name>"}}


def test_regions_schema_and_github_shape(shapes, tmp_path):
    """Documents with regions still validate against the official schema."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parent / "data" / "sarif-schema-2.1.0.json")
                        .read_text(encoding="utf-8"))
    xml = tmp_path / "grid.xml"
    xml.write_text(XML, encoding="utf-8")
    data = pandas.read_RDF([str(xml)])
    violations = triplets.validation.enrich(triplets.validation.validate_shacl(data, shapes, engine="pandas"),
                                            data=data, shapes=shapes)
    jsonschema.validate(build_sarif(violations, sources=[str(xml)]), schema)
    jsonschema.validate(build_sarif(violations, group=False, sources=[str(xml)]), schema)


def test_sources_absent_ids_fall_back(shapes):
    """IDs not found in the sources keep the enrichment-label location."""
    data = frame(count=2)
    violations = triplets.validation.enrich(triplets.validation.validate_shacl(data, shapes, engine="pandas"),
                                            data=data, shapes=shapes)
    run = build_sarif(violations, sources=[])["runs"][0]           # nothing locatable
    location = run["results"][0]["locations"][0]
    assert location["physicalLocation"]["artifactLocation"]["uri"] == "Svedala%20EQ.xml"
    # whole-file convention — GitHub needs region.startLine to display at all
    assert location["physicalLocation"]["region"] == {"startLine": 1, "endLine": 1}


def bare(profiles, language=None, constraint="sh:minCount", **meta):
    """One-rule bare frame, one row per PROFILE cell."""
    violations = pandas.DataFrame(
        [["id1", "IdentifiedObject.name", None, constraint, "m", "Violation", "urn:shape", profile]
         for profile in profiles],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE", "PROFILE"])
    if language or meta:
        violations.attrs["validation"] = {**({"language": language} if language else {}), **meta}
    return violations


def test_rule_tags():
    """Tags: language, constraint, each profile of the rule (all rows, first
    seen) — distinct, at most 10 (what GitHub keeps)."""
    tags = lambda violations: build_sarif(violations)["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]
    assert tags(bare([None])) == ["shacl", "sh:minCount"]                      # bare frame: shacl
    assert tags(bare([["EQ", "OP"], ["OP", "SC"], "SSH", None], language="rdfs",
                     constraint="xsd:minOccurs")) == ["rdfs", "xsd:minOccurs", "EQ", "OP", "SC", "SSH"]
    many = tags(bare([[f"P{index}" for index in range(12)]], language="rdfs"))
    assert many == ["rdfs", "sh:minCount", *(f"P{index}" for index in range(8))]
    assert tags(bare([["rdfs", "EQ"]], language="rdfs")) == ["rdfs", "sh:minCount", "EQ"]   # no duplicates


def test_tool_name_per_language():
    name = lambda violations: build_sarif(violations)["runs"][0]["tool"]["driver"]["name"]
    assert name(bare([None])) == "triplets-shacl"                              # no metadata
    assert name(bare([None], language="shacl")) == "triplets-shacl"
    assert name(bare([None], language="rdfs")) == "triplets-rdfs"


def test_skipped_entries_as_configuration_notifications():
    """One warning notification per skipped shape / component on
    invocations[0]; the run property bag keeps the lists; no timestamps still
    gives a valid invocation (executionSuccessful)."""
    meta = {"skipped_shapes": ["A: datatype conflict", "profile XX: no schema"],
            "skipped_components": ["sh:qualifiedValueShape"]}
    run = build_sarif(bare([None], language="rdfs", **meta))["runs"][0]
    assert run["invocations"] == [{"executionSuccessful": True, "toolConfigurationNotifications": [
        {"descriptor": {"id": "triplets/skippedShape"}, "level": "warning",
         "message": {"text": "A: datatype conflict"}},
        {"descriptor": {"id": "triplets/skippedShape"}, "level": "warning",
         "message": {"text": "profile XX: no schema"}},
        {"descriptor": {"id": "triplets/skippedComponent"}, "level": "warning",
         "message": {"text": "sh:qualifiedValueShape"}}]}]
    assert run["properties"] == {"language": "rdfs", **meta}

    timed = build_sarif(bare([None], generated_at="2026-01-01T00:00:00+00:00",
                             skipped_shapes=["A"], skipped_components=[]))["runs"][0]["invocations"][0]
    assert list(timed) == ["executionSuccessful", "endTimeUtc", "toolConfigurationNotifications"]
    assert len(timed["toolConfigurationNotifications"]) == 1
    assert "invocations" not in build_sarif(bare([None], skipped_shapes=[], skipped_components=[]))["runs"][0]


def located(tmp_path):
    """Violations of objects a (repo/sub/grid EQ.xml, under the root), c (other.xml,
    outside it), d (zip member inner/grid.xml of repo/grid.zip) and e (a file-like)."""
    (tmp_path / "repo" / "sub").mkdir(parents=True)
    inside = tmp_path / "repo" / "sub" / "grid EQ.xml"
    inside.write_text(XML, encoding="utf-8")
    outside = tmp_path / "other.xml"
    outside.write_text(XML.replace("_aaaaaaaa", "_cccccccc"), encoding="utf-8")
    archive = tmp_path / "repo" / "grid.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("inner/grid.xml", XML.replace("_aaaaaaaa", "_dddddddd"))
    memory = io.BytesIO(XML.replace("_aaaaaaaa", "_eeeeeeee").encode("utf-8"))
    memory.name = "memory.xml"
    violations = pandas.DataFrame(
        [[f"{letter * 8}-2222-3333-4444-555555555555", "IdentifiedObject.name", None, "sh:minCount", "m",
          "Violation", f"urn:shape{letter}"] for letter in "acde"],
        columns=["ID", "KEY", "VALUE", "VIOLATION_TYPE", "MESSAGE", "SEVERITY", "SOURCE_SHAPE"])
    return violations, [str(inside), str(outside), str(archive), memory]


def test_source_root_relative_locations(tmp_path):
    """source_root: a source under it is written relative (forward slashes) —
    no absolute root in the log; a path outside it, a zip member and a
    file-like keep their names."""
    expected = {"a": "sub/grid%20EQ.xml", "c": quote_path(str(tmp_path / "other.xml")),
                "d": "inner/grid.xml", "e": "memory.xml"}
    violations, sources = located(tmp_path)
    document = build_sarif(violations, sources=sources, source_root=tmp_path / "repo")
    assert {result["properties"]["sampleIDs"][0][0]:
            result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            for result in document["runs"][0]["results"]} == expected
    assert str(tmp_path / "repo") not in json.dumps(document)

    violations, sources = located(tmp_path / "rows")
    rows = build_sarif(violations, group=False, sources=sources, source_root=tmp_path / "rows" / "repo")
    assert {result["properties"]["id"][0]: result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            for result in rows["runs"][0]["results"]} \
        == {**expected, "c": quote_path(str(tmp_path / "rows" / "other.xml"))}

    violations, sources = located(tmp_path / "default")
    without = build_sarif(violations, sources=sources)["runs"][0]           # default: as given
    assert without["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"] \
        == {"uri": quote_path(sources[0])}

    violations, sources = located(tmp_path / "accessor")
    via_accessor = json.loads(violations.shacl.to_sarif(sources=sources, source_root=tmp_path / "accessor" / "repo",
                                                        export_to_memory=True).read())
    assert via_accessor["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"] \
        == {"uri": "sub/grid%20EQ.xml"}


def test_source_root_applies_to_the_locate_pass_only(tmp_path):
    """A frame already carrying the location columns is used as-is."""
    violations, sources = located(tmp_path)
    stamped = triplets.validation.locate_violations(violations, sources)
    violations, sources = located(tmp_path / "again")                      # fresh file-like
    run = build_sarif(stamped, sources=sources, source_root=tmp_path / "repo")["runs"][0]
    assert run["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"] \
        == {"uri": quote_path(str(tmp_path / "repo" / "sub" / "grid EQ.xml"))}


def quote_path(path):
    from urllib.parse import quote
    return quote(path, safe="/")
