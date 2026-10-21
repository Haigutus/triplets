"""Tests for the optional context enrichment pass (triplets.validation.context)."""
import pandas
import pytest

pytest.importorskip("rdflib")

import triplets
from triplets.validation.context import ENRICHMENT_COLUMNS, enrich

LINE = "11111111-2222-3333-4444-555555555555"
DIST = "dddddddd-2222-3333-4444-555555555555"

DATA = pandas.DataFrame(
    [
        (LINE, "Type", "ACLineSegment", "g1"),
        (LINE, "IdentifiedObject.name", "Line 1", "g1"),
        (DIST, "Type", "Distribution", "g1"),
        (DIST, "label", "20220615T2230Z_Svedala_EQ.xml", "g1"),
    ],
    columns=["ID", "KEY", "VALUE", "INSTANCE_ID"],
)

SHAPE = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix cim: <http://iec.ch/TC57/CIM100#> .

cim:ACLineSegmentShape a sh:NodeShape ;
    sh:targetClass cim:ACLineSegment ;
    sh:name "ACLineSegment completeness" ;
    sh:description "Every line needs a length." ;
    sh:property [ sh:path cim:Conductor.length ; sh:minCount 1 ] .
"""

RDF_MAP = {"EQ": {
    "ACLineSegment": {"type": "Class",
                      "description": "A wire or combination of wires used to carry current."},
    "Conductor.length": {"type": "Attribute", "description": "Segment length for calculating line section capabilities.",
                         "multiplicity": "1..1", "xsd:type": "xsd:float"},
}}


@pytest.fixture(scope="module")
def shapes():
    """One parsed graph for the whole module — anonymous property shapes get
    fresh BNode ids per parse, so enrichment must see the same shapes object
    the validation ran with."""
    import rdflib
    graph = rdflib.Graph()
    graph.parse(data=SHAPE, format="turtle")
    return triplets.validation.compile(graph)


@pytest.fixture()
def violations(shapes):
    return triplets.validation.validate_shacl(DATA, shapes, engine="pandas")


def test_enrich_all_sources(violations, shapes):
    result = enrich(violations, data=DATA, shapes=shapes, rdf_map=RDF_MAP)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["VIOLATION_TYPE"] == "sh:minCount"
    assert row["INSTANCE_ID"] == "g1"
    assert row["INSTANCE_LABEL"] == "20220615T2230Z_Svedala_EQ.xml"
    assert row["OBJECT_TYPE"] == "ACLineSegment"
    assert row["OBJECT_NAME"] == "Line 1"
    assert row["SHAPE_NAME"] == "ACLineSegment completeness"
    assert row["SHAPE_DESCRIPTION"] == "Every line needs a length."
    assert row["SCHEMA_DESCRIPTION"].startswith("Segment length")
    assert row["SCHEMA_MULTIPLICITY"] == "1..1"
    assert row["CLASS_DESCRIPTION"].startswith("A wire")


def test_enrich_sources_optional(violations):
    result = enrich(violations)
    assert list(result.columns[-len(ENRICHMENT_COLUMNS):]) == ENRICHMENT_COLUMNS
    assert result[ENRICHMENT_COLUMNS].isna().all().all()

    only_data = enrich(violations, data=DATA)
    assert only_data["OBJECT_NAME"].iloc[0] == "Line 1"
    assert only_data["SHAPE_NAME"].isna().all()

    only_schema = enrich(violations, rdf_map=RDF_MAP)
    assert only_schema["SCHEMA_MULTIPLICITY"].iloc[0] == "1..1"
    assert only_schema["CLASS_DESCRIPTION"].isna().all()   # needs OBJECT_TYPE from data


def test_enrich_after_validate(shapes):
    violations = triplets.validation.validate_shacl(DATA, shapes, engine="pandas", rdf_map=RDF_MAP, rdf_map_scope="*")
    assert "TARGET" not in violations.columns                # what was found comes from enrich
    result = enrich(violations, data=DATA, shapes=shapes, rdf_map=RDF_MAP, rdf_map_scope="*")
    assert set(ENRICHMENT_COLUMNS) <= set(result.columns)
    assert result["OBJECT_NAME"].iloc[0] == "Line 1"
    assert result["SCHEMA_DESCRIPTION"].iloc[0].startswith("Segment length")
    assert result["SHAPE_NAME"].iloc[0] == "ACLineSegment completeness"


def test_pyshacl_engine_enrichment_degrades_gracefully(shapes):
    """pyshacl SOURCE_SHAPE joins on the same shape IRIs; anonymous property
    shapes fall back to null shape columns — never an error."""
    pytest.importorskip("pyshacl")
    violations = enrich(triplets.validation.validate_shacl(DATA, shapes, engine="pyshacl"), data=DATA, shapes=shapes)
    assert set(ENRICHMENT_COLUMNS) <= set(violations.columns)
    assert (violations["OBJECT_TYPE"] == "ACLineSegment").any()


def test_flatten_schema_first_wins_and_shapes():
    from triplets.export.nquads_utils import flatten_schema
    duplicated = {"EQ": RDF_MAP["EQ"],
                  "TP": {"Conductor.length": {"type": "Attribute", "description": "other",
                                              "multiplicity": "0..1"}}}
    key_info, class_info = flatten_schema(duplicated)
    assert key_info["Conductor.length"]["multiplicity"] == "1..1"   # first profile wins
    assert class_info["ACLineSegment"].startswith("A wire")


def test_enrich_keeps_messages_verbatim(violations):
    """The raw constraint text is never rewritten — the violated path travels
    as sh:resultPath / SARIF [path], not appended into the message."""
    generic = violations.copy()
    generic["MESSAGE"] = "Missing required property (attribute)."
    assert list(enrich(generic)["MESSAGE"]) == ["Missing required property (attribute)."]


def test_schema_context_is_looked_up_by_profile():
    """A KEY whose multiplicity differs between profiles reports the profiles the
    violation was checked against: the PROFILE column when present, else
    rdf_map_scope (one scope, or one per instance); a merged PROFILE reports the
    relaxed multiplicity that was checked."""
    rdf_map = {
        "SIS": {"ProfileMetadata": {"keyword": "SIS"},
                "BaseTimeSeries.timeSeriesKind": {"type": "Enumeration", "multiplicity": "0..1", "description": "sis",
                                                  "xsd:minOccours": "0", "xsd:maxOccours": "1"}},
        "AS": {"ProfileMetadata": {"keyword": "AS"},
               "BaseTimeSeries.timeSeriesKind": {"type": "Enumeration", "multiplicity": "1..1", "description": "as",
                                                 "xsd:minOccours": "1", "xsd:maxOccours": "1"}},
    }
    violations = pandas.DataFrame({"ID": ["t1", "t2"], "KEY": ["BaseTimeSeries.timeSeriesKind"] * 2,
                                   "VALUE": [None, None], "VIOLATION_TYPE": ["sh:minCount"] * 2,
                                   "MESSAGE": [""] * 2, "SEVERITY": ["Violation"] * 2, "SOURCE_SHAPE": [None] * 2})
    by_column = enrich(violations.assign(PROFILE=[["AS"], ["SIS"]]), rdf_map=rdf_map)
    assert by_column["SCHEMA_MULTIPLICITY"].tolist() == ["1..1", "0..1"]
    data = pandas.DataFrame([("t1", "Type", "BaseTimeSeries", "i1"), ("t2", "Type", "BaseTimeSeries", "i2")],
                            columns=["ID", "KEY", "VALUE", "INSTANCE_ID"])
    by_instance = enrich(violations, data=data, rdf_map=rdf_map, rdf_map_scope={"i1": "AS", "i2": ["SIS"]})
    assert by_instance["SCHEMA_MULTIPLICITY"].tolist() == ["1..1", "0..1"]
    assert by_instance["SCHEMA_DESCRIPTION"].tolist() == ["as", "sis"]
    assert enrich(violations, rdf_map=rdf_map, rdf_map_scope="AS")["SCHEMA_DESCRIPTION"].tolist() == ["as", "as"]
    merged = enrich(violations.assign(PROFILE=[["AS", "SIS"]] * 2), rdf_map=rdf_map)
    assert merged["SCHEMA_MULTIPLICITY"].tolist() == ["0..1", "0..1"]
    fallback = enrich(violations, rdf_map=rdf_map)                    # no profile: first-wins view
    assert fallback["SCHEMA_MULTIPLICITY"].tolist() == ["0..1", "0..1"]


def test_schema_context_reads_the_merged_scope():
    """Profiles merged for a run: a KEY only the second defines comes from that
    profile, not the first-wins view of every section."""
    rdf_map = {"EQ": {"ProfileMetadata": {"keyword": "EQ"}, "A.x": {"type": "Attribute", "multiplicity": "0..1"}},
               "OTHER": {"ProfileMetadata": {"keyword": "OTHER"}, "A.y": {"type": "Attribute", "multiplicity": "0..n"}},
               "SC": {"ProfileMetadata": {"keyword": "SC"}, "A.y": {"type": "Attribute", "multiplicity": "1..1"}}}
    violations = pandas.DataFrame({"ID": ["a1"], "KEY": ["A.y"], "VALUE": [None], "VIOLATION_TYPE": ["sh:minCount"],
                                   "MESSAGE": [""], "SEVERITY": ["Violation"], "SOURCE_SHAPE": [None]})
    assert enrich(violations, rdf_map=rdf_map)["SCHEMA_MULTIPLICITY"].tolist() == ["0..n"]
    assert enrich(violations, rdf_map=rdf_map, rdf_map_scope=["EQ", "SC"])["SCHEMA_MULTIPLICITY"].tolist() == ["1..1"]


def test_concat_violations_merges_run_metadata(shapes):
    """Two runs of the same shapes: rows concatenated, the run window spans both,
    shape counts once, data sources united."""
    from triplets.validation import concat_violations
    other = DATA.assign(INSTANCE_ID="g2", ID=DATA["ID"].str.replace("1111", "9999"))
    first = triplets.validation.validate_shacl(DATA, shapes, engine="pandas")
    second = triplets.validation.validate_shacl(other.assign(VALUE=other["VALUE"].replace(
        "20220615T2230Z_Svedala_EQ.xml", "other.xml")), shapes, engine="pandas")
    both = concat_violations([first, second])
    meta = both.attrs["validation"]
    assert len(both) == 2
    assert meta["started_at"] == first.attrs["validation"]["started_at"]
    assert meta["generated_at"] == second.attrs["validation"]["generated_at"]
    assert meta["node_shapes"] == first.attrs["validation"]["node_shapes"]
    assert meta["source"] == ["20220615T2230Z_Svedala_EQ.xml", "other.xml"]
    assert concat_violations([]).empty
    schema_run = first.copy()
    schema_run.attrs["validation"] = {**first.attrs["validation"], "language": "rdfs"}
    with pytest.raises(ValueError, match="one constraint language"):
        concat_violations([first, schema_run])
    schema_run = first.copy()
    schema_run.attrs["validation"] = {**first.attrs["validation"], "language": "rdfs"}
    with pytest.raises(ValueError, match="one constraint language"):
        concat_violations([first, schema_run])
